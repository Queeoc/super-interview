"""预设 Skill 查询 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.services.skill_service import skill_service

router = APIRouter()


@router.get("/skills")
async def list_skills() -> dict[str, Any]:
    """返回当前可用的预设 Skill 列表。"""

    skills = skill_service.list_skills()
    return {
        "code": 200,
        "message": "success",
        "data": [skill.model_dump(mode="json") for skill in skills],
    }


@router.get("/skills/{skill_id}")
async def get_skill_detail(skill_id: str) -> dict[str, Any]:
    """返回单个 Skill 的完整定义。"""

    detail = skill_service.get_skill_detail(skill_id)
    return {
        "code": 200,
        "message": "success",
        "data": detail.model_dump(mode="json"),
    }


@router.get("/skills/{skill_id}/reference-section")
async def get_skill_reference_section(skill_id: str) -> dict[str, Any]:
    """返回 Skill 拼装后的 reference section。"""

    section = skill_service.build_reference_section(skill_id)
    return {
        "code": 200,
        "message": "success",
        "data": section.model_dump(mode="json"),
    }
