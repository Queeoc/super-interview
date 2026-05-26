"""预设 Skill 的加载、解析与查询服务。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from loguru import logger
import yaml

from app.config import config
from app.models.skill import (
    SkillCategoryDTO,
    SkillDetailDTO,
    SkillReferenceDTO,
    SkillReferenceSectionResponse,
    SkillSummaryDTO,
)
from app.utils.exceptions import BusinessException, ErrorCode

PRESET_SKILL_IDS = ("java-backend", "python-backend", "algorithm")
_FRONT_MATTER_PATTERN = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


@dataclass(slots=True)
class _ParsedSkillDocument:
    """解析后的 SKILL.md 文档内容。"""

    name: str
    description: str
    content_markdown: str


@dataclass(slots=True)
class _ResolvedReference:
    """解析后的 reference 文件与其元数据。"""

    metadata: SkillReferenceDTO
    content_markdown: str


class SkillService:
    """负责预设 Skill 的磁盘资源加载与查询。"""

    def __init__(
        self,
        root_dir: Path | None = None,
        allowed_skill_ids: tuple[str, ...] = PRESET_SKILL_IDS,
    ) -> None:
        self._root_dir = Path(root_dir or config.skill_root_dir).resolve()
        self._allowed_skill_ids = tuple(allowed_skill_ids)
        self._allowed_skill_id_set = set(self._allowed_skill_ids)

    def list_skills(self) -> list[SkillSummaryDTO]:
        """返回首批预设 Skill 的稳定列表。"""

        return [self._to_summary(skill) for skill in self._load_skill_catalog().values()]

    def get_skill_detail(self, skill_id: str) -> SkillDetailDTO:
        """获取单个 Skill 的完整详情。"""

        normalized_skill_id = self._normalize_skill_id(skill_id)
        return self._load_skill_catalog()[normalized_skill_id]

    def build_reference_section(self, skill_id: str) -> SkillReferenceSectionResponse:
        """根据 Skill 的 reference 绑定生成拼装后的 markdown。"""

        detail = self.get_skill_detail(skill_id)
        resolved_references = self._load_references(
            normalized_skill_id=detail.skill_id,
            categories=detail.categories,
        )

        lines: list[str] = [f"# {detail.display_name} 参考资料", ""]
        resolved_reference_files: list[str] = []
        for reference in resolved_references:
            resolved_reference_files.append(reference.metadata.resolved_path)
            lines.append(f"> 来源文件：{reference.metadata.resolved_path}")
            lines.append("")
            lines.append(reference.content_markdown.strip())
            lines.append("")

        reference_markdown = "\n".join(lines).strip()
        logger.info(
            "skill reference section built, skill_id={}, reference_count={}",
            detail.skill_id,
            len(resolved_reference_files),
        )
        return SkillReferenceSectionResponse(
            skill_id=detail.skill_id,
            reference_markdown=reference_markdown,
            resolved_reference_files=resolved_reference_files,
        )

    def _load_skill_catalog(self) -> dict[str, SkillDetailDTO]:
        """扫描技能目录并按预设顺序加载首批 Skill。"""

        if not self._root_dir.exists():
            raise BusinessException(
                code=ErrorCode.SKILL_RESOURCE_LOAD_FAILED,
                message="Skill 根目录不存在",
                http_status=500,
                details={"path": self._root_dir.as_posix()},
            )

        discovered_markdown_paths = {
            path.parent.name: path
            for path in sorted(self._root_dir.glob("*/SKILL.md"))
            if path.parent.name != "_shared"
        }

        catalog: dict[str, SkillDetailDTO] = {}
        for skill_id in self._allowed_skill_ids:
            markdown_path = discovered_markdown_paths.get(skill_id)
            if markdown_path is None:
                raise BusinessException(
                    code=ErrorCode.SKILL_RESOURCE_LOAD_FAILED,
                    message="预设 Skill 资源缺失",
                    http_status=500,
                    details={
                        "skill_id": skill_id,
                        "expected_path": (self._root_dir / skill_id / "SKILL.md").as_posix(),
                    },
                )
            catalog[skill_id] = self._load_single_skill(skill_id=skill_id, markdown_path=markdown_path)
        return catalog

    def _load_single_skill(self, *, skill_id: str, markdown_path: Path) -> SkillDetailDTO:
        """加载单个 Skill 的文档、meta 与 reference 绑定。"""

        skill_dir = markdown_path.parent
        meta_path = skill_dir / "skill.meta.yml"

        parsed_document = self._load_skill_document(skill_id=skill_id, markdown_path=markdown_path)
        meta = self._load_skill_meta(skill_id=skill_id, meta_path=meta_path)
        display_name = self._parse_optional_string(
            skill_id=skill_id,
            path=meta_path,
            field_name="displayName",
            raw_value=meta.get("displayName"),
            error_code=ErrorCode.SKILL_META_INVALID,
        ) or parsed_document.name
        display = self._parse_display(skill_id=skill_id, meta_path=meta_path, display_raw=meta.get("display"))
        categories = self._parse_categories(skill_id=skill_id, meta_path=meta_path, categories_raw=meta.get("categories"))
        enabled_tools = self._parse_string_list(
            skill_id=skill_id,
            meta_path=meta_path,
            field_name="enabled_tools",
            raw_value=meta.get("enabled_tools", []),
        )
        question_preferences = self._parse_mapping(
            skill_id=skill_id,
            meta_path=meta_path,
            field_name="question_preferences",
            raw_value=meta.get("question_preferences", {}),
        )
        references = [reference.metadata for reference in self._load_references(
            normalized_skill_id=skill_id,
            categories=categories,
        )]

        return SkillDetailDTO(
            skill_id=skill_id,
            display_name=display_name,
            description=parsed_document.description,
            display=display,
            categories=categories,
            content_markdown=parsed_document.content_markdown,
            references=references,
            enabled_tools=enabled_tools,
            question_preferences=question_preferences,
        )

    def _load_skill_document(self, *, skill_id: str, markdown_path: Path) -> _ParsedSkillDocument:
        """解析 SKILL.md front matter 与正文。"""

        raw_text = self._read_text(markdown_path)
        match = _FRONT_MATTER_PATTERN.match(raw_text)
        if match is None:
            raise BusinessException(
                code=ErrorCode.SKILL_RESOURCE_LOAD_FAILED,
                message="SKILL.md 缺少合法的 front matter",
                http_status=500,
                details={"skill_id": skill_id, "path": markdown_path.as_posix()},
            )

        try:
            front_matter = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            raise BusinessException(
                code=ErrorCode.SKILL_RESOURCE_LOAD_FAILED,
                message="SKILL.md front matter 解析失败",
                http_status=500,
                details={
                    "skill_id": skill_id,
                    "path": markdown_path.as_posix(),
                    "error": str(exc),
                },
            ) from exc

        if not isinstance(front_matter, dict):
            raise BusinessException(
                code=ErrorCode.SKILL_RESOURCE_LOAD_FAILED,
                message="SKILL.md front matter 必须是对象结构",
                http_status=500,
                details={"skill_id": skill_id, "path": markdown_path.as_posix()},
            )

        name = self._require_string(
            skill_id=skill_id,
            field_name="name",
            raw_value=front_matter.get("name"),
            path=markdown_path,
            error_code=ErrorCode.SKILL_RESOURCE_LOAD_FAILED,
        )
        description = self._require_string(
            skill_id=skill_id,
            field_name="description",
            raw_value=front_matter.get("description"),
            path=markdown_path,
            error_code=ErrorCode.SKILL_RESOURCE_LOAD_FAILED,
        )
        content_markdown = match.group(2).strip()
        if not content_markdown:
            raise BusinessException(
                code=ErrorCode.SKILL_RESOURCE_LOAD_FAILED,
                message="SKILL.md 正文不能为空",
                http_status=500,
                details={"skill_id": skill_id, "path": markdown_path.as_posix()},
            )

        return _ParsedSkillDocument(
            name=name,
            description=description,
            content_markdown=content_markdown,
        )

    def _load_skill_meta(self, *, skill_id: str, meta_path: Path) -> dict[str, Any]:
        """读取并解析 skill.meta.yml。"""

        try:
            parsed_meta = yaml.safe_load(self._read_text(meta_path)) or {}
        except FileNotFoundError as exc:
            raise BusinessException(
                code=ErrorCode.SKILL_RESOURCE_LOAD_FAILED,
                message="skill.meta.yml 文件缺失",
                http_status=500,
                details={"skill_id": skill_id, "path": meta_path.as_posix()},
            ) from exc
        except yaml.YAMLError as exc:
            raise BusinessException(
                code=ErrorCode.SKILL_META_INVALID,
                message="skill.meta.yml 解析失败",
                http_status=500,
                details={"skill_id": skill_id, "path": meta_path.as_posix(), "error": str(exc)},
            ) from exc

        if not isinstance(parsed_meta, dict):
            raise BusinessException(
                code=ErrorCode.SKILL_META_INVALID,
                message="skill.meta.yml 必须是对象结构",
                http_status=500,
                details={"skill_id": skill_id, "path": meta_path.as_posix()},
            )

        return parsed_meta

    def _parse_display(
        self,
        *,
        skill_id: str,
        meta_path: Path,
        display_raw: Any,
    ) -> dict[str, str]:
        """解析 display 字段。"""

        if display_raw is None:
            return {}
        if not isinstance(display_raw, dict):
            raise BusinessException(
                code=ErrorCode.SKILL_META_INVALID,
                message="display 字段必须是对象结构",
                http_status=500,
                details={"skill_id": skill_id, "path": meta_path.as_posix(), "field": "display"},
            )

        display: dict[str, str] = {}
        for key, value in display_raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise BusinessException(
                    code=ErrorCode.SKILL_META_INVALID,
                    message="display 字段的键和值都必须是字符串",
                    http_status=500,
                    details={"skill_id": skill_id, "path": meta_path.as_posix(), "field": "display"},
                )
            display[key] = value.strip()
        return display

    def _parse_categories(
        self,
        *,
        skill_id: str,
        meta_path: Path,
        categories_raw: Any,
    ) -> list[SkillCategoryDTO]:
        """解析 categories 配置。"""

        if categories_raw is None:
            return []
        if not isinstance(categories_raw, list):
            raise BusinessException(
                code=ErrorCode.SKILL_META_INVALID,
                message="categories 字段必须是数组",
                http_status=500,
                details={"skill_id": skill_id, "path": meta_path.as_posix(), "field": "categories"},
            )

        categories: list[SkillCategoryDTO] = []
        for index, category_raw in enumerate(categories_raw):
            if not isinstance(category_raw, dict):
                raise BusinessException(
                    code=ErrorCode.SKILL_META_INVALID,
                    message="categories 中的每一项都必须是对象结构",
                    http_status=500,
                    details={
                        "skill_id": skill_id,
                        "path": meta_path.as_posix(),
                        "field": f"categories[{index}]",
                    },
                )

            key = self._require_string(
                skill_id=skill_id,
                field_name=f"categories[{index}].key",
                raw_value=category_raw.get("key"),
                path=meta_path,
                error_code=ErrorCode.SKILL_META_INVALID,
            )
            label = self._require_string(
                skill_id=skill_id,
                field_name=f"categories[{index}].label",
                raw_value=category_raw.get("label"),
                path=meta_path,
                error_code=ErrorCode.SKILL_META_INVALID,
            )
            priority = self._require_string(
                skill_id=skill_id,
                field_name=f"categories[{index}].priority",
                raw_value=category_raw.get("priority"),
                path=meta_path,
                error_code=ErrorCode.SKILL_META_INVALID,
            )
            ref = self._parse_optional_string(
                skill_id=skill_id,
                path=meta_path,
                field_name=f"categories[{index}].ref",
                raw_value=category_raw.get("ref"),
                error_code=ErrorCode.SKILL_META_INVALID,
            )
            shared_raw = category_raw.get("shared", False)
            if not isinstance(shared_raw, bool):
                raise BusinessException(
                    code=ErrorCode.SKILL_META_INVALID,
                    message="shared 字段必须是布尔值",
                    http_status=500,
                    details={
                        "skill_id": skill_id,
                        "path": meta_path.as_posix(),
                        "field": f"categories[{index}].shared",
                    },
                )
            categories.append(
                SkillCategoryDTO(
                    key=key,
                    label=label,
                    priority=priority,
                    ref=ref,
                    shared=shared_raw,
                )
            )
        return categories

    def _load_references(
        self,
        *,
        normalized_skill_id: str,
        categories: list[SkillCategoryDTO],
    ) -> list[_ResolvedReference]:
        """根据 categories 解析 reference 文件。"""

        resolved_references: dict[str, _ResolvedReference] = {}
        for category in categories:
            if not category.ref:
                continue

            reference_path = self._resolve_reference_path(
                normalized_skill_id=normalized_skill_id,
                ref_file_name=category.ref,
                shared=category.shared,
            )
            reference_content = self._read_reference_content(
                normalized_skill_id=normalized_skill_id,
                reference_path=reference_path,
            )
            resolved_path = self._to_relative_display_path(reference_path)

            existing_reference = resolved_references.get(resolved_path)
            if existing_reference is not None:
                if category.key not in existing_reference.metadata.category_keys:
                    existing_reference.metadata.category_keys.append(category.key)
                continue

            resolved_references[resolved_path] = _ResolvedReference(
                metadata=SkillReferenceDTO(
                    file_name=reference_path.name,
                    title=self._extract_markdown_title(reference_content, reference_path.stem),
                    shared=category.shared,
                    resolved_path=resolved_path,
                    category_keys=[category.key],
                ),
                content_markdown=reference_content,
            )
        return list(resolved_references.values())

    def _resolve_reference_path(
        self,
        *,
        normalized_skill_id: str,
        ref_file_name: str,
        shared: bool,
    ) -> Path:
        """解析单个 reference 文件路径。"""

        if shared:
            return self._root_dir / "_shared" / "references" / ref_file_name
        return self._root_dir / normalized_skill_id / ref_file_name

    def _read_reference_content(self, *, normalized_skill_id: str, reference_path: Path) -> str:
        """读取 reference 文件内容并在缺失时抛出业务异常。"""

        try:
            return self._read_text(reference_path)
        except FileNotFoundError as exc:
            raise BusinessException(
                code=ErrorCode.SKILL_REFERENCE_NOT_FOUND,
                message="Skill reference 文件缺失",
                http_status=500,
                details={
                    "skill_id": normalized_skill_id,
                    "path": reference_path.as_posix(),
                },
            ) from exc

    def _normalize_skill_id(self, skill_id: str) -> str:
        """校验并标准化 Skill 标识。"""

        normalized_skill_id = skill_id.strip()
        if not normalized_skill_id or normalized_skill_id not in self._allowed_skill_id_set:
            raise BusinessException(
                code=ErrorCode.SKILL_NOT_FOUND,
                message="Skill 不存在",
                http_status=404,
                details={"skill_id": normalized_skill_id or skill_id},
            )
        return normalized_skill_id

    def _to_summary(self, detail: SkillDetailDTO) -> SkillSummaryDTO:
        """将详情对象转换为列表摘要对象。"""

        return SkillSummaryDTO(
            skill_id=detail.skill_id,
            display_name=detail.display_name,
            description=detail.description,
            display=detail.display,
            categories=detail.categories,
        )

    def _read_text(self, path: Path) -> str:
        """统一按 UTF-8 读取文本文件。"""

        return path.read_text(encoding="utf-8")

    def _extract_markdown_title(self, content_markdown: str, default_title: str) -> str:
        """从 markdown 首个标题提取参考资料标题。"""

        for line in content_markdown.splitlines():
            stripped_line = line.strip()
            if stripped_line.startswith("# "):
                return stripped_line[2:].strip()
        return default_title

    def _to_relative_display_path(self, path: Path) -> str:
        """把绝对路径转换为更稳定的相对展示路径。"""

        try:
            return path.relative_to(self._root_dir.parent).as_posix()
        except ValueError:
            return path.as_posix()

    def _parse_string_list(
        self,
        *,
        skill_id: str,
        meta_path: Path,
        field_name: str,
        raw_value: Any,
    ) -> list[str]:
        """解析字符串数组字段。"""

        if raw_value is None:
            return []
        if not isinstance(raw_value, list):
            raise BusinessException(
                code=ErrorCode.SKILL_META_INVALID,
                message=f"{field_name} 字段必须是数组",
                http_status=500,
                details={"skill_id": skill_id, "path": meta_path.as_posix(), "field": field_name},
            )

        values: list[str] = []
        for index, item in enumerate(raw_value):
            if not isinstance(item, str) or not item.strip():
                raise BusinessException(
                    code=ErrorCode.SKILL_META_INVALID,
                    message=f"{field_name} 中的每一项都必须是非空字符串",
                    http_status=500,
                    details={
                        "skill_id": skill_id,
                        "path": meta_path.as_posix(),
                        "field": f"{field_name}[{index}]",
                    },
                )
            values.append(item.strip())
        return values

    def _parse_mapping(
        self,
        *,
        skill_id: str,
        meta_path: Path,
        field_name: str,
        raw_value: Any,
    ) -> dict[str, Any]:
        """解析对象结构字段。"""

        if raw_value is None:
            return {}
        if not isinstance(raw_value, dict):
            raise BusinessException(
                code=ErrorCode.SKILL_META_INVALID,
                message=f"{field_name} 字段必须是对象结构",
                http_status=500,
                details={"skill_id": skill_id, "path": meta_path.as_posix(), "field": field_name},
            )
        return dict(raw_value)

    def _parse_optional_string(
        self,
        *,
        skill_id: str,
        path: Path,
        field_name: str,
        raw_value: Any,
        error_code: ErrorCode,
    ) -> str | None:
        """读取可选字符串字段，并在类型错误时抛出异常。"""

        if raw_value is None:
            return None
        if not isinstance(raw_value, str):
            raise BusinessException(
                code=error_code,
                message=f"{field_name} 字段必须是字符串",
                http_status=500,
                details={"skill_id": skill_id, "path": path.as_posix(), "field": field_name},
            )
        normalized_value = raw_value.strip()
        return normalized_value or None

    def _require_string(
        self,
        *,
        skill_id: str,
        field_name: str,
        raw_value: Any,
        path: Path,
        error_code: ErrorCode,
    ) -> str:
        """校验必填字符串字段。"""

        if not isinstance(raw_value, str) or not raw_value.strip():
            raise BusinessException(
                code=error_code,
                message=f"{field_name} 字段缺失或为空",
                http_status=500,
                details={"skill_id": skill_id, "path": path.as_posix(), "field": field_name},
            )
        return raw_value.strip()


skill_service = SkillService()


__all__ = [
    "PRESET_SKILL_IDS",
    "SkillService",
    "skill_service",
]
