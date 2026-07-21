"""简历证据检索工具。"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import math
import re
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

_HEADER_PATTERN = re.compile(r"^(#{1,6})\s+(?P<title>.+?)\s*$")
_BULLET_PATTERN = re.compile(r"^\s*[-*+]\s+")
_ACTION_TERMS = (
    "设计",
    "优化",
    "排查",
    "实现",
    "负责",
    "搭建",
    "主导",
    "开发",
    "改造",
    "落地",
    "design",
    "optimize",
    "optimized",
    "implement",
    "implemented",
    "led",
    "owned",
    "built",
)
_RESULT_TERMS = (
    "提升",
    "降低",
    "峰值",
    "qps",
    "缩短",
    "减少",
    "稳定",
    "收益",
    "throughput",
    "latency",
    "availability",
    "reduced",
    "improved",
)
_STOP_TERMS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "have",
    "has",
    "were",
    "was",
}
_SECTION_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("project", ("项目", "project", "projects", "项目经历", "项目经验")),
    ("work", ("工作", "经历", "work", "experience", "professional", "任职", "实习")),
    ("skill", ("技能", "skill", "skills", "tech stack", "技术栈")),
    ("education", ("教育", "education", "学校", "学历")),
)
_BM25_K1 = 1.5
_BM25_B = 0.75
_SEMANTIC_WEIGHT = 0.45
_BM25_WEIGHT = 0.55
_SEMANTIC_CUTOFF = 0.18
_SEMANTIC_CACHE: dict[str, list[float]] = {}
_QUERY_EMBEDDING_CACHE: dict[str, list[float]] = {}


class ResumeEvidenceToolInput(BaseModel):
    """简历证据检索输入。"""

    model_config = ConfigDict(extra="forbid")

    resume_markdown: str = Field(default="", description="简历 Markdown 内容")
    resume_metadata: dict[str, Any] = Field(default_factory=dict, description="简历元数据")
    category_key: str = Field(default="GENERAL", description="问题所属分类")
    question_text: str = Field(default="", description="当前问题")
    answer_text: str = Field(default="", description="候选人最近一次回答")
    focus_topics: list[str] = Field(default_factory=list, description="追问聚焦主题")
    top_k: int = Field(default=3, ge=1, le=5, description="返回片段数")
    missing_signals: list[str] = Field(default_factory=list, description="当前仍缺失的信号")


class ResumeEvidenceItem(BaseModel):
    """单条简历证据。"""

    model_config = ConfigDict(extra="forbid")

    snippet: str = Field(..., min_length=1, description="命中的简历片段")
    source_section: str = Field(..., description="来源章节")
    matched_terms: list[str] = Field(default_factory=list, description="命中的关键词")
    relevance_score: float = Field(default=0.0, description="相关性分数")


class ResumeEvidenceToolResult(BaseModel):
    """简历证据检索结果。"""

    model_config = ConfigDict(extra="forbid")

    found: bool = Field(default=False, description="是否命中相关证据")
    items: list[ResumeEvidenceItem] = Field(default_factory=list, description="证据片段列表")
    matched_projects: list[str] = Field(default_factory=list, description="命中的项目名")
    matched_skills: list[str] = Field(default_factory=list, description="命中的技能")
    retrieval_reason: str = Field(default="", description="检索结果说明")


class _ResumeChunk(BaseModel):
    """简历语义块。"""

    model_config = ConfigDict(extra="forbid")

    section_type: Literal["project", "work", "skill", "education", "general"]
    section_title: str
    project_name: str | None = None
    snippet: str


def resume_evidence_tool(**kwargs: Any) -> ResumeEvidenceToolResult:
    """从简历中检索最值得深挖的证据片段。"""

    try:
        tool_input = ResumeEvidenceToolInput.model_validate(kwargs)
        if not tool_input.resume_markdown.strip():
            logger.info("resume evidence retrieval skipped reason=resume_missing")
            return ResumeEvidenceToolResult(
                found=False,
                retrieval_reason="resume_missing",
            )

        chunks = _split_resume_into_chunks(tool_input.resume_markdown)
        if not chunks:
            logger.info("resume evidence retrieval skipped reason=resume_chunk_empty")
            return ResumeEvidenceToolResult(
                found=False,
                retrieval_reason="resume_chunk_empty",
            )

        semantic_query_segments = _collect_semantic_query_segments(tool_input)
        lexical_query_segments = _collect_lexical_query_segments(tool_input)
        query_text = _build_query_text(semantic_query_segments)
        display_terms = _build_display_terms(tool_input, semantic_query_segments)
        query_tokens = _build_weighted_query_tokens(lexical_query_segments)
        logger.info(
            "resume evidence retrieval started category_key={}, chunk_count={}, top_k={}, focus_topics={}, missing_signals={}, semantic_query_preview={}, lexical_query_preview={}",
            tool_input.category_key,
            len(chunks),
            tool_input.top_k,
            tool_input.focus_topics,
            tool_input.missing_signals,
            _preview_text(query_text),
            _preview_text(_build_query_text(lexical_query_segments)),
        )
        logger.debug(
            "resume evidence retrieval detail resume_file={}, display_terms={}, query_tokens={}, semantic_query_segments={}, lexical_query_segments={}, chunk_digest={}",
            tool_input.resume_metadata.get("original_file_name") or tool_input.resume_metadata.get("file_name"),
            display_terms[:16],
            query_tokens[:24],
            semantic_query_segments[:8],
            lexical_query_segments[:8],
            _build_chunk_debug_digest(chunks),
        )
        ranked_items, retrieval_reason = _rank_chunks(
            chunks=chunks,
            tool_input=tool_input,
            query_text=query_text,
            query_tokens=query_tokens,
            display_terms=display_terms,
        )
        if not ranked_items:
            logger.info(
                "resume evidence retrieval completed found=false retrieval_reason={} query_preview={}",
                "no_relevant_evidence",
                _preview_text(query_text),
            )
            return ResumeEvidenceToolResult(
                found=False,
                retrieval_reason="no_relevant_evidence",
            )

        top_items = ranked_items[: tool_input.top_k]
        matched_projects = _unique_list(
            [
                item["chunk"].project_name
                for item in top_items
                if item["chunk"].project_name
            ]
        )
        matched_skills = _extract_matched_skills(display_terms, top_items)
        result = ResumeEvidenceToolResult(
            found=True,
            items=[
                ResumeEvidenceItem(
                    snippet=item["chunk"].snippet,
                    source_section=item["chunk"].section_title,
                    matched_terms=item["matched_terms"],
                    relevance_score=round(item["fused_score"], 3),
                )
                for item in top_items
            ],
            matched_projects=matched_projects,
            matched_skills=matched_skills,
            retrieval_reason=retrieval_reason,
        )
        logger.info(
            "resume evidence retrieval completed found=true retrieval_reason={} top_items={}",
            retrieval_reason,
            [
                {
                    "section": item["chunk"].section_title,
                    "bm25": round(item["bm25_raw"], 4),
                    "semantic": round(item["semantic_raw"], 4),
                    "fused": round(item["fused_score"], 4),
                    "matched_terms": item["matched_terms"][:4],
                }
                for item in top_items
            ],
        )
        return result
    except Exception as exc:
        logger.exception("resume evidence tool failed: error={}", exc)
        return ResumeEvidenceToolResult(
            found=False,
            retrieval_reason="tool_execution_failed",
        )


def _build_query_text(query_segments: list[str]) -> str:
    """构建当前轮追问检索使用的查询文本。"""

    return "\n".join(part for part in query_segments if part)


def _collect_semantic_query_segments(tool_input: ResumeEvidenceToolInput) -> list[str]:
    """构建供语义检索使用的完整查询片段。"""

    segments: list[str] = []
    if tool_input.question_text.strip():
        segments.append(tool_input.question_text.strip())
    segments.extend(item.strip() for item in tool_input.focus_topics if item.strip())
    segments.extend(item.strip() for item in tool_input.missing_signals if item.strip())
    if tool_input.answer_text.strip():
        segments.append(tool_input.answer_text.strip())
    return segments


def _collect_lexical_query_segments(tool_input: ResumeEvidenceToolInput) -> list[str]:
    """构建供 BM25 使用的稀疏查询片段，仅保留追问焦点与缺失信号。"""

    segments: list[str] = []
    segments.extend(item.strip() for item in tool_input.focus_topics if item.strip())
    segments.extend(item.strip() for item in tool_input.missing_signals if item.strip())
    return _unique_list(segments)


def _split_resume_into_chunks(resume_markdown: str) -> list[_ResumeChunk]:
    """按 Markdown 章节与段落拆分简历。"""

    lines = [line.rstrip() for line in resume_markdown.splitlines()]
    chunks: list[_ResumeChunk] = []
    current_title = "简历概览"
    current_section_type: Literal["project", "work", "skill", "education", "general"] = "general"
    buffer: list[str] = []

    def _flush_buffer() -> None:
        nonlocal buffer
        normalized_lines = [line.strip() for line in buffer if line.strip()]
        if not normalized_lines:
            buffer = []
            return
        snippet = "\n".join(normalized_lines)
        project_name = _extract_project_name(current_title, normalized_lines)
        chunks.append(
            _ResumeChunk(
                section_type=current_section_type,
                section_title=current_title,
                project_name=project_name,
                snippet=snippet,
            )
        )
        buffer = []

    for line in lines:
        header_match = _HEADER_PATTERN.match(line.strip())
        if header_match:
            _flush_buffer()
            current_title = header_match.group("title").strip()
            current_section_type = _detect_section_type(current_title)
            continue

        if not line.strip():
            _flush_buffer()
            continue

        if _BULLET_PATTERN.match(line) and buffer:
            _flush_buffer()
        buffer.append(line)

    _flush_buffer()
    return chunks


def _detect_section_type(section_title: str) -> Literal["project", "work", "skill", "education", "general"]:
    """根据章节标题推断 section 类型。"""

    title_lower = section_title.lower()
    for section_type, aliases in _SECTION_ALIASES:
        if any(alias in title_lower for alias in aliases):
            return section_type  # type: ignore[return-value]
    return "general"


def _extract_project_name(section_title: str, lines: list[str]) -> str | None:
    """尽量提取项目名称。"""

    if _detect_section_type(section_title) == "project":
        return section_title
    for line in lines[:2]:
        normalized = line.strip(" -*")
        if normalized and any(marker in normalized for marker in ("项目", "Project", "project")):
            return normalized[:80]
    return None


def _build_display_terms(
    tool_input: ResumeEvidenceToolInput,
    query_segments: list[str],
) -> list[str]:
    """构建用于命中展示和日志的查询词列表。"""

    raw_terms: list[str] = []
    raw_terms.extend(tool_input.focus_topics)
    raw_terms.extend(tool_input.missing_signals)
    raw_terms.extend(_extract_terms_from_text(tool_input.question_text))
    raw_terms.extend(_extract_terms_from_text(tool_input.answer_text))
    for segment in query_segments:
        raw_terms.extend(_extract_terms_from_text(segment))
    return _unique_list(
        [
            term.strip()
            for term in raw_terms
            if term and len(term.strip()) >= 2 and term.strip().lower() not in _STOP_TERMS
        ]
    )


def _build_weighted_query_tokens(query_segments: list[str]) -> list[str]:
    """构建带权重的 BM25 查询词，仅对短语做稀疏展开。"""

    weighted_tokens: list[str] = []
    for segment in query_segments:
        segment_tokens = _tokenize_text_for_bm25(segment, include_ngram=False)
        weighted_tokens.extend(segment_tokens)
        weighted_tokens.extend(segment_tokens)
    return [token for token in weighted_tokens if token]


def _extract_terms_from_text(text: str) -> list[str]:
    """提取中英文候选词与短语。"""

    if not text.strip():
        return []
    normalized = re.sub(r"[^\w\u4e00-\u9fff#+./-]+", " ", text.lower())
    tokens = [token for token in normalized.split() if token]
    phrases: list[str] = []
    for token in tokens:
        if len(token) >= 2 and token not in _STOP_TERMS:
            phrases.append(token)
    for match in re.findall(r"[\u4e00-\u9fff]{2,12}", text):
        phrases.append(match)
    return phrases


def _tokenize_text_for_bm25(text: str, *, include_ngram: bool = True) -> list[str]:
    """为 BM25 构建 token，query 侧可关闭 n-gram 以减少噪声。"""

    tokens = _extract_terms_from_text(text)
    extra_tokens: list[str] = []
    if include_ngram:
        for segment in re.findall(r"[\u4e00-\u9fff]{2,16}", text):
            segment = segment.strip()
            if len(segment) > 2:
                extra_tokens.extend(segment[index : index + 2] for index in range(len(segment) - 1))
            if len(segment) > 3:
                extra_tokens.extend(segment[index : index + 3] for index in range(len(segment) - 2))
    normalized = [token.lower() for token in tokens + extra_tokens if token]
    return [token for token in normalized if token not in _STOP_TERMS]


def _compute_bm25_scores(
    chunks: list[_ResumeChunk],
    query_tokens: list[str],
) -> tuple[list[float], dict[str, Any]]:
    """对 chunk 列表计算 BM25 分数。"""

    corpus_tokens = [_tokenize_text_for_bm25(_build_chunk_text(chunk), include_ngram=True) for chunk in chunks]
    if not query_tokens:
        return [0.0 for _ in chunks], {"positive_hits": 0, "top_candidates": []}

    document_count = len(corpus_tokens)
    avgdl = sum(len(tokens) for tokens in corpus_tokens) / max(document_count, 1)
    document_frequencies: Counter[str] = Counter()
    for tokens in corpus_tokens:
        document_frequencies.update(set(tokens))

    scores: list[float] = []
    for tokens in corpus_tokens:
        token_counter = Counter(tokens)
        document_length = len(tokens) or 1
        score = 0.0
        for query_token in query_tokens:
            frequency = token_counter.get(query_token, 0)
            if frequency <= 0:
                continue
            df = document_frequencies.get(query_token, 0)
            idf = math.log(1 + (document_count - df + 0.5) / (df + 0.5))
            denominator = frequency + _BM25_K1 * (
                1 - _BM25_B + _BM25_B * document_length / max(avgdl, 1e-6)
            )
            score += idf * frequency * (_BM25_K1 + 1) / denominator
        scores.append(score)

    top_candidates = sorted(
        [
            {
                "section": chunk.section_title,
                "score": round(score, 4),
            }
            for chunk, score in zip(chunks, scores, strict=False)
            if score > 0
        ],
        key=lambda item: -item["score"],
    )[:3]
    return scores, {
        "positive_hits": sum(1 for score in scores if score > 0),
        "top_candidates": top_candidates,
    }


def _score_chunks_with_semantic(
    chunks: list[_ResumeChunk],
    query_text: str,
) -> tuple[list[float] | None, dict[str, Any]]:
    """为 chunk 计算语义相似度分数。"""

    if not query_text.strip():
        return None, {"enabled": False, "reason": "empty_query"}

    try:
        embedding_backend = _get_embedding_backend()
    except Exception as exc:
        logger.warning("resume evidence semantic scoring unavailable stage=backend_init error={}", exc)
        return None, {"enabled": False, "reason": "backend_init_failed", "error": str(exc)}

    try:
        chunk_texts = [_build_chunk_text(chunk) for chunk in chunks]
        query_vector, query_cache_hit = _embed_query_with_cache(query_text, embedding_backend)
        chunk_vectors, cache_hits, cache_misses = _embed_documents_with_cache(chunk_texts, embedding_backend)
        scores = [_cosine_similarity(query_vector, chunk_vector) for chunk_vector in chunk_vectors]
        top_candidates = sorted(
            [
                {
                    "section": chunk.section_title,
                    "score": round(score, 4),
                }
                for chunk, score in zip(chunks, scores, strict=False)
            ],
            key=lambda item: -item["score"],
        )[:3]
        return scores, {
            "enabled": True,
            "query_cache_hit": query_cache_hit,
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "top_candidates": top_candidates,
        }
    except Exception as exc:
        logger.warning("resume evidence semantic scoring unavailable stage=embedding error={}", exc)
        return None, {"enabled": False, "reason": "embedding_failed", "error": str(exc)}


def _get_embedding_backend() -> Any:
    """懒加载 embedding backend，避免模块导入时就触发外部依赖。"""

    from app.services.vector_embedding_service import vector_embedding_service

    return vector_embedding_service


def _embed_query_with_cache(query_text: str, embedding_backend: Any) -> tuple[list[float], bool]:
    """查询向量缓存。"""

    cache_key = _hash_text(query_text)
    if cache_key in _QUERY_EMBEDDING_CACHE:
        return _QUERY_EMBEDDING_CACHE[cache_key], True

    vector = embedding_backend.embed_query(query_text)
    _QUERY_EMBEDDING_CACHE[cache_key] = vector
    return vector, False


def _embed_documents_with_cache(
    texts: list[str],
    embedding_backend: Any,
) -> tuple[list[list[float]], int, int]:
    """文档向量缓存，保持与输入顺序一致。"""

    cache_hits = 0
    cache_misses = 0
    missing_texts: list[str] = []
    missing_keys: list[str] = []
    vectors_by_key: dict[str, list[float]] = {}

    for text in texts:
        cache_key = _hash_text(text)
        if cache_key in _SEMANTIC_CACHE:
            vectors_by_key[cache_key] = _SEMANTIC_CACHE[cache_key]
            cache_hits += 1
            continue
        missing_texts.append(text)
        missing_keys.append(cache_key)
        cache_misses += 1

    if missing_texts:
        embedded_vectors = embedding_backend.embed_documents(missing_texts)
        for cache_key, vector in zip(missing_keys, embedded_vectors, strict=False):
            _SEMANTIC_CACHE[cache_key] = vector
            vectors_by_key[cache_key] = vector

    ordered_vectors = [vectors_by_key[_hash_text(text)] for text in texts]
    return ordered_vectors, cache_hits, cache_misses


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    """计算余弦相似度。"""

    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(left_item * right_item for left_item, right_item in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _rank_chunks(
    *,
    chunks: list[_ResumeChunk],
    tool_input: ResumeEvidenceToolInput,
    query_text: str,
    query_tokens: list[str],
    display_terms: list[str],
) -> tuple[list[dict[str, Any]], str]:
    """基于 BM25 与语义分数融合排序。"""

    bm25_scores, bm25_debug = _compute_bm25_scores(chunks, query_tokens)
    semantic_scores, semantic_debug = _score_chunks_with_semantic(chunks, query_text)
    normalized_bm25_scores = _normalize_scores(bm25_scores)
    normalized_semantic_scores = _normalize_scores(semantic_scores or [])

    logger.info(
        "resume evidence bm25 summary positive_hits={} top_candidates={}",
        bm25_debug["positive_hits"],
        bm25_debug["top_candidates"],
    )
    logger.debug(
        "resume evidence bm25 detail query_token_count={} query_token_sample={} top_candidates={}",
        len(query_tokens),
        query_tokens[:20],
        bm25_debug["top_candidates"],
    )
    logger.info(
        "resume evidence semantic summary enabled={} cache_hits={} cache_misses={} top_candidates={}",
        semantic_debug.get("enabled", False),
        semantic_debug.get("cache_hits", 0),
        semantic_debug.get("cache_misses", 0),
        semantic_debug.get("top_candidates", []),
    )
    logger.debug(
        "resume evidence semantic detail enabled={} reason={} query_cache_hit={} cache_hits={} cache_misses={} top_candidates={}",
        semantic_debug.get("enabled", False),
        semantic_debug.get("reason", ""),
        semantic_debug.get("query_cache_hit", False),
        semantic_debug.get("cache_hits", 0),
        semantic_debug.get("cache_misses", 0),
        semantic_debug.get("top_candidates", []),
    )

    ranked_items: list[dict[str, Any]] = []
    semantic_max = max(semantic_scores or [0.0]) if semantic_scores else 0.0
    semantic_threshold = max(_SEMANTIC_CUTOFF, semantic_max * 0.78)
    for index, chunk in enumerate(chunks):
        haystack = _build_chunk_text(chunk).lower()
        matched_terms = _collect_matched_terms(display_terms, haystack)
        bm25_raw = bm25_scores[index]
        semantic_raw = semantic_scores[index] if semantic_scores else 0.0
        is_semantic_candidate = bool(semantic_scores) and semantic_raw >= semantic_threshold
        if bm25_raw <= 0 and not is_semantic_candidate:
            continue

        structure_bonus = _compute_structure_bonus(chunk)
        signal_bonus = _compute_signal_bonus(
            haystack=haystack,
            focus_topics=tool_input.focus_topics,
            missing_signals=tool_input.missing_signals,
        )
        action_bonus = min(sum(1 for term in _ACTION_TERMS if term.lower() in haystack), 3) * 0.03
        result_bonus = min(sum(1 for term in _RESULT_TERMS if term.lower() in haystack), 3) * 0.04
        bm25_norm = normalized_bm25_scores[index] if index < len(normalized_bm25_scores) else 0.0
        semantic_norm = normalized_semantic_scores[index] if index < len(normalized_semantic_scores) else 0.0
        fused_score = (
            _BM25_WEIGHT * bm25_norm
            + _SEMANTIC_WEIGHT * semantic_norm
            + structure_bonus
            + signal_bonus
            + action_bonus
            + result_bonus
        )
        ranked_items.append(
            {
                "chunk": chunk,
                "matched_terms": matched_terms,
                "bm25_raw": bm25_raw,
                "semantic_raw": semantic_raw,
                "fused_score": fused_score,
            }
        )

    ranked_items.sort(
        key=lambda item: (
            -float(item["fused_score"]),
            -float(item["bm25_raw"]),
            -float(item["semantic_raw"]),
            0 if item["chunk"].section_type in {"project", "work"} else 1,
        )
    )
    ranked_items = _deduplicate_ranked_items_by_section(ranked_items)
    logger.debug(
        "resume evidence ranking detail semantic_threshold={} semantic_max={} ranked_items={}",
        round(semantic_threshold, 4),
        round(semantic_max, 4),
        _build_ranked_item_debug_summary(ranked_items),
    )
    retrieval_reason = "hybrid_bm25_embedding" if semantic_debug.get("enabled") else "bm25_only"
    return ranked_items, retrieval_reason


def _compute_structure_bonus(chunk: _ResumeChunk) -> float:
    """给更适合追问的章节类型更高权重。"""

    if chunk.section_type == "project":
        return 0.12
    if chunk.section_type == "work":
        return 0.08
    if chunk.section_type == "skill":
        return 0.03
    return 0.01


def _compute_signal_bonus(
    *,
    haystack: str,
    focus_topics: list[str],
    missing_signals: list[str],
) -> float:
    """给当前轮重点关注的主题额外加权。"""

    focus_bonus = 0.06 if any(topic.lower() in haystack for topic in focus_topics if topic.strip()) else 0.0
    missing_bonus = (
        0.08 if any(signal.lower() in haystack for signal in missing_signals if signal.strip()) else 0.0
    )
    return focus_bonus + missing_bonus


def _collect_matched_terms(display_terms: list[str], haystack: str) -> list[str]:
    """收集真正命中的展示词。"""

    matched_terms = [term for term in display_terms if term.lower() in haystack]
    return _unique_list(matched_terms)


def _normalize_scores(scores: list[float]) -> list[float]:
    """把原始分数缩放到 0-1。"""

    if not scores:
        return []
    maximum = max(scores)
    minimum = min(scores)
    if math.isclose(maximum, minimum):
        return [1.0 if maximum > 0 else 0.0 for _ in scores]
    return [(score - minimum) / (maximum - minimum) for score in scores]


def _build_chunk_text(chunk: _ResumeChunk) -> str:
    """构建一个 chunk 的统一检索文本。"""

    return f"{chunk.section_title}\n{chunk.snippet}"


def _extract_matched_skills(
    display_terms: list[str],
    ranked_items: list[dict[str, Any]],
) -> list[str]:
    """从命中片段中提取技能词。"""

    skill_terms: list[str] = []
    for item in ranked_items:
        chunk = item["chunk"]
        if chunk.section_type == "skill":
            skill_terms.extend(item["matched_terms"])
            continue
        for term in item["matched_terms"]:
            if re.search(r"[a-zA-Z#+./-]{2,}", term):
                skill_terms.append(term)
    return _unique_list([term for term in skill_terms if term in display_terms])


def _preview_text(text: str, max_length: int = 160) -> str:
    """压缩长文本，便于日志观察。"""

    preview = text.replace("\n", " ").strip()
    if len(preview) <= max_length:
        return preview
    return preview[:max_length] + "..."


def _build_chunk_debug_digest(chunks: list[_ResumeChunk], limit: int = 8) -> list[dict[str, Any]]:
    """构建简化后的 chunk 诊断信息。"""

    return [
        {
            "section": chunk.section_title,
            "section_type": chunk.section_type,
            "project_name": chunk.project_name,
            "preview": _preview_text(chunk.snippet, max_length=80),
        }
        for chunk in chunks[:limit]
    ]


def _build_ranked_item_debug_summary(
    ranked_items: list[dict[str, Any]],
    limit: int = 6,
) -> list[dict[str, Any]]:
    """输出排序后的关键信息，方便判断命中是否合理。"""

    summary: list[dict[str, Any]] = []
    for item in ranked_items[:limit]:
        chunk = item["chunk"]
        summary.append(
            {
                "section": chunk.section_title,
                "section_type": chunk.section_type,
                "project_name": chunk.project_name,
                "bm25": round(item["bm25_raw"], 4),
                "semantic": round(item["semantic_raw"], 4),
                "fused": round(item["fused_score"], 4),
                "matched_terms": item["matched_terms"][:6],
                "preview": _preview_text(chunk.snippet, max_length=80),
            }
        )
    return summary


def _deduplicate_ranked_items_by_section(
    ranked_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按 section 去重，避免同一项目块重复占用 top_k。"""

    deduplicated: list[dict[str, Any]] = []
    seen_sections: set[str] = set()
    for item in ranked_items:
        section = str(item["chunk"].section_title).strip()
        if section in seen_sections:
            continue
        seen_sections.add(section)
        deduplicated.append(item)
    return deduplicated


def _hash_text(text: str) -> str:
    """为缓存生成稳定键。"""

    return sha256(text.encode("utf-8")).hexdigest()


def _unique_list(values: list[Any]) -> list[Any]:
    """按顺序去重。"""

    result: list[Any] = []
    seen: set[Any] = set()
    for value in values:
        normalized = value.strip() if isinstance(value, str) else value
        if normalized in ("", None):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


__all__ = [
    "ResumeEvidenceItem",
    "ResumeEvidenceToolInput",
    "ResumeEvidenceToolResult",
    "resume_evidence_tool",
]
