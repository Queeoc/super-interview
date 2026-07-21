"""向量索引服务模块"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from app.config import config
from app.models.knowledge import KnowledgeDocumentEntity
from app.services.document_splitter_service import document_splitter_service
from app.services.vector_store_manager import vector_store_manager


class IndexingResult:
    """索引结果类"""

    def __init__(self):
        self.success = False
        self.directory_path = ""
        self.total_files = 0
        self.success_count = 0
        self.fail_count = 0
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        self.error_message = ""
        self.failed_files: dict[str, str] = {}

    def increment_success_count(self):
        """增加成功计数"""
        self.success_count += 1

    def increment_fail_count(self):
        """增加失败计数"""
        self.fail_count += 1

    def add_failed_file(self, file_path: str, error: str):
        """添加失败文件"""
        self.failed_files[file_path] = error

    def get_duration_ms(self) -> int:
        """获取耗时（毫秒）"""
        if self.start_time and self.end_time:
            return int((self.end_time - self.start_time).total_seconds() * 1000)
        return 0

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "success": self.success,
            "directory_path": self.directory_path,
            "total_files": self.total_files,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "duration_ms": self.get_duration_ms(),
            "error_message": self.error_message,
            "failed_files": self.failed_files,
        }


@dataclass(slots=True)
class KnowledgeIndexingResult:
    """知识库文件入向量库结果。"""

    success: bool
    document_id: str
    knowledge_base_id: str
    storage_path: str
    chunk_count: int = 0
    error_message: str = ""
    vector_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""

        return {
            "success": self.success,
            "document_id": self.document_id,
            "knowledge_base_id": self.knowledge_base_id,
            "storage_path": self.storage_path,
            "chunk_count": self.chunk_count,
            "error_message": self.error_message,
            "vector_ids": list(self.vector_ids),
        }


class VectorIndexService:
    """向量索引服务 - 负责读取文件、生成向量、存储到 Milvus"""

    def __init__(self):
        """初始化向量索引服务"""
        self.upload_path = config.storage.base_dir
        logger.info("向量索引服务初始化完成")

    def index_directory(self, directory_path: Optional[str] = None) -> IndexingResult:
        """
        索引指定目录下的所有文件

        Args:
            directory_path: 目录路径（可选，默认使用配置的上传目录）

        Returns:
            IndexingResult: 索引结果
        """
        result = IndexingResult()
        result.start_time = datetime.now()

        try:
            # 使用指定目录或默认上传目录
            target_path = directory_path if directory_path else self.upload_path
            dir_path = Path(target_path).resolve()

            if not dir_path.exists() or not dir_path.is_dir():
                raise ValueError(f"目录不存在或不是有效目录: {target_path}")

            result.directory_path = str(dir_path)

            # 获取所有支持的文件
            files = list(dir_path.glob("*.txt")) + list(dir_path.glob("*.md"))

            if not files:
                logger.warning(f"目录中没有找到支持的文件: {target_path}")
                result.total_files = 0
                result.success = True
                result.end_time = datetime.now()
                return result

            result.total_files = len(files)
            logger.info(f"开始索引目录: {target_path}, 找到 {len(files)} 个文件")

            # 遍历并索引每个文件
            for file_path in files:
                try:
                    self.index_single_file(str(file_path))
                    result.increment_success_count()
                    logger.info(f"✓ 文件索引成功: {file_path.name}")
                except Exception as e:
                    result.increment_fail_count()
                    result.add_failed_file(str(file_path), str(e))
                    logger.error(f"✗ 文件索引失败: {file_path.name}, 错误: {e}")

            result.success = result.fail_count == 0
            result.end_time = datetime.now()

            logger.info(
                f"目录索引完成: 总数={result.total_files}, "
                f"成功={result.success_count}, 失败={result.fail_count}"
            )

            return result

        except Exception as e:
            logger.error(f"索引目录失败: {e}")
            result.success = False
            result.error_message = str(e)
            result.end_time = datetime.now()
            return result

    def index_single_file(self, file_path: str) -> None:
        """
        索引单个文件 (使用新的 LangChain 分割器)

        Args:
            file_path: 文件路径

        Raises:
            ValueError: 文件不存在时抛出
            RuntimeError: 索引失败时抛出
        """
        path = Path(file_path).resolve()

        if not path.exists() or not path.is_file():
            raise ValueError(f"文件不存在: {file_path}")

        logger.info(f"开始索引文件: {path}")

        try:
            # 1. 读取文件内容
            content = self._read_text_content(path)
            logger.info(f"读取文件: {path}, 内容长度: {len(content)} 字符")

            # 2. 删除该文件的旧数据（如果存在）
            normalized_path = path.as_posix()
            vector_store_manager.delete_by_source(normalized_path)

            # 3. 使用新的文档分割器
            documents = document_splitter_service.split_document(content, normalized_path)
            logger.info(f"文档分割完成: {file_path} -> {len(documents)} 个分片")

            # 4. 添加文档到向量存储
            if documents:
                vector_store_manager.add_documents(documents)
                logger.info(f"文件索引完成: {file_path}, 共 {len(documents)} 个分片")
            else:
                logger.warning(f"文件内容为空或无法分割: {file_path}")

        except Exception as e:
            logger.error(f"索引文件失败: {file_path}, 错误: {e}")
            raise RuntimeError(f"索引文件失败: {e}") from e

    def index_knowledge_document(
        self,
        document: KnowledgeDocumentEntity,
    ) -> KnowledgeIndexingResult:
        """
        按知识库文件记录完成文档向量化入库。

        Args:
            document: 知识库文件记录

        Returns:
            KnowledgeIndexingResult: 结构化索引结果
        """

        path = Path(document.storage_path).resolve()
        logger.info(
            "开始索引知识库文件: knowledge_base_id={}, document_id={}, path={}",
            document.knowledge_base_id,
            document.id,
            path,
        )

        try:
            if not path.exists() or not path.is_file():
                raise ValueError(f"知识库文件不存在: {document.storage_path}")

            content = self._read_text_content(path)
            normalized_path = path.as_posix()

            vector_store_manager.delete_by_document_id(document.id)
            documents = document_splitter_service.split_document(
                content,
                normalized_path,
                metadata={
                    "knowledge_base_id": document.knowledge_base_id,
                    "document_id": document.id,
                    "skill_id": document.skill_id,
                    "category": document.category,
                    "is_enabled": bool(document.is_enabled),
                    "_source": normalized_path,
                    "_file_name": document.original_file_name,
                    "_extension": document.file_extension,
                },
            )

            vector_ids: list[str] = []
            if documents:
                vector_ids = vector_store_manager.add_documents(documents)

            logger.info(
                "知识库文件索引完成: knowledge_base_id={}, document_id={}, chunk_count={}",
                document.knowledge_base_id,
                document.id,
                len(documents),
            )

            return KnowledgeIndexingResult(
                success=True,
                document_id=document.id,
                knowledge_base_id=document.knowledge_base_id,
                storage_path=document.storage_path,
                chunk_count=len(documents),
                vector_ids=vector_ids,
            )
        except Exception as exc:
            logger.error(
                "知识库文件索引失败: knowledge_base_id={}, document_id={}, error={}",
                document.knowledge_base_id,
                document.id,
                exc,
            )
            return KnowledgeIndexingResult(
                success=False,
                document_id=document.id,
                knowledge_base_id=document.knowledge_base_id,
                storage_path=document.storage_path,
                error_message=str(exc),
            )

    def _read_text_content(self, path: Path) -> str:
        """读取 UTF-8 文本内容。"""

        return path.read_text(encoding="utf-8")


# 全局单例
vector_index_service = VectorIndexService()
