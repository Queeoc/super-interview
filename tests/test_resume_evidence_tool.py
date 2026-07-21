"""resume_evidence_tool 的单元测试。"""

from __future__ import annotations

import importlib
from typing import Any

from loguru import logger
import pytest

from app.tools.resume_evidence_tool import resume_evidence_tool

resume_tool_module = importlib.import_module("app.tools.resume_evidence_tool")


class _FakeEmbeddings:
    """测试用 embedding 后端，避免依赖真实外部服务。"""

    def __init__(self, query_vectors: dict[str, list[float]], document_vectors: dict[str, list[float]]) -> None:
        self._query_vectors = query_vectors
        self._document_vectors = document_vectors

    def embed_query(self, text: str) -> list[float]:
        return self._query_vectors.get(text, [0.1, 0.1, 0.1])

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._document_vectors.get(text, [0.1, 0.1, 0.1]) for text in texts]


@pytest.fixture(autouse=True)
def _clear_embedding_cache() -> None:
    """每个测试前清理工具内缓存，避免互相污染。"""

    resume_tool_module._SEMANTIC_CACHE.clear()
    resume_tool_module._QUERY_EMBEDDING_CACHE.clear()


def test_resume_evidence_tool_prefers_project_experience_over_skill_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """项目经历命中时，应优先返回项目/工作证据而不是纯技能列表。"""

    resume_markdown = """
# 项目经历
## 分布式缓存平台项目
- 负责设计 Redis 多级缓存方案，优化热点 key 处理与失效策略。
- 通过缓存预热和一致性补偿机制，将接口 P99 延迟降低 35%。

# 技能清单
- Redis / MySQL / Python / FastAPI
"""

    query_text = "请讲讲你如何设计缓存失效策略\nredis\n失效策略\nresults\ntradeoffs\n我做过一些 Redis 优化。"
    project_chunk = (
        "分布式缓存平台项目\n"
        "负责设计 Redis 多级缓存方案，优化热点 key 处理与失效策略。"
    )
    skill_chunk = "技能清单\nRedis / MySQL / Python / FastAPI"
    fake_backend = _FakeEmbeddings(
        query_vectors={query_text: [1.0, 0.0, 0.0]},
        document_vectors={
            project_chunk: [0.95, 0.05, 0.0],
            skill_chunk: [0.35, 0.65, 0.0],
        },
    )
    monkeypatch.setattr(resume_tool_module, "_get_embedding_backend", lambda: fake_backend)

    result = resume_evidence_tool(
        resume_markdown=resume_markdown,
        resume_metadata={},
        category_key="CACHE",
        question_text="请讲讲你如何设计缓存失效策略",
        answer_text="我做过一些 Redis 优化。",
        focus_topics=["redis", "失效策略"],
        missing_signals=["results", "tradeoffs"],
        top_k=2,
    )

    assert result.found is True
    assert result.retrieval_reason == "hybrid_bm25_embedding"
    assert result.items
    assert result.items[0].source_section == "分布式缓存平台项目"
    assert "redis" in [term.lower() for term in result.items[0].matched_terms]
    assert result.matched_projects == ["分布式缓存平台项目"]


def test_resume_evidence_tool_uses_focus_topics_and_missing_signals_for_ranking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """focus_topics 与 missing_signals 应影响排序。"""

    resume_markdown = """
# 工作经历
## 后端开发
- 负责订单服务接口开发。

# 项目经历
## 数据库优化项目
- 排查慢 SQL，优化索引和查询计划，将核心接口响应时间缩短 60%。
"""

    query_text = "你如何处理慢查询问题？\n慢 SQL\n索引\n结果\ntradeoff\n我做过一些数据库优化。"
    work_chunk = "后端开发\n负责订单服务接口开发。"
    project_chunk = "数据库优化项目\n排查慢 SQL，优化索引和查询计划，将核心接口响应时间缩短 60%。"
    fake_backend = _FakeEmbeddings(
        query_vectors={query_text: [1.0, 1.0, 0.0]},
        document_vectors={
            work_chunk: [0.25, 0.2, 0.0],
            project_chunk: [0.95, 0.9, 0.0],
        },
    )
    monkeypatch.setattr(resume_tool_module, "_get_embedding_backend", lambda: fake_backend)

    result = resume_evidence_tool(
        resume_markdown=resume_markdown,
        resume_metadata={},
        category_key="DB",
        question_text="你如何处理慢查询问题？",
        answer_text="我做过一些数据库优化。",
        focus_topics=["慢 SQL", "索引"],
        missing_signals=["结果", "tradeoff"],
        top_k=1,
    )

    assert result.found is True
    assert len(result.items) == 1
    assert result.items[0].source_section == "数据库优化项目"
    assert result.items[0].relevance_score > 0


def test_resume_evidence_tool_returns_stable_empty_result_without_resume() -> None:
    """空简历应返回稳定空结果。"""

    result = resume_evidence_tool(
        resume_markdown="",
        resume_metadata={},
        category_key="CACHE",
        question_text="请介绍缓存方案",
        answer_text="我了解 Redis。",
        focus_topics=["redis"],
        missing_signals=[],
        top_k=2,
    )

    assert result.found is False
    assert result.items == []
    assert result.retrieval_reason == "resume_missing"


def test_resume_evidence_tool_falls_back_to_bm25_when_embedding_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """embedding 不可用时，应稳定降级为 bm25_only。"""

    monkeypatch.setattr(
        resume_tool_module,
        "_get_embedding_backend",
        lambda: (_ for _ in ()).throw(RuntimeError("embedding backend unavailable")),
    )

    resume_markdown = """
# 项目经历
## API 网关重构
- 负责实现 Python API gateway 限流与缓存改造，提升 throughput，降低 latency。
- 最终将峰值 QPS 提升到原来的 2 倍，并减少 40% 超时告警。
"""

    result = resume_evidence_tool(
        resume_markdown=resume_markdown,
        resume_metadata={},
        category_key="SYSTEM_DESIGN",
        question_text="你做过哪些性能优化？",
        answer_text="我做过 API gateway 的性能优化。",
        focus_topics=["python", "gateway", "latency"],
        missing_signals=["结果"],
        top_k=1,
    )

    assert result.found is True
    assert result.retrieval_reason == "bm25_only"
    assert result.items[0].source_section == "API 网关重构"
    assert any(term.lower() == "latency" for term in result.items[0].matched_terms)


def test_resume_evidence_tool_emits_debug_logs_for_retrieval_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """debug 日志应包含 query/chunk/ranking 诊断信息，便于排查命中效果。"""

    resume_markdown = """
# 项目经历
## 分布式缓存平台项目
- 负责设计 Redis 多级缓存方案，优化热点 key 处理与失效策略。
- 通过缓存预热和一致性补偿机制，将接口 P99 延迟降低 35%。
"""

    query_text = "请讲讲你如何设计缓存失效策略\nredis\n失效策略\n结果\n我做过一些 Redis 优化。"
    project_chunk = (
        "分布式缓存平台项目\n"
        "负责设计 Redis 多级缓存方案，优化热点 key 处理与失效策略。"
    )
    fake_backend = _FakeEmbeddings(
        query_vectors={query_text: [1.0, 0.0, 0.0]},
        document_vectors={project_chunk: [0.96, 0.04, 0.0]},
    )
    monkeypatch.setattr(resume_tool_module, "_get_embedding_backend", lambda: fake_backend)

    records: list[str] = []
    handler_id = logger.add(
        lambda message: records.append(str(message.record["message"])),
        level="DEBUG",
    )
    try:
        result = resume_evidence_tool(
            resume_markdown=resume_markdown,
            resume_metadata={"original_file_name": "resume.md"},
            category_key="CACHE",
            question_text="请讲讲你如何设计缓存失效策略",
            answer_text="我做过一些 Redis 优化。",
            focus_topics=["redis", "失效策略"],
            missing_signals=["结果"],
            top_k=1,
        )
    finally:
        logger.remove(handler_id)

    assert result.found is True
    assert any("resume evidence retrieval detail" in record for record in records)
    assert any("resume evidence bm25 detail" in record for record in records)
    assert any("resume evidence ranking detail" in record for record in records)


def test_resume_evidence_tool_deduplicates_same_section_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一 section 的多个 chunk 不应重复占用 top_k。"""

    resume_markdown = """
# 项目经历
## 秒杀系统项目
- 负责 Redis 预扣库存和原子 decr 设计。
- 通过消息队列异步下单，削峰并避免超卖。
- 增加幂等和补偿逻辑，保证一致性。
"""

    query_text = "请介绍你的秒杀系统设计\n缓存击穿\n一致性保证\n我用 Redis 和消息队列做过秒杀系统。"
    fake_backend = _FakeEmbeddings(
        query_vectors={query_text: [1.0, 0.0, 0.0]},
        document_vectors={
            "秒杀系统项目\n负责 Redis 预扣库存和原子 decr 设计。": [0.95, 0.01, 0.0],
            "秒杀系统项目\n通过消息队列异步下单，削峰并避免超卖。": [0.93, 0.02, 0.0],
            "秒杀系统项目\n增加幂等和补偿逻辑，保证一致性。": [0.92, 0.03, 0.0],
        },
    )
    monkeypatch.setattr(resume_tool_module, "_get_embedding_backend", lambda: fake_backend)

    result = resume_evidence_tool(
        resume_markdown=resume_markdown,
        resume_metadata={},
        category_key="SYSTEM_DESIGN",
        question_text="请介绍你的秒杀系统设计",
        answer_text="我用 Redis 和消息队列做过秒杀系统。",
        focus_topics=["缓存击穿", "一致性保证"],
        missing_signals=["方案"],
        top_k=3,
    )

    assert result.found is True
    assert len(result.items) == 1
    assert result.items[0].source_section == "秒杀系统项目"


def test_resume_evidence_tool_lexical_query_comes_from_focus_and_missing_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BM25 query token 不应再由整段 question_text 暴力扩展。"""

    captured: dict[str, Any] = {}

    def _record_scores(chunks: list[Any], query_tokens: list[str]) -> tuple[list[float], dict[str, Any]]:
        captured["query_tokens"] = list(query_tokens)
        return [1.0 for _ in chunks], {"positive_hits": len(chunks), "top_candidates": []}

    monkeypatch.setattr(resume_tool_module, "_compute_bm25_scores", _record_scores)
    monkeypatch.setattr(resume_tool_module, "_score_chunks_with_semantic", lambda chunks, query_text: (None, {"enabled": False, "reason": "disabled"}))

    result = resume_evidence_tool(
        resume_markdown="""
# 项目经历
## 秒杀系统项目
- 负责 Redis 预扣库存和原子 decr 设计。
""",
        resume_metadata={},
        category_key="SYSTEM_DESIGN",
        question_text="假设你需要设计一个秒杀系统，请描述你的设计方案，包括如何处理高并发、避免超卖以及保证一致性。",
        answer_text="我用 Redis 和消息队列做过。",
        focus_topics=["缓存击穿", "消息队列使用"],
        missing_signals=["一致性保证"],
        top_k=1,
    )

    assert result.found is True
    assert captured["query_tokens"]
    joined_tokens = " ".join(captured["query_tokens"])
    assert "缓存击穿" in joined_tokens
    assert "消息队列使用" in joined_tokens
    assert "一致性保证" in joined_tokens
    assert "假设你需要设计一个秒杀系统" not in joined_tokens
