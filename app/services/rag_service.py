"""知识库检索服务。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from langchain_qwq import ChatQwen
from loguru import logger

from app.config import config
from app.services.vector_store_manager import (
    VectorSearchHit,
    VectorStoreManager,
    vector_store_manager,
)
from app.utils.exceptions import BusinessException, ErrorCode

DEFAULT_QUERY_REWRITE_PROMPT_PATH = (
    Path(__file__).resolve().parents[2] / "prompts" / "rag" / "query_rewrite.st"
)


@dataclass(slots=True)
class RagSearchHit:
    """RAG 检索返回给 API 的命中项。"""

    knowledge_base_id: str
    document_id: str
    category: str
    source_type: str
    skill_id: str | None
    file_name: str
    source: str
    score: float
    content: str

    def to_dict(self) -> dict[str, Any]:
        """转换为接口响应字典。"""

        return {
            "knowledge_base_id": self.knowledge_base_id,
            "document_id": self.document_id,
            "category": self.category,
            "source_type": self.source_type,
            "skill_id": self.skill_id,
            "file_name": self.file_name,
            "source": self.source,
            "score": self.score,
            "content": self.content,
        }


@dataclass(slots=True)
class RagSearchResult:
    """RAG 检索接口结果。"""

    query: str
    rewritten_query: str
    used_rewrite: bool
    top_k: int
    score_threshold: float | None
    hits: list[RagSearchHit]
    message: str

    def to_dict(self) -> dict[str, Any]:
        """转换为接口响应字典。"""

        return {
            "query": self.query,
            "rewritten_query": self.rewritten_query,
            "used_rewrite": self.used_rewrite,
            "top_k": self.top_k,
            "score_threshold": self.score_threshold,
            "hits": [hit.to_dict() for hit in self.hits],
            "message": self.message,
        }


class RagService:
    """提供独立于旧聊天链路的知识检索能力。"""

    def __init__(
        self,
        *,
        vector_store: VectorStoreManager | None = None,
        rewrite_model: Any | None = None,
        rewrite_prompt_path: Path | None = None,
    ) -> None:
        self._vector_store = vector_store or vector_store_manager
        self._rewrite_model = rewrite_model
        self._rewrite_prompt_path = rewrite_prompt_path or DEFAULT_QUERY_REWRITE_PROMPT_PATH
        self._rewrite_prompt_cache: str | None = None

    async def search(
        self,
        *,
        query: str,
        knowledge_base_id: str | None = None,
        knowledge_base_ids: Sequence[str] | None = None,
        skill_id: str | None = None,
        include_global_skill: bool = True,
        category: str | None = None,
        top_k: int | None = None,
        score_threshold: float | None = None,
        rewrite_enabled: bool | None = None,
    ) -> RagSearchResult:
        """
        执行按知识库和分类过滤的向量检索。

        Args:
            query: 用户检索问题
            knowledge_base_id: 单个知识库 ID
            knowledge_base_ids: 多知识库联合检索 ID 列表
            skill_id: 可选 skill 过滤
            include_global_skill: 是否同时召回通用资料
            category: 可选分类
            top_k: 可选返回数量
            score_threshold: 可选分数阈值
            rewrite_enabled: 是否启用 query rewrite

        Returns:
            RagSearchResult: 检索结果
        """

        normalized_query = query.strip()
        if not normalized_query:
            raise BusinessException(
                code=ErrorCode.BAD_REQUEST,
                message="检索问题不能为空",
            )

        resolved_top_k = top_k if top_k is not None else config.rag.top_k
        resolved_score_threshold = (
            score_threshold if score_threshold is not None else config.rag.score_threshold
        )
        resolved_rewrite_enabled = (
            config.rag.rewrite_enabled if rewrite_enabled is None else rewrite_enabled
        )
        normalized_knowledge_base_ids = self._normalize_knowledge_base_ids(
            knowledge_base_id=knowledge_base_id,
            knowledge_base_ids=knowledge_base_ids,
        )
        rewritten_query, used_rewrite = await self._maybe_rewrite_query(
            query=normalized_query,
            rewrite_enabled=resolved_rewrite_enabled,
        )

        try:
            raw_hits = self._vector_store.search_documents(
                rewritten_query,
                top_k=resolved_top_k,
                score_threshold=resolved_score_threshold,
                knowledge_base_ids=normalized_knowledge_base_ids or None,
                skill_id=skill_id.strip() if skill_id else None,
                include_global_skill=include_global_skill,
                category=category.strip() if category else None,
            )
        except Exception as exc:
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_SEARCH_FAILED,
                message="知识检索失败",
                http_status=500,
                details={"query": normalized_query, "error": str(exc)},
            ) from exc

        hits = [self._build_search_hit(hit) for hit in raw_hits]
        result_message = "success" if hits else config.rag.empty_result_message

        logger.info(
            "知识检索完成: query={}, rewritten_query={}, used_rewrite={}, result_count={}",
            normalized_query,
            rewritten_query,
            used_rewrite,
            len(hits),
        )
        return RagSearchResult(
            query=normalized_query,
            rewritten_query=rewritten_query,
            used_rewrite=used_rewrite,
            top_k=resolved_top_k,
            score_threshold=resolved_score_threshold,
            hits=hits,
            message=result_message,
        )

    def _normalize_knowledge_base_ids(
        self,
        *,
        knowledge_base_id: str | None,
        knowledge_base_ids: Sequence[str] | None,
    ) -> list[str]:
        """归一化知识库 ID 列表，保持输入顺序并去重。"""

        normalized_ids: list[str] = []
        seen: set[str] = set()

        for raw_value in [knowledge_base_id, *(knowledge_base_ids or [])]:
            if raw_value is None:
                continue
            normalized_value = raw_value.strip()
            if not normalized_value or normalized_value in seen:
                continue
            seen.add(normalized_value)
            normalized_ids.append(normalized_value)

        return normalized_ids

    async def _maybe_rewrite_query(
        self,
        *,
        query: str,
        rewrite_enabled: bool,
    ) -> tuple[str, bool]:
        """根据配置决定是否执行 query rewrite，失败时降级为原始问题。"""

        if not rewrite_enabled:
            return query, False

        try:
            prompt_template = self._load_rewrite_prompt()
            prompt_text = prompt_template.format(user_query=self._wrap_untrusted_query(query))
            rewritten_query = (await self._invoke_rewrite_model(prompt_text)).strip()
            if not rewritten_query:
                return query, False
            return rewritten_query, True
        except Exception as exc:
            logger.warning("query rewrite 失败，降级使用原始查询: query={}, error={}", query, exc)
            return query, False

    def _load_rewrite_prompt(self) -> str:
        """加载并缓存 query rewrite 提示模板。"""

        if self._rewrite_prompt_cache is not None:
            return self._rewrite_prompt_cache

        if self._rewrite_prompt_path.exists():
            self._rewrite_prompt_cache = self._rewrite_prompt_path.read_text(encoding="utf-8")
        else:
            self._rewrite_prompt_cache = (
                "你是知识检索查询改写助手。"
                "请把用户问题改写成更适合知识库检索的单行查询，"
                "不要回答问题，不要输出解释。\n"
                "<user_query>\n{user_query}\n</user_query>"
            )
        return self._rewrite_prompt_cache

    async def _invoke_rewrite_model(self, prompt_text: str) -> str:
        """调用兼容当前项目的 ChatQwen 进行查询改写。"""

        model = self._get_rewrite_model()
        if hasattr(model, "ainvoke"):
            response = await model.ainvoke(prompt_text)
        else:
            response = await asyncio.to_thread(model.invoke, prompt_text)
        return self._extract_response_text(response)

    def _get_rewrite_model(self) -> Any:
        """延迟初始化 query rewrite 模型。"""

        if self._rewrite_model is None:
            self._rewrite_model = ChatQwen(
                model=config.rag.model,
                api_key=config.dashscope_api_key,
                temperature=0.0,
                streaming=False,
            )
        return self._rewrite_model

    def _extract_response_text(self, response: Any) -> str:
        """从不同形态的模型响应中提取纯文本。"""

        if isinstance(response, str):
            return response

        content = getattr(response, "content", "")
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            texts: list[str] = []
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    texts.append(str(block.get("text", "")))
                else:
                    texts.append(str(block))
            return "".join(texts)

        return str(response)

    def _wrap_untrusted_query(self, query: str) -> str:
        """给用户查询增加显式边界，避免直接注入提示词。"""

        return f"<question>\n{query}\n</question>"

    def _build_search_hit(self, hit: VectorSearchHit) -> RagSearchHit:
        """把向量检索命中项映射成 API 需要的稳定字段。"""

        metadata = hit.metadata or {}
        skill_id_raw = metadata.get("skill_id")
        skill_id = str(skill_id_raw) if skill_id_raw not in (None, "") else None

        return RagSearchHit(
            knowledge_base_id=str(metadata.get("knowledge_base_id", "")),
            document_id=str(metadata.get("document_id", "")),
            category=str(metadata.get("category", "")),
            source_type=str(metadata.get("source_type", "")),
            skill_id=skill_id,
            file_name=str(metadata.get("_file_name", "")),
            source=str(metadata.get("_source", "")),
            score=hit.score,
            content=hit.content,
        )


rag_service = RagService()


__all__ = [
    "RagSearchHit",
    "RagSearchResult",
    "RagService",
    "rag_service",
]
