"""Phase 4 预设 Skill 系统的最小验证。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api import skill as skill_api
from app.config import config
from app.middleware.error_handler import register_exception_handlers
from app.services.skill_service import PRESET_SKILL_IDS, SkillService
from app.utils.exceptions import BusinessException, ErrorCode


@pytest.fixture
def preset_skill_service() -> SkillService:
    """使用仓库内真实 skills 目录构建服务实例。"""

    return SkillService(root_dir=config.skill_root_dir)


@pytest.fixture
def skill_api_client(
    monkeypatch: pytest.MonkeyPatch,
    preset_skill_service: SkillService,
) -> TestClient:
    """构建只挂载 Skill 路由的轻量测试客户端。"""

    app = FastAPI()
    register_exception_handlers(app)
    monkeypatch.setattr(skill_api, "skill_service", preset_skill_service)
    app.include_router(skill_api.router, prefix="/api")
    return TestClient(app)


def test_skill_service_lists_preset_skills_in_stable_order(
    preset_skill_service: SkillService,
) -> None:
    """Skill 列表应按预设顺序稳定返回首批 Skill。"""

    skills = preset_skill_service.list_skills()

    assert [skill.skill_id for skill in skills] == list(PRESET_SKILL_IDS)
    assert skills[0].display_name == "Java 后端开发"
    assert skills[1].display_name == "Python 后端开发"
    assert skills[2].display_name == "算法与数据结构"


def test_skill_service_returns_detail_with_markdown_and_references(
    preset_skill_service: SkillService,
) -> None:
    """Skill 详情应包含正文、分类和 reference 元数据。"""

    detail = preset_skill_service.get_skill_detail("python-backend")

    assert detail.skill_id == "python-backend"
    assert detail.display_name == "Python 后端开发"
    assert "你是一位 Python 后端面试官" in detail.content_markdown
    assert [category.key for category in detail.categories] == [
        "PYTHON_BASIC",
        "DATABASE",
        "DJANGO_FLASK",
        "CACHE",
        "SYSTEM_DESIGN_SCENARIO",
        "DEPLOY",
        "PROJECT",
    ]
    assert [reference.file_name for reference in detail.references] == [
        "python-basic.md",
        "database.md",
        "django-flask.md",
        "redis.md",
        "system-design-scenarios.md",
    ]
    assert detail.enabled_tools == []
    assert detail.question_preferences == {}


def test_skill_service_builds_reference_section_with_deduplicated_files(
    preset_skill_service: SkillService,
) -> None:
    """同一 reference 被多个分类复用时应只拼接一次。"""

    section = preset_skill_service.build_reference_section("algorithm")

    assert section.skill_id == "algorithm"
    assert section.resolved_reference_files == [
        "skills/_shared/references/algorithm-data-structures.md"
    ]
    assert "# 算法与数据结构 参考资料" in section.reference_markdown
    assert "来源文件：skills/_shared/references/algorithm-data-structures.md" in (
        section.reference_markdown
    )
    assert "算法与数据结构（统一参考）" in section.reference_markdown


def test_skill_service_raises_not_found_for_unknown_skill(
    preset_skill_service: SkillService,
) -> None:
    """未知 Skill ID 应返回 404 语义的业务异常。"""

    with pytest.raises(BusinessException) as exc_info:
        preset_skill_service.get_skill_detail("frontend")

    assert exc_info.value.code == ErrorCode.SKILL_NOT_FOUND
    assert exc_info.value.http_status == 404


def test_skill_service_raises_clear_error_for_invalid_meta_yaml(tmp_path: Path) -> None:
    """损坏的 skill.meta.yml 应返回清晰的解析失败错误。"""

    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "broken-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: broken-skill\ndescription: 测试 Skill\n---\n# Overview\n测试内容\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.meta.yml").write_text(
        "displayName: Broken Skill\ncategories: [\n",
        encoding="utf-8",
    )

    service = SkillService(root_dir=skill_root, allowed_skill_ids=("broken-skill",))

    with pytest.raises(BusinessException) as exc_info:
        service.list_skills()

    assert exc_info.value.code == ErrorCode.SKILL_META_INVALID
    assert "broken-skill" in exc_info.value.details["skill_id"]


def test_skill_service_raises_clear_error_for_missing_reference_file(tmp_path: Path) -> None:
    """缺失 reference 文件时不应静默降级。"""

    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "broken-skill"
    shared_reference_dir = skill_root / "_shared" / "references"
    skill_dir.mkdir(parents=True)
    shared_reference_dir.mkdir(parents=True)

    (skill_dir / "SKILL.md").write_text(
        "---\nname: broken-skill\ndescription: 测试 Skill\n---\n# Overview\n测试内容\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.meta.yml").write_text(
        "\n".join(
            [
                "displayName: Broken Skill",
                "categories:",
                "  - key: TEST",
                "    label: 测试分类",
                "    priority: CORE",
                "    ref: missing.md",
                "    shared: true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    service = SkillService(root_dir=skill_root, allowed_skill_ids=("broken-skill",))

    with pytest.raises(BusinessException) as exc_info:
        service.get_skill_detail("broken-skill")

    assert exc_info.value.code == ErrorCode.SKILL_REFERENCE_NOT_FOUND
    assert exc_info.value.details["path"].endswith("missing.md")


def test_skill_api_happy_path_for_list_detail_and_reference_section(
    skill_api_client: TestClient,
) -> None:
    """Skill API 应覆盖列表、详情与 reference section 三条 happy path。"""

    list_response = skill_api_client.get("/api/skills")
    assert list_response.status_code == 200
    assert [item["skill_id"] for item in list_response.json()["data"]] == list(PRESET_SKILL_IDS)

    detail_response = skill_api_client.get("/api/skills/java-backend")
    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["display_name"] == "Java 后端开发"
    assert "references" in detail_response.json()["data"]

    section_response = skill_api_client.get("/api/skills/java-backend/reference-section")
    assert section_response.status_code == 200
    assert section_response.json()["data"]["resolved_reference_files"] == [
        "skills/_shared/references/java.md",
        "skills/_shared/references/mysql.md",
        "skills/_shared/references/redis.md",
        "skills/_shared/references/spring.md",
        "skills/_shared/references/system-design-scenarios.md",
    ]

