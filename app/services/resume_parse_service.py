"""简历文件解析与标准化服务。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from loguru import logger
import pymupdf4llm

from app.utils.exceptions import BusinessException, ErrorCode


_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


@dataclass(slots=True)
class ParsedResumeContent:
    """简历解析结果。"""

    markdown_content: str
    normalized_content: str
    content_hash: str


class ResumeParseService:
    """负责把 PDF / Markdown 转为标准化 Markdown。"""

    def parse_file(self, file_path: Path, *, file_extension: str) -> ParsedResumeContent:
        """解析文件并生成稳定内容指纹。"""

        extension = file_extension.lower().lstrip(".")
        try:
            if extension == "pdf":
                markdown_content = self._parse_pdf(file_path)
            elif extension == "md":
                markdown_content = file_path.read_text(encoding="utf-8")
            else:
                raise BusinessException(
                    code=ErrorCode.RESUME_FILE_TYPE_NOT_SUPPORTED,
                    message="简历文件仅支持 PDF 或 Markdown",
                    details={"file_extension": file_extension},
                )
        except BusinessException:
            raise
        except Exception as exc:
            logger.exception("简历解析失败: file_path={}, error={}", file_path, exc)
            raise BusinessException(
                code=ErrorCode.RESUME_PARSE_FAILED,
                message="简历解析失败",
                details={"file_path": str(file_path), "error": str(exc)},
            ) from exc

        normalized_content = self._normalize_markdown(markdown_content)
        content_hash = sha256(normalized_content.encode("utf-8")).hexdigest()
        return ParsedResumeContent(
            markdown_content=normalized_content,
            normalized_content=normalized_content,
            content_hash=content_hash,
        )

    def parse_bytes(
        self,
        *,
        file_name: str,
        file_content: bytes,
        file_extension: str,
    ) -> ParsedResumeContent:
        """从内存字节解析简历。"""

        extension = file_extension.lower().lstrip(".")

        try:
            if extension == "pdf":
                with self._temporary_file(file_name, file_content) as temp_path:
                    markdown_content = self._parse_pdf(temp_path)
            elif extension == "md":
                markdown_content = file_content.decode("utf-8")
            else:
                raise BusinessException(
                    code=ErrorCode.RESUME_FILE_TYPE_NOT_SUPPORTED,
                    message="简历文件仅支持 PDF 或 Markdown",
                    details={"file_extension": file_extension},
                )
        except BusinessException:
            raise
        except Exception as exc:
            logger.exception("简历解析失败: file_name={}, error={}", file_name, exc)
            raise BusinessException(
                code=ErrorCode.RESUME_PARSE_FAILED,
                message="简历解析失败",
                details={"file_name": file_name, "error": str(exc)},
            ) from exc

        normalized_content = self._normalize_markdown(markdown_content)
        content_hash = sha256(normalized_content.encode("utf-8")).hexdigest()
        return ParsedResumeContent(
            markdown_content=normalized_content,
            normalized_content=normalized_content,
            content_hash=content_hash,
        )

    def _parse_pdf(self, file_path: Path) -> str:
        """使用 pymupdf4llm 将 PDF 转成 Markdown。"""

        return str(pymupdf4llm.to_markdown(str(file_path)))

    def _normalize_markdown(self, content: str) -> str:
        """对 Markdown 做最小标准化，保证指纹稳定。"""

        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        normalized = _WHITESPACE_RE.sub(" ", normalized)
        normalized = "\n".join(line.rstrip() for line in normalized.splitlines())
        normalized = _BLANK_LINES_RE.sub("\n\n", normalized)
        return normalized.strip()

    def _temporary_file(self, file_name: str, file_content: bytes):
        """写入临时文件以兼容仅支持路径的 PDF 解析场景。"""

        from contextlib import contextmanager

        @contextmanager
        def _manager():
            with NamedTemporaryFile(prefix="resume_", suffix=Path(file_name).suffix, delete=True) as temp_file:
                temp_file.write(file_content)
                temp_file.flush()
                yield Path(temp_file.name)

        return _manager()


resume_parse_service = ResumeParseService()


__all__ = ["ParsedResumeContent", "ResumeParseService", "resume_parse_service"]
