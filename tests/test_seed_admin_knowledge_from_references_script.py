"""Phase 3 管理员知识库种子脚本的最小验证。"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
from pathlib import Path
import sys
from typing import Any

from app.config import config
from app.models.knowledge import KnowledgeBaseEntity, KnowledgeDocumentEntity
from app.services.knowledge_service import KnowledgeUploadResult
from app.services.skill_service import PRESET_SKILL_IDS, SkillService


def _load_script_module():
    """按文件路径加载种子脚本模块。"""

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "seed_admin_knowledge_from_references.py"
    spec = importlib.util.spec_from_file_location("seed_admin_knowledge_from_references", script_path)
    if spec is None or spec.loader is None:
        raise AssertionError("无法加载 scripts/seed_admin_knowledge_from_references.py")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeSeedRepository:
    """只保留脚本所需接口的简化仓储。"""

    def __init__(self) -> None:
        self.knowledge_bases_by_scope: dict[tuple[str, str, str | None, str | None], KnowledgeBaseEntity] = {}
        self.knowledge_bases_by_id: dict[str, KnowledgeBaseEntity] = {}
        self.documents_by_kb: dict[str, list[KnowledgeDocumentEntity]] = {}

    async def find_knowledge_base_by_name_and_scope(
        self,
        *,
        name: str,
        category: str,
        skill_id: str | None,
        source_type: str | None = None,
        only_enabled: bool = False,
    ) -> KnowledgeBaseEntity | None:
        _ = only_enabled
        return self.knowledge_bases_by_scope.get((name, category, skill_id, source_type))

    async def list_documents_by_knowledge_base(
        self,
        knowledge_base_id: str,
        index_status: Any = None,
        limit: int = 50,
    ) -> list[KnowledgeDocumentEntity]:
        _ = index_status, limit
        return list(self.documents_by_kb.get(knowledge_base_id, []))


class _FakeAdminKnowledgeService:
    """记录创建与追加调用的假管理员服务。"""

    def __init__(self, repository: _FakeSeedRepository) -> None:
        self._repository = repository
        self.create_calls: list[dict[str, Any]] = []
        self.add_calls: list[dict[str, Any]] = []
        self._knowledge_base_seq = 0
        self._document_seq = 0

    async def create_knowledge_base(
        self,
        session: Any,
        *,
        name: str,
        category: str,
        description: str | None = None,
        skill_id: str | None = None,
    ) -> KnowledgeBaseEntity:
        _ = session
        self.create_calls.append(
            {
                "name": name,
                "category": category,
                "description": description,
                "skill_id": skill_id,
            }
        )
        self._knowledge_base_seq += 1
        entity = KnowledgeBaseEntity(
            id=f"kb-{self._knowledge_base_seq}",
            name=name,
            description=description,
            category=category,
            skill_id=skill_id,
            source_type="admin",
            is_enabled=True,
            metadata_json={"document_count": 0, "doc_type": "domain_corpus"},
        )
        self._repository.knowledge_bases_by_scope[(name, category, skill_id, "admin")] = entity
        self._repository.knowledge_bases_by_id[entity.id] = entity
        self._repository.documents_by_kb.setdefault(entity.id, [])
        return entity

    async def add_document_to_knowledge_base(
        self,
        session: Any,
        *,
        knowledge_base_id: str,
        file_name: str,
        file_content: bytes,
        content_type: str | None = None,
    ) -> KnowledgeUploadResult:
        _ = session, content_type
        self.add_calls.append(
            {
                "knowledge_base_id": knowledge_base_id,
                "file_name": file_name,
                "content_hash": hashlib.sha256(file_content).hexdigest(),
            }
        )
        self._document_seq += 1
        knowledge_base = self._repository.knowledge_bases_by_id[knowledge_base_id]
        document = KnowledgeDocumentEntity(
            id=f"doc-{self._document_seq}",
            knowledge_base_id=knowledge_base_id,
            original_file_name=file_name,
            storage_path=f"/tmp/{file_name}",
            file_extension=Path(file_name).suffix,
            mime_type="text/markdown" if file_name.endswith(".md") else "text/plain",
            file_size=len(file_content),
            source_type=knowledge_base.source_type,
            category=knowledge_base.category,
            skill_id=knowledge_base.skill_id,
            is_enabled=True,
            metadata_json={
                "doc_type": "domain_corpus",
                "content_hash": hashlib.sha256(file_content).hexdigest(),
            },
        )
        self._repository.documents_by_kb.setdefault(knowledge_base_id, []).append(document)
        knowledge_base.metadata_json = {
            **dict(knowledge_base.metadata_json or {}),
            "document_count": int(knowledge_base.metadata_json.get("document_count", 0) or 0) + 1,
        }
        return KnowledgeUploadResult(
            knowledge_base_id=knowledge_base_id,
            document_id=document.id,
            name=knowledge_base.name,
            category=document.category,
            source_type=document.source_type,
            skill_id=document.skill_id,
            file_name=file_name,
            file_size=len(file_content),
            index_status="indexed",
            chunk_count=1,
        )


def test_seed_script_builds_plans_from_existing_skill_references() -> None:
    """种子计划应按 preset skill 读取现成 reference 资料。"""

    module = _load_script_module()
    skill_service = SkillService(root_dir=config.skill_root_dir)

    plans = module.build_seed_knowledge_base_plans(skill_service)

    assert [plan.skill_id for plan in plans] == list(PRESET_SKILL_IDS)

    python_plan = next(plan for plan in plans if plan.skill_id == "python-backend")
    assert python_plan.category == "reference_knowledge"
    assert [document.file_name for document in python_plan.documents] == [
        "python-basic.md",
        "database.md",
        "django-flask.md",
        "redis.md",
        "system-design-scenarios.md",
    ]

    algorithm_plan = next(plan for plan in plans if plan.skill_id == "algorithm")
    assert [document.file_name for document in algorithm_plan.documents] == [
        "algorithm-data-structures.md",
    ]


def test_seed_script_is_idempotent_for_repeated_plan_execution() -> None:
    """同一份种子计划重复执行时，不应重复创建知识库和文档。"""

    module = _load_script_module()
    skill_service = SkillService(root_dir=config.skill_root_dir)
    plan = next(
        item for item in module.build_seed_knowledge_base_plans(skill_service)
        if item.skill_id == "python-backend"
    )

    repository = _FakeSeedRepository()
    admin_service = _FakeAdminKnowledgeService(repository)
    session = object()

    first_run = asyncio.run(module.seed_knowledge_base_for_plan(session, repository, admin_service, plan))
    second_run = asyncio.run(module.seed_knowledge_base_for_plan(session, repository, admin_service, plan))

    assert first_run.created is True
    assert first_run.created_documents == len(plan.documents)
    assert first_run.skipped_documents == 0
    assert second_run.created is False
    assert second_run.created_documents == 0
    assert second_run.skipped_documents == len(plan.documents)
    assert len(admin_service.create_calls) == 1
    assert len(admin_service.add_calls) == len(plan.documents)
