"""简历上传、查询与面试注入编排服务。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from fastapi import UploadFile
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage_client import StorageClient, storage_manager
from app.models.resume import ResumeDetailDTO, ResumeEntity, ResumeStatus, ResumeSummaryDTO, ResumeUploadResponse
from app.repositories.resume_repository import ResumeRepository
from app.services.resume_parse_service import ParsedResumeContent, ResumeParseService, resume_parse_service
from app.services.resume_persistence_service import ResumePersistenceService
from app.utils.exceptions import BusinessException, ErrorCode

ALLOWED_RESUME_FILE_EXTENSIONS = {".pdf", ".md"}
MAX_RESUME_FILE_SIZE_BYTES = 10 * 1024 * 1024


@dataclass(slots=True)
class ResumeContextBundle:
    """面试阶段需要的简历上下文。"""

    resume_id: str
    markdown_content: str
    metadata: dict[str, Any]


class ResumeService:
    """负责简历模块的同步闭环编排。"""

    def __init__(
        self,
        *,
        persistence_service: ResumePersistenceService | None = None,
        parse_service: ResumeParseService | None = None,
        storage_client: StorageClient | None = None,
        repository_factory: type[ResumeRepository] | None = None,
    ) -> None:
        self._persistence_service = persistence_service or ResumePersistenceService()
        self._parse_service = parse_service or resume_parse_service
        self._storage_client = storage_client
        self._repository_factory = repository_factory or ResumeRepository

    async def upload_resume(
        self,
        session: AsyncSession,
        *,
        file: UploadFile,
        visitor_id: str,
    ) -> ResumeUploadResponse:
        """上传并同步解析简历。"""

        if not file.filename:
            raise BusinessException(
                code=ErrorCode.BAD_REQUEST,
                message="简历文件名不能为空",
            )

        safe_file_name = self._sanitize_file_name(file.filename)
        file_extension = self._get_file_extension(safe_file_name)
        if file_extension not in ALLOWED_RESUME_FILE_EXTENSIONS:
            raise BusinessException(
                code=ErrorCode.RESUME_FILE_TYPE_NOT_SUPPORTED,
                message="简历文件仅支持 PDF 或 Markdown",
                details={"file_extension": file_extension},
            )

        file_content = await file.read()
        if not file_content:
            raise BusinessException(
                code=ErrorCode.BAD_REQUEST,
                message="上传文件不能为空",
            )
        if len(file_content) > MAX_RESUME_FILE_SIZE_BYTES:
            raise BusinessException(
                code=ErrorCode.RESUME_FILE_TOO_LARGE,
                message="简历文件大小超过限制",
                details={
                    "file_size": len(file_content),
                    "max_size": MAX_RESUME_FILE_SIZE_BYTES,
                },
            )

        parsed_content = self._parse_service.parse_bytes(
            file_name=safe_file_name,
            file_content=file_content,
            file_extension=file_extension,
        )

        existing_resume = await self._persistence_service.get_resume_by_visitor_and_content_hash(
            session,
            visitor_id,
            parsed_content.content_hash,
        )
        if existing_resume is not None:
            logger.info(
                "reuse existing resume, visitor_id={}, resume_id={}",
                visitor_id,
                existing_resume.id,
            )
            return ResumeUploadResponse(
                resume=self._to_detail_dto(existing_resume),
                reused_existing=True,
            )

        resume_id = self._generate_identifier()
        object_key = self._build_storage_object_key(
            visitor_id=visitor_id,
            resume_id=resume_id,
            file_name=safe_file_name,
        )

        storage_client = self._resolve_storage_client()
        try:
            storage_path = await storage_client.upload_file(object_key, file_content)
        except Exception as exc:
            raise BusinessException(
                code=ErrorCode.STORAGE_UPLOAD_FAILED,
                message="简历原始文件上传失败",
                http_status=500,
                details={"error": str(exc), "file_name": safe_file_name},
            ) from exc

        resume = ResumeEntity(
            id=resume_id,
            visitor_id=visitor_id,
            original_file_name=safe_file_name,
            storage_path=storage_path,
            file_extension=file_extension,
            mime_type=file.content_type or self._guess_mime_type(file_extension),
            content_hash=parsed_content.content_hash,
            file_size=len(file_content),
            markdown_content=parsed_content.markdown_content,
            status=ResumeStatus.COMPLETED.value,
            source_metadata_json=self._build_source_metadata(
                parsed_content=parsed_content,
                file_name=safe_file_name,
                object_key=object_key,
            ),
        )

        try:
            saved_resume = await self._persistence_service.save_resume(session, resume)
            await session.commit()
        except BusinessException:
            await session.rollback()
            raise
        except Exception as exc:
            await session.rollback()
            logger.exception("简历持久化失败: visitor_id={}, error={}", visitor_id, exc)
            raise BusinessException(
                code=ErrorCode.RESUME_PERSIST_FAILED,
                message="简历保存失败",
                http_status=500,
                details={"error": str(exc), "file_name": safe_file_name},
            ) from exc

        logger.info(
            "resume uploaded, visitor_id={}, resume_id={}, file_name={}",
            visitor_id,
            saved_resume.id,
            safe_file_name,
        )
        return ResumeUploadResponse(
            resume=self._to_detail_dto(saved_resume),
            reused_existing=False,
        )

    async def list_resumes(
        self,
        session: AsyncSession,
        *,
        visitor_id: str,
        limit: int = 20,
    ) -> list[ResumeSummaryDTO]:
        """查询访客的历史简历。"""

        resumes = await self._persistence_service.list_resumes_by_visitor(
            session,
            visitor_id,
            limit=limit,
        )
        return [self._to_summary_dto(resume) for resume in resumes]

    async def get_resume(
        self,
        session: AsyncSession,
        *,
        resume_id: str,
        visitor_id: str,
    ) -> ResumeDetailDTO:
        """查询单份简历详情。"""

        resume = await self._persistence_service.get_resume_by_visitor(
            session,
            resume_id,
            visitor_id,
        )
        if resume is None:
            raise BusinessException(
                code=ErrorCode.RESUME_NOT_FOUND,
                message="简历不存在",
                http_status=404,
                details={"resume_id": resume_id},
            )
        return self._to_detail_dto(resume)

    async def resolve_resume_for_interview(
        self,
        session: AsyncSession,
        *,
        visitor_id: str,
        resume_id: str | None,
    ) -> ResumeContextBundle | None:
        """仅在显式选择简历时，为面试解析对应简历上下文。"""

        if not resume_id:
            return None

        resume = await self._persistence_service.get_resume_by_visitor(
            session,
            resume_id,
            visitor_id,
        )
        if resume is None:
            raise BusinessException(
                code=ErrorCode.RESUME_NOT_FOUND,
                message="简历不存在",
                http_status=404,
                details={"resume_id": resume_id},
            )

        return ResumeContextBundle(
            resume_id=resume.id,
            markdown_content=resume.markdown_content,
            metadata={
                "original_file_name": resume.original_file_name,
                "file_extension": resume.file_extension,
                "file_size": resume.file_size,
                **dict(resume.source_metadata_json or {}),
            },
        )

    def _resolve_storage_client(self) -> StorageClient:
        """解析当前存储客户端。"""

        return self._storage_client or storage_manager.get_client()

    def _sanitize_file_name(self, file_name: str) -> str:
        """清洗客户端文件名。"""

        sanitized = file_name.replace(" ", "_")
        for char in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
            sanitized = sanitized.replace(char, "_")
        return sanitized.strip("._") or "resume"

    def _get_file_extension(self, file_name: str) -> str:
        """获取小写扩展名。"""

        suffix = PurePosixPath(file_name).suffix.lower()
        return suffix

    def _build_storage_object_key(
        self,
        *,
        visitor_id: str,
        resume_id: str,
        file_name: str,
    ) -> str:
        """构建原始简历文件的存储路径。"""

        return f"resumes/{visitor_id}/{resume_id}/{file_name}"

    def _build_source_metadata(
        self,
        *,
        parsed_content: ParsedResumeContent,
        file_name: str,
        object_key: str,
    ) -> dict[str, Any]:
        """构建来源与解析元数据。"""

        return {
            "object_key": object_key,
            "normalized_content_hash": parsed_content.content_hash,
            "parser": "pymupdf4llm" if file_name.lower().endswith(".pdf") else "markdown-native",
            "markdown_length": len(parsed_content.markdown_content),
        }

    def _guess_mime_type(self, file_extension: str) -> str:
        """为已知简历格式提供默认 MIME。"""

        if file_extension == ".pdf":
            return "application/pdf"
        return "text/markdown"

    def _to_summary_dto(self, resume: ResumeEntity) -> ResumeSummaryDTO:
        """将实体转换为摘要 DTO。"""

        return ResumeSummaryDTO(
            resume_id=resume.id,
            original_file_name=resume.original_file_name,
            file_extension=resume.file_extension,
            mime_type=resume.mime_type,
            file_size=resume.file_size,
            status=resume.status,
            uploaded_at=resume.uploaded_at,
            updated_at=resume.updated_at,
        )

    def _to_detail_dto(self, resume: ResumeEntity) -> ResumeDetailDTO:
        """将实体转换为详情 DTO。"""

        return ResumeDetailDTO(
            resume_id=resume.id,
            original_file_name=resume.original_file_name,
            file_extension=resume.file_extension,
            mime_type=resume.mime_type,
            file_size=resume.file_size,
            status=resume.status,
            uploaded_at=resume.uploaded_at,
            updated_at=resume.updated_at,
            markdown_content=resume.markdown_content,
            source_metadata=dict(resume.source_metadata_json or {}),
        )

    @staticmethod
    def _generate_identifier() -> str:
        """生成字符串格式的 UUID。"""

        return str(uuid4())


resume_service = ResumeService()


__all__ = [
    "ALLOWED_RESUME_FILE_EXTENSIONS",
    "MAX_RESUME_FILE_SIZE_BYTES",
    "ResumeContextBundle",
    "ResumeService",
    "resume_service",
]
