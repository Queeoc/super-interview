"""面试追问阶段的知识库证据检索工具。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from app.services.rag_service import RagSearchHit, rag_service


class KnowledgeEvidenceToolInput(BaseModel):
    """知识库证据检索输入。"""

    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(default="", description="当前面试 skill 标识")
    category_key: str = Field(default="GENERAL", description="当前问题分类")
    question_text: str = Field(default="", description="当前问题")
    answer_text: str = Field(default="", description="候选人最近回答")
    focus_topics: list[str] = Field(default_factory=list, description="追问重点")
    missing_signals: list[str] = Field(default_factory=list, description="当前缺失信号")
    top_k: int = Field(default=3, ge=1, le=5, description="返回命中条数")
    include_global_skill: bool = Field(default=True, description="是否同时召回通用资料")


class KnowledgeEvidenceItem(BaseModel):
    """单条知识库证据。"""

    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: str = Field(default="", description="知识库 ID")
    document_id: str = Field(default="", description="文档 ID")
    category: str = Field(default="", description="文档分类")
    skill_id: str | None = Field(default=None, description="文档绑定 skill")
    file_name: str = Field(default="", description="来源文件名")
    source: str = Field(default="", description="来源路径")
    score: float = Field(default=0.0, description="相似度分数")
    content: str = Field(default="", description="命中片段")


class KnowledgeEvidenceDocument(BaseModel):
    """命中文档摘要。"""

    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: str = Field(default="", description="知识库 ID")
    document_id: str = Field(default="", description="文档 ID")
    category: str = Field(default="", description="文档分类")
    skill_id: str | None = Field(default=None, description="文档绑定 skill")
    file_name: str = Field(default="", description="来源文件名")
    source: str = Field(default="", description="来源路径")
    score: float = Field(default=0.0, description="最佳相似度")


class KnowledgeEvidenceToolResult(BaseModel):
    """知识库证据检索结果。"""

    model_config = ConfigDict(extra="forbid")

    found: bool = Field(default=False, description="是否命中证据")
    query: str = Field(default="", description="最终检索 query")
    retrieval_reason: str = Field(default="", description="检索结果说明")
    matched_categories: list[str] = Field(default_factory=list, description="命中的分类")
    matched_documents: list[KnowledgeEvidenceDocument] = Field(
        default_factory=list,
        description="命中的文档摘要",
    )
    items: list[KnowledgeEvidenceItem] = Field(default_factory=list, description="命中的证据片段")


async def knowledge_evidence_tool(**kwargs: Any) -> KnowledgeEvidenceToolResult:
    """为追问阶段检索知识库中的技术证据。"""

    tool_input = KnowledgeEvidenceToolInput.model_validate(kwargs)
    query = _build_query_text(tool_input)
    if not query.strip():
        logger.info("knowledge evidence retrieval skipped reason=knowledge_query_empty")
        return KnowledgeEvidenceToolResult(
            found=False,
            query="",
            retrieval_reason="knowledge_query_empty",
        )

    normalized_top_k = max(1, min(5, int(tool_input.top_k or 3)))
    logger.info(
        "knowledge evidence retrieval started skill_id={}, category_key={}, top_k={}, focus_topics={}, missing_signals={}, query_preview={}",
        tool_input.skill_id,
        tool_input.category_key,
        normalized_top_k,
        tool_input.focus_topics,
        tool_input.missing_signals,
        query[:120],
    )

    try:
        search_result = await rag_service.search(
            query=query,
            skill_id=tool_input.skill_id.strip() or None,
            category=tool_input.category_key.strip() or None,
            top_k=normalized_top_k,
            rewrite_enabled=False,
            include_global_skill=tool_input.include_global_skill,
        )
    except Exception as exc:
        logger.warning("knowledge evidence retrieval unavailable error={}", exc)
        return KnowledgeEvidenceToolResult(
            found=False,
            query=query,
            retrieval_reason="knowledge_search_unavailable",
        )

    if not search_result.hits:
        logger.info(
            "knowledge evidence retrieval completed found=false retrieval_reason={} query_preview={}",
            search_result.message or "knowledge_search_empty",
            query[:120],
        )
        return KnowledgeEvidenceToolResult(
            found=False,
            query=query,
            retrieval_reason=search_result.message or "knowledge_search_empty",
        )

    items = [_build_item(hit) for hit in search_result.hits[:normalized_top_k]]
    matched_documents = _build_matched_documents(search_result.hits)
    matched_categories = _unique_values(
        [hit.category for hit in search_result.hits if hit.category.strip()]
    )
    result = KnowledgeEvidenceToolResult(
        found=True,
        query=query,
        retrieval_reason=(
            search_result.message
            if search_result.message and search_result.message != "success"
            else "knowledge_search_matched"
        ),
        matched_categories=matched_categories,
        matched_documents=matched_documents,
        items=items,
    )
    logger.info(
        "knowledge evidence retrieval completed found=true retrieval_reason={} matched_documents={} top_items={}",
        result.retrieval_reason,
        [item.file_name for item in matched_documents[:4]],
        [
            {
                "file_name": item.file_name,
                "category": item.category,
                "score": round(item.score, 4),
            }
            for item in items[:4]
        ],
    )
    return result


def _build_query_text(tool_input: KnowledgeEvidenceToolInput) -> str:
    """把追问阶段信号收敛成稳定检索 query。"""

    parts: list[str] = [tool_input.category_key.strip()]
    parts.extend(
        item
        for item in (
            tool_input.question_text.strip(),
            tool_input.answer_text.strip(),
            _join_terms(tool_input.focus_topics),
            _join_terms(tool_input.missing_signals),
        )
        if item
    )
    return " ".join(part for part in parts if part)


def _join_terms(values: Iterable[str]) -> str:
    """把短词项压成检索友好的字符串。"""

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(text)
    return " ".join(normalized)


def _build_item(hit: RagSearchHit) -> KnowledgeEvidenceItem:
    """将 RAG 命中转换成追问可用的证据片段。"""

    return KnowledgeEvidenceItem(
        knowledge_base_id=hit.knowledge_base_id,
        document_id=hit.document_id,
        category=hit.category,
        skill_id=hit.skill_id,
        file_name=hit.file_name,
        source=hit.source,
        score=hit.score,
        content=hit.content,
    )


def _build_matched_documents(hits: list[RagSearchHit]) -> list[KnowledgeEvidenceDocument]:
    """把 chunk 级命中折叠为文档级摘要。"""

    best_by_document: dict[str, KnowledgeEvidenceDocument] = {}
    for hit in hits:
        current = best_by_document.get(hit.document_id)
        if current is None or hit.score >= current.score:
            best_by_document[hit.document_id] = _build_document(hit)
    return list(best_by_document.values())


def _build_document(hit: RagSearchHit) -> KnowledgeEvidenceDocument:
    """从命中片段构建文档摘要。"""

    return KnowledgeEvidenceDocument(
        knowledge_base_id=hit.knowledge_base_id,
        document_id=hit.document_id,
        category=hit.category,
        skill_id=hit.skill_id,
        file_name=hit.file_name,
        source=hit.source,
        score=hit.score,
    )


def _unique_values(values: Iterable[str]) -> list[str]:
    """对字符串去重并保留原始顺序。"""

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(text)
    return normalized


__all__ = [
    "KnowledgeEvidenceDocument",
    "KnowledgeEvidenceItem",
    "KnowledgeEvidenceToolInput",
    "KnowledgeEvidenceToolResult",
    "knowledge_evidence_tool",
]
