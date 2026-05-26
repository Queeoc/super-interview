"""向量嵌入服务模块 - 基于 LangChain Embeddings 标准接口"""

from collections.abc import Iterator
from typing import List

from langchain_core.embeddings import Embeddings
from openai import OpenAI
from loguru import logger

from app.config import config


class DashScopeEmbeddings(Embeddings):
    """阿里云 DashScope Text Embedding (OpenAI 兼容模式)
    
    实现 LangChain 标准 Embeddings 接口:
    - embed_documents(texts: List[str]) → List[List[float]]: 批量嵌入文档
    - embed_query(text: str) → List[float]: 嵌入单个查询
    """

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-v4",
        dimensions: int = 1024,
        max_batch_size: int = 10,
    ):
        """
        初始化 DashScope Embeddings
        
        Args:
            api_key: DashScope API Key
            model: 嵌入模型名称
            dimensions: 向量维度
            max_batch_size: 单次批量嵌入最大条数
        """
        if not api_key or api_key == "your-api-key-here":
            raise ValueError("请设置环境变量 DASHSCOPE_API_KEY")
        if max_batch_size <= 0:
            raise ValueError("max_batch_size 必须大于 0")
        
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self.model = model
        self.dimensions = dimensions
        self.max_batch_size = max_batch_size
        
        # 打印初始化信息
        masked_key = self._mask_api_key(api_key)
        logger.info(
            f"DashScope Embeddings 初始化完成 - "
            f"模型: {model}, 维度: {dimensions}, API Key: {masked_key}"
        )

    @staticmethod
    def _mask_api_key(api_key: str) -> str:
        """掩码 API Key 用于日志"""
        if len(api_key) > 8:
            return f"{api_key[:8]}...{api_key[-4:]}"
        return "***"

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        批量嵌入文档列表 (LangChain 标准接口)
        
        Args:
            texts: 文本列表
            
        Returns:
            List[List[float]]: 嵌入向量列表
        """
        if not texts:
            return []
        
        try:
            logger.info(f"批量嵌入 {len(texts)} 个文档")

            embeddings: List[List[float]] = []
            total_batches = (len(texts) + self.max_batch_size - 1) // self.max_batch_size

            for batch_index, batch_texts in enumerate(
                self._iter_batches(texts, self.max_batch_size),
                start=1,
            ):
                logger.debug(
                    "执行文档嵌入批次: batch={}/{}, size={}",
                    batch_index,
                    total_batches,
                    len(batch_texts),
                )
                response = self.client.embeddings.create(
                    model=self.model,
                    input=batch_texts,
                    dimensions=self.dimensions,
                    encoding_format="float"
                )
                embeddings.extend(item.embedding for item in response.data)

            logger.debug(f"批量嵌入完成, 维度: {len(embeddings[0])}")
            
            return embeddings
            
        except Exception as e:
            logger.error(f"批量嵌入失败: {e}")
            raise RuntimeError(f"批量嵌入失败: {e}") from e

    def embed_query(self, text: str) -> List[float]:
        """
        嵌入单个查询文本 (LangChain 标准接口)
        
        Args:
            text: 查询文本
            
        Returns:
            List[float]: 嵌入向量
        """
        if not text or not text.strip():
            raise ValueError("查询文本不能为空")
        
        try:
            logger.debug(f"嵌入查询, 长度: {len(text)} 字符")
            
            response = self.client.embeddings.create(
                model=self.model,
                input=text,
                dimensions=self.dimensions,
                encoding_format="float"
            )
            
            embedding = response.data[0].embedding
            logger.debug(f"查询嵌入完成, 维度: {len(embedding)}")
            
            return embedding
            
        except Exception as e:
            logger.error(f"查询嵌入失败: {e}")
            raise RuntimeError(f"查询嵌入失败: {e}") from e

    def _iter_batches(self, texts: List[str], batch_size: int) -> Iterator[List[str]]:
        """把文本列表按固定批次切分。"""

        for start_index in range(0, len(texts), batch_size):
            yield texts[start_index : start_index + batch_size]


# 全局单例
vector_embedding_service = DashScopeEmbeddings(
    api_key=config.dashscope_api_key,
    model=config.dashscope_embedding_model,
    dimensions=config.milvus.vector_dim,
)
