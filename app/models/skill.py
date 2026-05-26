"""Skill 领域的 Pydantic 数据模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SkillCategoryDTO(BaseModel):
    """Skill 下的题目分类定义。"""

    key: str = Field(..., description="分类标识")
    label: str = Field(..., description="分类展示名称")
    priority: str = Field(..., description="分类优先级")
    ref: str | None = Field(default=None, description="关联的参考资料文件名")
    shared: bool = Field(default=False, description="是否引用共享 reference 目录")


class SkillReferenceDTO(BaseModel):
    """Skill 绑定的参考资料定义。"""

    file_name: str = Field(..., description="参考资料文件名")
    title: str = Field(..., description="参考资料标题")
    shared: bool = Field(..., description="是否为共享 reference 文件")
    resolved_path: str = Field(..., description="解析后的 reference 相对路径")
    category_keys: list[str] = Field(default_factory=list, description="使用该资料的分类列表")


class SkillSummaryDTO(BaseModel):
    """Skill 列表接口使用的摘要对象。"""

    skill_id: str = Field(..., description="Skill 标识")
    display_name: str = Field(..., description="Skill 展示名称")
    description: str = Field(..., description="Skill 描述")
    display: dict[str, str] = Field(default_factory=dict, description="展示配置")
    categories: list[SkillCategoryDTO] = Field(default_factory=list, description="分类配置")


class SkillDetailDTO(SkillSummaryDTO):
    """Skill 详情对象。"""

    content_markdown: str = Field(..., description="SKILL.md 正文内容")
    references: list[SkillReferenceDTO] = Field(default_factory=list, description="引用的参考资料")
    enabled_tools: list[str] = Field(default_factory=list, description="启用的工具列表")
    question_preferences: dict[str, Any] = Field(
        default_factory=dict,
        description="出题偏好配置",
    )


class SkillReferenceSectionResponse(BaseModel):
    """Skill reference section 响应对象。"""

    skill_id: str = Field(..., description="Skill 标识")
    reference_markdown: str = Field(..., description="拼装后的 reference markdown")
    resolved_reference_files: list[str] = Field(
        default_factory=list,
        description="实际引用的 reference 文件路径",
    )

