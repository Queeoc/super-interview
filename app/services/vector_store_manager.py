"""向量存储管理器 - 封装 Milvus VectorStore 操作"""

from dataclasses import dataclass
from typing import Any, List

from langchain_core.documents import Document
from langchain_milvus import Milvus
from loguru import logger

from app.config import config
from app.core.milvus_client import milvus_manager
from app.services.vector_embedding_service import vector_embedding_service


# 统一使用 biz collection
COLLECTION_NAME = "biz"


@dataclass(slots=True)
class VectorSearchHit:
    """向量检索命中结果。"""

    id: str
    content: str
    score: float
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """转换为字典结构。"""

        return {
            "id": self.id,
            "content": self.content,
            "score": self.score,
            "metadata": dict(self.metadata),
        }


class VectorStoreManager:
    """向量存储管理器"""

    def __init__(self):
        """初始化向量存储管理器"""
        self.vector_store = None
        self.collection_name = config.milvus.collection_name or COLLECTION_NAME

    def _initialize_vector_store(self):
        """初始化 Milvus VectorStore"""
        try:
            # 必须在 PyMilvus / langchain_milvus 访问 Collection 之前建立连接，
            # 否则会出现 ConnectionNotExistException: should create connection first.
            # （模块导入时就会执行此处，早于 FastAPI lifespan 中的 milvus_manager.connect）
            _ = milvus_manager.connect()

            connection_args = {
                "host": config.milvus_host,
                "port": config.milvus_port,
            }

            # 创建 LangChain Milvus VectorStore
            # 使用 biz collection，字段映射：text_field -> content, vector_field -> vector
            self.vector_store = Milvus(
                embedding_function=vector_embedding_service,
                collection_name=self.collection_name,
                connection_args=connection_args,
                auto_id=False,  # 使用自定义 id
                drop_old=False,
                text_field="content",  # 文本内容存储到 content 字段
                vector_field="vector",  # 向量存储到 vector 字段
                primary_field="id",  # 主键字段
                metadata_field="metadata",  # 元数据字段
            )

            logger.info(
                f"VectorStore 初始化成功: {config.milvus_host}:{config.milvus_port}, "
                f"collection: {self.collection_name}"
            )

        except Exception as e:
            logger.error(f"VectorStore 初始化失败: {e}")
            raise

    def _ensure_initialized(self) -> None:
        """Lazily create the vector store when first needed."""

        if self.vector_store is None:
            self._initialize_vector_store()

    def add_documents(self, documents: List[Document]) -> List[str]:
        """
        批量添加文档到向量存储（自动批量向量化）

        Args:
            documents: 文档列表

        Returns:
            List[str]: 文档 ID 列表
        """
        try:
            self._ensure_initialized()
            import time
            import uuid
            start_time = time.time()

            # 优先使用稳定 chunk_id，方便幂等删除与追踪；缺失时回退到随机 UUID。
            ids = [
                str(document.metadata.get("chunk_id") or uuid.uuid4())
                for document in documents
            ]

            # LangChain Milvus 的 add_documents 会自动调用 embedding_function
            # 并进行批量处理，性能更好
            result_ids = self.vector_store.add_documents(documents, ids=ids)

            elapsed = time.time() - start_time
            logger.info(
                f"批量添加 {len(documents)} 个文档到 VectorStore 完成, "
                f"耗时: {elapsed:.2f}秒, 平均: {elapsed/len(documents):.2f}秒/个"
            )
            return result_ids
        except Exception as e:
            logger.error(f"添加文档失败: {e}")
            raise

    def delete_by_document_id(self, document_id: str) -> int:
        """
        删除指定文件记录对应的所有向量分片。

        Args:
            document_id: 知识库文件记录 ID

        Returns:
            int: 删除的文档数量
        """

        escaped_document_id = self._escape_expr_value(document_id)
        expr = f'metadata["document_id"] == "{escaped_document_id}"'
        return self._delete_by_expr(expr, context_value=document_id, context_label="document_id")

    def delete_by_source(self, file_path: str) -> int:
        """
        删除指定文件的所有文档

        Args:
            file_path: 文件路径

        Returns:
            int: 删除的文档数量
        """
        escaped_path = self._escape_expr_value(file_path)
        expr = f'metadata["_source"] == "{escaped_path}"'
        return self._delete_by_expr(expr, context_value=file_path, context_label="_source")

    def get_vector_store(self) -> Milvus:
        """
        获取 VectorStore 实例

        Returns:
            Milvus: VectorStore 实例
        """
        self._ensure_initialized()
        return self.vector_store

    def similarity_search(self, query: str, k: int = 3) -> List[Document]:
        """
        相似度搜索

        Args:
            query: 查询文本
            k: 返回结果数量

        Returns:
            List[Document]: 相关文档列表
        """
        try:
            self._ensure_initialized()
            docs = self.vector_store.similarity_search(query, k=k)
            logger.debug(f"相似度搜索完成: query='{query}', 结果数={len(docs)}")
            return docs
        except Exception as e:
            logger.error(f"相似度搜索失败: {e}")
            return []

    def search_documents(
        self,
        query: str,
        *,
        top_k: int = 3,
        score_threshold: float | None = None,
        knowledge_base_ids: list[str] | None = None,
        category: str | None = None,
    ) -> list[VectorSearchHit]:
        """
        按知识库和分类过滤进行向量检索。

        Args:
            query: 检索问题
            top_k: 返回条数
            score_threshold: 最大距离阈值，L2 距离越小越相似
            knowledge_base_ids: 限定知识库 ID 列表
            category: 可选分类过滤

        Returns:
            list[VectorSearchHit]: 检索命中结果
        """

        try:
            self._ensure_initialized()
            collection = milvus_manager.get_collection()
            query_vector = vector_embedding_service.embed_query(query)
            expr = self._build_metadata_filter_expression(
                knowledge_base_ids=knowledge_base_ids,
                category=category,
            )

            search_kwargs: dict[str, Any] = {
                "data": [query_vector],
                "anns_field": "vector",
                "param": {
                    "metric_type": "L2",
                    "params": {"nprobe": 10},
                },
                "limit": top_k,
                "output_fields": ["id", "content", "metadata"],
            }
            if expr is not None:
                search_kwargs["expr"] = expr

            results = collection.search(**search_kwargs)

            hits: list[VectorSearchHit] = []
            for search_hits in results:
                for hit in search_hits:
                    score = float(hit.distance)
                    if score_threshold is not None and score > score_threshold:
                        continue

                    metadata = hit.entity.get("metadata", {}) or {}
                    hits.append(
                        VectorSearchHit(
                            id=str(hit.entity.get("id")),
                            content=str(hit.entity.get("content") or ""),
                            score=score,
                            metadata=dict(metadata),
                        )
                    )

            logger.info(
                "知识检索完成: query={}, top_k={}, score_threshold={}, knowledge_base_ids={}, category={}, result_count={}",
                query,
                top_k,
                score_threshold,
                knowledge_base_ids or [],
                category,
                len(hits),
            )
            return hits
        except Exception as exc:
            logger.error("知识检索失败: query={}, error={}", query, exc)
            raise

    def _build_metadata_filter_expression(
        self,
        *,
        knowledge_base_ids: list[str] | None,
        category: str | None,
    ) -> str | None:
        """构建 Milvus JSON 元数据过滤表达式。"""

        expressions: list[str] = []

        if knowledge_base_ids:
            kb_expressions = [
                f'metadata["knowledge_base_id"] == "{self._escape_expr_value(knowledge_base_id)}"'
                for knowledge_base_id in knowledge_base_ids
            ]
            expressions.append(f"({' or '.join(kb_expressions)})")

        if category:
            expressions.append(
                f'metadata["category"] == "{self._escape_expr_value(category)}"'
            )

        if not expressions:
            return None

        return " and ".join(expressions)

    def _delete_by_expr(self, expr: str, *, context_value: str, context_label: str) -> int:
        """执行通用删除表达式。"""

        try:
            self._ensure_initialized()
            collection = milvus_manager.get_collection()
            result = collection.delete(expr)
            deleted_count = result.delete_count if hasattr(result, "delete_count") else 0
            logger.info(
                "删除向量旧数据: {}={}, 删除数量={}",
                context_label,
                context_value,
                deleted_count,
            )
            return int(deleted_count)
        except Exception as exc:
            logger.warning(
                "删除向量旧数据失败(可能是首次索引): {}={}, error={}",
                context_label,
                context_value,
                exc,
            )
            return 0

    def _escape_expr_value(self, value: str) -> str:
        """对 Milvus 表达式中的字符串做最小转义。"""

        return value.replace("\\", "\\\\").replace('"', '\\"')


# 全局单例
vector_store_manager = VectorStoreManager()
