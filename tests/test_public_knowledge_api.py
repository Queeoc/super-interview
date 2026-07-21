"""公开知识库 API 的最小回归测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api import knowledge as knowledge_api
from app.core.database import get_db_session
from app.middleware.error_handler import register_exception_handlers
from app.models.knowledge import KnowledgeBaseEntity, KnowledgeDocumentEntity


def _build_public_api_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    service: Any | None = None,
    rag_service: Any | None = None,
) -> TestClient:
    monkeypatch.setattr(knowledge_api, "knowledge_service", service or SimpleNamespace())
    monkeypatch.setattr(knowledge_api, "rag_service", rag_service or SimpleNamespace())

    app = FastAPI()
    register_exception_handlers(app)

    async def override_get_db_session():
        yield AsyncMock()

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.include_router(knowledge_api.router, prefix="/api")
    return TestClient(app, base_url="https://testserver")


def _build_public_knowledge_base() -> KnowledgeBaseEntity:
    now = datetime.now(timezone.utc)
    return KnowledgeBaseEntity(
        id="kb-public-1",
        name="Python 核心知识库",
        description="Python 面试知识",
        category="reference_knowledge",
        skill_id="python-backend",
        source_type="admin",
        is_enabled=True,
        metadata_json={"document_count": 1},
        created_at=now,
        updated_at=now,
    )


def _build_public_document() -> KnowledgeDocumentEntity:
    now = datetime.now(timezone.utc)
    return KnowledgeDocumentEntity(
        id="doc-public-1",
        knowledge_base_id="kb-public-1",
        original_file_name="python-basic.md",
        storage_path="/tmp/python-basic.md",
        file_extension=".md",
        mime_type="text/markdown",
        file_size=128,
        source_type="admin",
        category="reference_knowledge",
        skill_id="python-backend",
        index_status="indexed",
        is_enabled=True,
        metadata_json={"chunk_count": 2},
        uploaded_at=now,
        indexed_at=now,
        created_at=now,
        updated_at=now,
    )


def test_public_knowledge_api_lists_and_returns_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_base = _build_public_knowledge_base()
    document = _build_public_document()
    service = SimpleNamespace(
        list_public_knowledge_bases=AsyncMock(return_value=[knowledge_base]),
        get_public_knowledge_base_detail=AsyncMock(return_value=(knowledge_base, [document])),
    )
    client = _build_public_api_client(monkeypatch, service=service)

    list_response = client.get("/api/knowledge/bases")
    detail_response = client.get("/api/knowledge/bases/kb-public-1")

    assert list_response.status_code == 200
    assert list_response.json()["data"]["items"][0]["id"] == "kb-public-1"
    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["knowledge_base"]["name"] == "Python 核心知识库"
    assert detail_response.json()["data"]["documents"][0]["original_file_name"] == "python-basic.md"


def test_public_knowledge_api_searches_without_admin_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag_service = SimpleNamespace(
        search=AsyncMock(
            return_value=SimpleNamespace(
                to_dict=lambda: {
                    "query": "Python 解释器",
                    "rewritten_query": "Python 解释器",
                    "used_rewrite": False,
                    "top_k": 5,
                    "score_threshold": None,
                    "hits": [],
                    "message": "没有命中",
                }
            )
        )
    )
    client = _build_public_api_client(monkeypatch, rag_service=rag_service)

    response = client.post(
        "/api/knowledge/search",
        json={
            "query": "Python 解释器",
            "knowledge_base_id": "kb-public-1",
            "top_k": 5,
            "rewrite_enabled": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["query"] == "Python 解释器"
