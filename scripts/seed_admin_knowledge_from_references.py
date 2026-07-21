"""基于 preset skill reference 初始化管理员种子知识库。

运行方式：

    conda run -n biz_agent python scripts/seed_admin_knowledge_from_references.py

本脚本会：
1. 读取 `skills/*/skill.meta.yml` 与对应的 `SKILL.md`
2. 将 skill 中引用的共享 reference 文档收敛成少量逻辑知识库
3. 通过现有知识库服务完成上传、切分、向量化与入库
4. 对重复执行保持幂等：同名同作用域知识库与同名同内容文档会被跳过
"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger

from app.core.database import database_manager
from app.core.milvus_client import milvus_manager
from app.core.storage_client import storage_manager
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.admin_knowledge_service import AdminKnowledgeService, admin_knowledge_service
from app.services.skill_service import PRESET_SKILL_IDS, SkillService
from app.utils.logger import setup_logger


SEED_KNOWLEDGE_CATEGORY = "reference_knowledge"
SEED_KNOWLEDGE_SOURCE_TYPE = "admin"


@dataclass(slots=True)
class SeedReferenceDocumentPlan:
    """单个种子 reference 文档的初始化计划。"""

    file_name: str
    file_path: Path
    title: str
    category_keys: list[str]
    content_hash: str


@dataclass(slots=True)
class SeedKnowledgeBasePlan:
    """单个逻辑知识库的初始化计划。"""

    skill_id: str
    name: str
    category: str
    description: str
    source_type: str
    documents: list[SeedReferenceDocumentPlan] = field(default_factory=list)


@dataclass(slots=True)
class SeedDocumentOutcome:
    """单个文档的种子初始化结果。"""

    file_name: str
    document_id: str | None
    chunk_count: int = 0
    skipped: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_name": self.file_name,
            "document_id": self.document_id,
            "chunk_count": self.chunk_count,
            "skipped": self.skipped,
            "reason": self.reason,
        }


@dataclass(slots=True)
class SeedKnowledgeBaseOutcome:
    """单个知识库的种子初始化结果。"""

    skill_id: str
    knowledge_base_id: str
    knowledge_base_name: str
    created: bool
    document_outcomes: list[SeedDocumentOutcome] = field(default_factory=list)

    @property
    def created_documents(self) -> int:
        return sum(1 for item in self.document_outcomes if not item.skipped)

    @property
    def skipped_documents(self) -> int:
        return sum(1 for item in self.document_outcomes if item.skipped)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "knowledge_base_id": self.knowledge_base_id,
            "knowledge_base_name": self.knowledge_base_name,
            "created": self.created,
            "created_documents": self.created_documents,
            "skipped_documents": self.skipped_documents,
            "document_outcomes": [item.to_dict() for item in self.document_outcomes],
        }


@dataclass(slots=True)
class SeedExecutionReport:
    """脚本总执行结果。"""

    total_knowledge_bases: int
    created_knowledge_bases: int
    reused_knowledge_bases: int
    created_documents: int
    skipped_documents: int
    items: list[SeedKnowledgeBaseOutcome] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_knowledge_bases": self.total_knowledge_bases,
            "created_knowledge_bases": self.created_knowledge_bases,
            "reused_knowledge_bases": self.reused_knowledge_bases,
            "created_documents": self.created_documents,
            "skipped_documents": self.skipped_documents,
            "items": [item.to_dict() for item in self.items],
        }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="初始化管理员种子知识库")
    return parser


def _resolve_reference_path(reference_path: str) -> Path:
    path = Path(reference_path)
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def _sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def build_seed_knowledge_base_plans(skill_service: SkillService | None = None) -> list[SeedKnowledgeBasePlan]:
    """基于 preset skill 的 reference 生成种子计划。"""

    resolved_skill_service = skill_service or SkillService(root_dir=PROJECT_ROOT / "skills")

    plans: list[SeedKnowledgeBasePlan] = []
    for skill_summary in resolved_skill_service.list_skills():
        skill_detail = resolved_skill_service.get_skill_detail(skill_summary.skill_id)
        documents: list[SeedReferenceDocumentPlan] = []
        for reference in skill_detail.references:
            reference_path = _resolve_reference_path(reference.resolved_path)
            if not reference_path.exists():
                raise RuntimeError(f"seed reference file missing: {reference_path}")

            documents.append(
                SeedReferenceDocumentPlan(
                    file_name=reference.file_name,
                    file_path=reference_path,
                    title=reference.title,
                    category_keys=list(reference.category_keys),
                    content_hash=_sha256_file(reference_path),
                )
            )

        plans.append(
            SeedKnowledgeBasePlan(
                skill_id=skill_detail.skill_id,
                name=f"{skill_detail.display_name} 参考知识库",
                category=SEED_KNOWLEDGE_CATEGORY,
                description=f"由 {skill_detail.display_name} 的 skill reference 初始化的种子知识库",
                source_type=SEED_KNOWLEDGE_SOURCE_TYPE,
                documents=documents,
            )
        )

    return plans


async def seed_knowledge_base_for_plan(
    session: Any,
    repository: KnowledgeRepository,
    admin_service: AdminKnowledgeService,
    plan: SeedKnowledgeBasePlan,
) -> SeedKnowledgeBaseOutcome:
    """为单个逻辑知识库执行幂等种子初始化。"""

    knowledge_base = await repository.find_knowledge_base_by_name_and_scope(
        name=plan.name,
        category=plan.category,
        skill_id=plan.skill_id,
        source_type=plan.source_type,
        only_enabled=False,
    )
    created = False
    if knowledge_base is None:
        knowledge_base = await admin_service.create_knowledge_base(
            session,
            name=plan.name,
            category=plan.category,
            description=plan.description,
            skill_id=plan.skill_id,
        )
        created = True

    existing_documents = await repository.list_documents_by_knowledge_base(
        knowledge_base.id,
        limit=500,
    )
    existing_documents_by_name = {
        document.original_file_name: document for document in existing_documents
    }

    document_outcomes: list[SeedDocumentOutcome] = []
    for document_plan in plan.documents:
        existing_document = existing_documents_by_name.get(document_plan.file_name)
        if existing_document is not None:
            existing_hash = str(dict(existing_document.metadata_json or {}).get("content_hash", ""))
            if existing_hash == document_plan.content_hash:
                document_outcomes.append(
                    SeedDocumentOutcome(
                        file_name=document_plan.file_name,
                        document_id=existing_document.id,
                        skipped=True,
                        reason="already_seeded",
                    )
                )
                continue

            document_outcomes.append(
                SeedDocumentOutcome(
                    file_name=document_plan.file_name,
                    document_id=existing_document.id,
                    skipped=True,
                    reason="file_name_exists",
                )
            )
            logger.warning(
                "seed document already exists with different content hash, skip it: knowledge_base_id={}, file_name={}, existing_hash={}, seed_hash={}",
                knowledge_base.id,
                document_plan.file_name,
                existing_hash,
                document_plan.content_hash,
            )
            continue

        result = await admin_service.add_document_to_knowledge_base(
            session,
            knowledge_base_id=knowledge_base.id,
            file_name=document_plan.file_name,
            file_content=document_plan.file_path.read_bytes(),
            content_type="text/markdown" if document_plan.file_name.endswith(".md") else "text/plain",
        )
        document_outcomes.append(
            SeedDocumentOutcome(
                file_name=document_plan.file_name,
                document_id=result.document_id,
                chunk_count=result.chunk_count,
                skipped=False,
            )
        )

    return SeedKnowledgeBaseOutcome(
        skill_id=plan.skill_id,
        knowledge_base_id=knowledge_base.id,
        knowledge_base_name=knowledge_base.name,
        created=created,
        document_outcomes=document_outcomes,
    )


async def seed_admin_knowledge_from_references() -> SeedExecutionReport:
    """执行全部 preset skill 的种子初始化。"""

    setup_logger(force=True)

    await storage_manager.connect()
    await database_manager.connect()
    milvus_manager.connect()

    skill_service = SkillService(root_dir=PROJECT_ROOT / "skills")
    plans = build_seed_knowledge_base_plans(skill_service)

    session_factory = database_manager.get_session_factory()
    outcomes: list[SeedKnowledgeBaseOutcome] = []
    try:
        async with session_factory() as session:
            repository = KnowledgeRepository(session)
            for plan in plans:
                outcome = await seed_knowledge_base_for_plan(
                    session,
                    repository,
                    admin_knowledge_service,
                    plan,
                )
                outcomes.append(outcome)
                logger.info(
                    "seed knowledge base processed: skill_id={}, knowledge_base_id={}, created={}, created_documents={}, skipped_documents={}",
                    outcome.skill_id,
                    outcome.knowledge_base_id,
                    outcome.created,
                    outcome.created_documents,
                    outcome.skipped_documents,
                )
    finally:
        await database_manager.close()
        milvus_manager.close()

    created_knowledge_bases = sum(1 for item in outcomes if item.created)
    reused_knowledge_bases = len(outcomes) - created_knowledge_bases
    created_documents = sum(item.created_documents for item in outcomes)
    skipped_documents = sum(item.skipped_documents for item in outcomes)

    return SeedExecutionReport(
        total_knowledge_bases=len(outcomes),
        created_knowledge_bases=created_knowledge_bases,
        reused_knowledge_bases=reused_knowledge_bases,
        created_documents=created_documents,
        skipped_documents=skipped_documents,
        items=outcomes,
    )


def main() -> int:
    _ = _build_arg_parser().parse_args()
    try:
        report = asyncio.run(seed_admin_knowledge_from_references())
        logger.success("seed admin knowledge completed: {}", report.to_dict())
        return 0
    except Exception:
        logger.exception("seed admin knowledge failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
