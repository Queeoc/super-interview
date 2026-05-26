"""文档分割服务模块 - 基于 LangChain 的智能文档分割"""

from pathlib import Path
from typing import Any, List

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from loguru import logger

from app.config import config


class DocumentSplitterService:
    """文档分割服务 - 使用 LangChain 的分割器"""

    def __init__(self):
        """初始化文档分割服务"""
        self.chunk_size = config.chunk_max_size
        self.chunk_overlap = config.chunk_overlap

        # Markdown 标题分割器 (只按一级和二级标题分割，减少分片数)
        self.markdown_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#", "h1"),
                ("##", "h2"),
                # 不再按三级标题分割，避免过度碎片化
            ],
            strip_headers=False,  # 保留标题在内容中
        )

        # 递归字符分割器 (用于二次分割，使用更大的chunk_size)
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size * 2,  # 加倍chunk_size，减少分片数
            chunk_overlap=self.chunk_overlap,
            length_function=len,
            is_separator_regex=False,
        )

        logger.info(
            f"文档分割服务初始化完成, chunk_size={self.chunk_size}, "
            f"secondary_chunk_size={self.chunk_size * 2}, "
            f"overlap={self.chunk_overlap}"
        )

    def split_markdown(
        self,
        content: str,
        file_path: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> List[Document]:
        """
        分割 Markdown 文档 (两阶段分割 + 合并小片段)

        Args:
            content: Markdown 内容
            file_path: 文件路径 (用于元数据)
            metadata: 额外元数据

        Returns:
            List[Document]: 文档分片列表
        """
        if not content or not content.strip():
            logger.warning(f"Markdown 文档内容为空: {file_path}")
            return []

        try:
            # 第一阶段: 按标题分割
            md_docs = self.markdown_splitter.split_text(content)

            # 第二阶段: 按大小进一步分割
            docs_after_split = self.text_splitter.split_documents(md_docs)

            # 第三阶段: 合并太小的分片 (< 300字符)
            final_docs = self._merge_small_chunks(docs_after_split, min_size=300)

            # 添加标准化元数据
            base_metadata = self._build_base_metadata(file_path=file_path, extra_metadata=metadata)
            self._apply_chunk_metadata(final_docs, base_metadata)

            logger.info(f"Markdown 分割完成: {file_path} -> {len(final_docs)} 个分片")
            return final_docs

        except Exception as e:
            logger.error(f"Markdown 分割失败: {file_path}, 错误: {e}")
            raise

    def split_text(
        self,
        content: str,
        file_path: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> List[Document]:
        """
        分割普通文本文档

        Args:
            content: 文本内容
            file_path: 文件路径 (用于元数据)
            metadata: 额外元数据

        Returns:
            List[Document]: 文档分片列表
        """
        if not content or not content.strip():
            logger.warning(f"文本文档内容为空: {file_path}")
            return []

        try:
            # 直接使用递归字符分割器
            base_metadata = self._build_base_metadata(file_path=file_path, extra_metadata=metadata)
            docs = self.text_splitter.create_documents(
                texts=[content],
                metadatas=[base_metadata],
            )
            self._apply_chunk_metadata(docs, base_metadata)

            logger.info(f"文本分割完成: {file_path} -> {len(docs)} 个分片")
            return docs

        except Exception as e:
            logger.error(f"文本分割失败: {file_path}, 错误: {e}")
            raise

    def split_document(
        self,
        content: str,
        file_path: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> List[Document]:
        """
        智能分割文档 (根据文件类型选择分割器)

        Args:
            content: 文档内容
            file_path: 文件路径
            metadata: 额外元数据

        Returns:
            List[Document]: 文档分片列表
        """
        if file_path.endswith(".md"):
            return self.split_markdown(content, file_path, metadata=metadata)
        return self.split_text(content, file_path, metadata=metadata)

    def _merge_small_chunks(
        self, documents: List[Document], min_size: int = 300
    ) -> List[Document]:
        """
        合并太小的分片

        Args:
            documents: 文档列表
            min_size: 最小分片大小 (字符数)

        Returns:
            List[Document]: 合并后的文档列表
        """
        if not documents:
            return []

        merged_docs = []
        current_doc = None

        for doc in documents:
            doc_size = len(doc.page_content)

            if current_doc is None:
                # 第一个文档
                current_doc = doc
            elif doc_size < min_size and len(current_doc.page_content) < self.chunk_size * 2:
                # 当前文档太小且合并后不会太大，则合并
                current_doc.page_content += "\n\n" + doc.page_content
                # 保留主文档的元数据
            else:
                # 保存当前文档，开始新文档
                merged_docs.append(current_doc)
                current_doc = doc

        # 添加最后一个文档
        if current_doc is not None:
            merged_docs.append(current_doc)

        return merged_docs

    def _build_base_metadata(
        self,
        file_path: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构建每个分片都需要带上的稳定元数据。"""

        path = Path(file_path) if file_path else Path("")
        base_metadata: dict[str, Any] = {
            "_source": file_path,
            "_extension": path.suffix,
            "_file_name": path.name,
        }

        if extra_metadata:
            base_metadata.update(extra_metadata)

        return base_metadata

    def _apply_chunk_metadata(
        self,
        documents: List[Document],
        base_metadata: dict[str, Any],
    ) -> None:
        """把统一元数据和 chunk 索引注入到分片中。"""

        document_id = str(base_metadata.get("document_id", "")).strip()

        for chunk_index, document in enumerate(documents):
            merged_metadata = dict(document.metadata)
            merged_metadata.update(base_metadata)
            merged_metadata["chunk_index"] = chunk_index

            if document_id:
                merged_metadata["chunk_id"] = f"{document_id}:{chunk_index}"

            document.metadata = merged_metadata


# 全局单例
document_splitter_service = DocumentSplitterService()
