"""通用结构化输出组件。"""

from __future__ import annotations

import json
import re
from enum import Enum
from types import UnionType
from typing import Any, Literal, TypeVar, Union, get_args, get_origin

from loguru import logger
from pydantic import BaseModel

from app.config import config
from app.core.llm_factory import llm_factory

TModel = TypeVar("TModel", bound=BaseModel)

_CODE_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_TRAILING_COMMA_PATTERN = re.compile(r",(\s*[}\]])")
_SINGLE_QUOTED_KEY_PATTERN = re.compile(r"(?P<prefix>[{,\s])'(?P<key>[^']+?)'\s*:")
_SINGLE_QUOTED_VALUE_PATTERN = re.compile(r":\s*'(?P<value>[^']*?)'(?P<suffix>\s*[,}])")


class StructuredOutputRunner:
    """统一封装 provider-native structured output 与本地 JSON 修复。"""

    def __init__(
        self,
        *,
        enable_llm: bool = True,
        llm_factory_fn: Any | None = None,
        max_retries: int | None = None,
    ) -> None:
        self._enable_llm = enable_llm
        self._llm_factory_fn = llm_factory_fn or llm_factory.create_chat_model
        self._max_retries = max(0, max_retries if max_retries is not None else config.interview.structured_output_max_retries)

    async def ainvoke(
        self,
        *,
        prompt_text: str,
        schema: type[TModel],
        model: str | None = None,
        temperature: float = 0.2,
    ) -> TModel:
        """调用结构化输出，必要时回退到本地 JSON 修复。"""

        if not self._enable_llm:
            raise RuntimeError("LLM 已禁用，使用规则降级")

        structured_prompt = self._build_structured_prompt(prompt_text=prompt_text, schema=schema)
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 2):
            llm = self._llm_factory_fn(
                model=model,
                temperature=temperature,
                streaming=False,
            )
            try:
                logger.info(
                    "执行结构化输出: schema={}, attempt={}",
                    schema.__name__,
                    attempt,
                )
                return await self._invoke_once(
                    llm=llm,
                    prompt_text=structured_prompt,
                    schema=schema,
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "结构化输出失败: schema={}, attempt={}, error={}",
                    schema.__name__,
                    attempt,
                    exc,
                )

        raise ValueError(f"structured output 解析失败: {schema.__name__}") from last_error

    async def _invoke_once(
        self,
        *,
        llm: Any,
        prompt_text: str,
        schema: type[TModel],
    ) -> TModel:
        """优先走 provider-native structured output，失败后尝试本地修复。"""

        native_failed = False
        if hasattr(llm, "with_structured_output"):
            try:
                structured_llm = llm.with_structured_output(
                    schema,
                    method="json_schema",
                    strict=True,
                    include_raw=True,
                )
                response = await structured_llm.ainvoke(prompt_text)
                return self._parse_native_response(schema=schema, response=response)
            except Exception as exc:
                native_failed = True
                logger.info(
                    "provider-native structured output 未成功，切换原始文本修复: schema={}, error={}",
                    schema.__name__,
                    exc,
                )

        if not hasattr(llm, "ainvoke"):
            raise ValueError(f"structured output 缺少可用调用入口: {type(llm).__name__}")

        raw_response = await llm.ainvoke(prompt_text)
        raw_text = self._extract_raw_text(raw_response)
        result = self._validate_repaired_payload(schema=schema, raw_text=raw_text)
        if native_failed:
            logger.info(
                "structured output 原始文本修复成功: schema={}, resolution_mode=native_raw_repaired",
                schema.__name__,
            )
        else:
            logger.info(
                "structured output 原始文本直出成功: schema={}, resolution_mode=raw_only",
                schema.__name__,
            )
        return result

    def _parse_native_response(
        self,
        *,
        schema: type[TModel],
        response: Any,
    ) -> TModel:
        """解析 provider-native structured output 响应。"""

        if isinstance(response, schema):
            logger.info(
                "structured output 解析成功: schema={}, resolution_mode=native",
                schema.__name__,
            )
            return response

        if not isinstance(response, dict):
            raw_text = self._extract_raw_text(response)
            result = self._validate_repaired_payload(schema=schema, raw_text=raw_text)
            logger.info(
                "structured output 原始文本修复成功: schema={}, resolution_mode=native_raw_repaired",
                schema.__name__,
            )
            return result

        parsing_error = response.get("parsing_error")
        parsed = response.get("parsed")

        if parsing_error is None and parsed is not None:
            if isinstance(parsed, schema):
                logger.info(
                    "structured output 解析成功: schema={}, resolution_mode=native",
                    schema.__name__,
                )
                return parsed
            result = schema.model_validate(parsed, strict=True)
            logger.info(
                "structured output 解析成功: schema={}, resolution_mode=native",
                schema.__name__,
            )
            return result

        raw_text = self._extract_raw_text(response.get("raw"))
        if raw_text:
            try:
                result = self._validate_repaired_payload(schema=schema, raw_text=raw_text)
                logger.info(
                    "structured output 原始文本修复成功: schema={}, resolution_mode=native_raw_repaired",
                    schema.__name__,
                )
                return result
            except Exception as raw_exc:
                logger.warning(
                    "本地 JSON 修复失败: schema={}, raw={}, error={}",
                    schema.__name__,
                    self._preview_raw_response(raw_text),
                    raw_exc,
                )

        if parsing_error is not None:
            logger.warning(
                "provider-native 解析失败: schema={}, raw={}, error={}",
                schema.__name__,
                self._preview_raw_response(response.get("raw")),
                parsing_error,
            )
            if isinstance(parsing_error, BaseException):
                raise ValueError("structured output 解析失败") from parsing_error
            raise ValueError(f"structured output 解析失败: {parsing_error}")

        raise ValueError(f"structured output 缺少 parsed 结果: {schema.__name__}")

    def _validate_repaired_payload(
        self,
        *,
        schema: type[TModel],
        raw_text: str,
    ) -> TModel:
        """修复 JSON 文本后进行 schema 校验。"""

        repaired_text = self._repair_json_text(raw_text)
        payload = json.loads(repaired_text)
        return schema.model_validate(payload, strict=True)

    def _build_structured_prompt(
        self,
        *,
        prompt_text: str,
        schema: type[TModel],
    ) -> str:
        """为结构化输出补充统一的 JSON 指令。"""

        json_instruction = (
            "\n\n## Structured Output\n"
            "Return only a JSON object.\n"
            f"Use the canonical field names for `{schema.__name__}` exactly.\n"
            "Do not rename fields, omit required fields, add extra keys, or wrap the JSON in markdown.\n"
            "Arrays must be arrays, objects must stay objects, and optional fields may be null only when the schema allows it.\n"
            f"{self._render_schema_contract(schema)}\n"
        )
        return prompt_text + json_instruction

    def _render_schema_contract(self, schema: type[TModel]) -> str:
        """把 Pydantic schema 压缩成可直接写进 prompt 的契约说明。"""

        required_fields = [f"`{name}`" for name, field in schema.model_fields.items() if field.is_required()]
        field_lines = self._render_field_contract_lines(schema)
        example_payload = self._build_example_payload(schema)

        parts = [
            "### Schema Contract",
            f"- Canonical schema: `{schema.__name__}`",
            f"- Required top-level fields: {', '.join(required_fields) if required_fields else 'none'}",
            "- Extra keys are forbidden.",
            "- Field definitions:",
        ]
        parts.extend(field_lines or ["- (no fields)"])
        parts.extend(
            [
                "- Canonical JSON example:",
                "```json",
                json.dumps(example_payload, ensure_ascii=False, indent=2),
                "```",
            ]
        )
        return "\n".join(parts)

    def _render_field_contract_lines(
        self,
        schema: type[BaseModel],
        *,
        prefix: str = "",
    ) -> list[str]:
        """递归渲染字段说明，确保 prompt 里能看到 canonical 字段名。"""

        lines: list[str] = []
        for field_name, field_info in schema.model_fields.items():
            path = f"{prefix}{field_name}"
            requirement = "required" if field_info.is_required() else "optional"
            type_label = self._describe_annotation(field_info.annotation, field_name=field_name)
            description = (field_info.description or "").strip()
            line = f"- `{path}`: {type_label}, {requirement}"
            if description:
                line += f" - {description}"
            lines.append(line)

            nested_model = self._annotation_model(field_info.annotation)
            if nested_model is not None:
                lines.extend(self._render_field_contract_lines(nested_model, prefix=f"{path}."))
                continue

            list_model = self._list_item_model(field_info.annotation)
            if list_model is not None:
                lines.extend(self._render_field_contract_lines(list_model, prefix=f"{path}[]."))

        return lines

    def _build_example_payload(self, schema: type[BaseModel]) -> dict[str, Any]:
        """生成一个最小可读 JSON 示例，避免模型自己猜字段名。"""

        return {
            field_name: self._example_for_annotation(field_info.annotation, field_name=field_name)
            for field_name, field_info in schema.model_fields.items()
        }

    def _example_for_annotation(self, annotation: Any, *, field_name: str = "") -> Any:
        """为给定注解生成一个稳定的 JSON 示例值。"""

        annotation = self._unwrap_annotated(annotation)
        origin = get_origin(annotation)

        if origin in (Union, UnionType):
            return self._example_for_union(annotation, field_name=field_name)

        if origin is Literal:
            literal_values = list(get_args(annotation))
            return literal_values[0] if literal_values else ""

        if origin in (list, tuple):
            item_args = get_args(annotation)
            item_annotation = item_args[0] if item_args else Any
            return [self._example_for_annotation(item_annotation, field_name=field_name)]

        if origin in (dict,):
            value_args = get_args(annotation)
            value_annotation = value_args[1] if len(value_args) >= 2 else Any
            if field_name == "dimension_scores":
                return {
                    "technical_depth": 0.0,
                    "implementation_clarity": 0.0,
                    "problem_solving": 0.0,
                }
            return {"key": self._example_for_annotation(value_annotation, field_name=field_name)}

        if isinstance(annotation, type):
            if issubclass(annotation, BaseModel):
                return self._build_example_payload(annotation)
            if issubclass(annotation, Enum):
                members = list(annotation)
                return members[0].value if members else ""
            if annotation is str:
                if field_name in {"summary_text", "reason", "rationale", "closing_message", "question_text"}:
                    return "示例文本"
                return "示例"
            if annotation is int:
                return 1
            if annotation is float:
                return 0.0
            if annotation is bool:
                return False

        if annotation is Any:
            return "示例"

        return "示例"

    def _example_for_union(self, annotation: Any, *, field_name: str) -> Any:
        """为联合类型选择一个最安全的示例。"""

        candidates = [arg for arg in get_args(annotation) if arg is not type(None)]
        if not candidates:
            return None
        return self._example_for_annotation(candidates[0], field_name=field_name)

    def _describe_annotation(self, annotation: Any, *, field_name: str = "") -> str:
        """把注解压缩成便于 prompt 阅读的类型描述。"""

        annotation = self._unwrap_annotated(annotation)
        origin = get_origin(annotation)

        if origin in (Union, UnionType):
            items = [self._describe_annotation(arg, field_name=field_name) for arg in get_args(annotation) if arg is not type(None)]
            if not items:
                return "null"
            if len(items) == 1:
                return f"{items[0]} | null"
            return " | ".join(items)

        if origin is Literal:
            values = ", ".join(json.dumps(value, ensure_ascii=False) for value in get_args(annotation))
            return f"literal({values})"

        if origin in (list, tuple):
            item_args = get_args(annotation)
            item_type = self._describe_annotation(item_args[0], field_name=field_name) if item_args else "any"
            return f"array[{item_type}]"

        if origin in (dict,):
            value_args = get_args(annotation)
            value_type = self._describe_annotation(value_args[1], field_name=field_name) if len(value_args) >= 2 else "any"
            return f"object<string, {value_type}>"

        if isinstance(annotation, type):
            if issubclass(annotation, BaseModel):
                return "object"
            if issubclass(annotation, Enum):
                values = ", ".join(json.dumps(member.value, ensure_ascii=False) for member in annotation)
                return f"enum({values})"
            if annotation is str:
                return "string"
            if annotation is int:
                return "integer"
            if annotation is float:
                return "number"
            if annotation is bool:
                return "boolean"

        if annotation is Any:
            return "any"

        return "any"

    @staticmethod
    def _annotation_model(annotation: Any) -> type[BaseModel] | None:
        """提取注解中直接嵌套的 BaseModel 类型。"""

        annotation = StructuredOutputRunner._unwrap_annotated(annotation)
        origin = get_origin(annotation)
        if origin in (Union, UnionType):
            for candidate in get_args(annotation):
                nested = StructuredOutputRunner._annotation_model(candidate)
                if nested is not None:
                    return nested
            return None

        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation
        return None

    @staticmethod
    def _list_item_model(annotation: Any) -> type[BaseModel] | None:
        """提取列表元素中的 BaseModel 类型。"""

        annotation = StructuredOutputRunner._unwrap_annotated(annotation)
        origin = get_origin(annotation)
        if origin not in (list, tuple):
            return None

        args = get_args(annotation)
        if not args:
            return None
        return StructuredOutputRunner._annotation_model(args[0])

    @staticmethod
    def _unwrap_annotated(annotation: Any) -> Any:
        """去掉 Annotated 外壳，便于后续分析。"""

        origin = get_origin(annotation)
        if origin is None:
            return annotation

        if str(origin).endswith("Annotated"):
            args = get_args(annotation)
            if args:
                return args[0]
        return annotation

    def _repair_json_text(self, raw_text: str) -> str:
        """尽力从原始文本中提取并修复 JSON。"""

        normalized = raw_text.strip()
        normalized = _CODE_FENCE_PATTERN.sub("", normalized).strip()
        normalized = (
            normalized.replace("“", '"')
            .replace("”", '"')
            .replace("‘", "'")
            .replace("’", "'")
        )

        start_positions = [index for index in (normalized.find("{"), normalized.find("[")) if index >= 0]
        if start_positions:
            start_index = min(start_positions)
            end_index = max(normalized.rfind("}"), normalized.rfind("]"))
            if end_index >= start_index:
                normalized = normalized[start_index : end_index + 1]

        normalized = _TRAILING_COMMA_PATTERN.sub(r"\1", normalized)
        normalized = _SINGLE_QUOTED_KEY_PATTERN.sub(r'\g<prefix>"\g<key>":', normalized)
        normalized = _SINGLE_QUOTED_VALUE_PATTERN.sub(r': "\g<value>"\g<suffix>', normalized)

        return normalized

    @staticmethod
    def _extract_raw_text(raw_response: Any) -> str:
        """从各种响应对象中提取原始文本。"""

        if raw_response is None:
            return ""

        if isinstance(raw_response, str):
            return raw_response

        content = getattr(raw_response, "content", raw_response)
        return str(content)

    @staticmethod
    def _preview_raw_response(raw_response: Any, *, max_length: int = 240) -> str:
        """压缩展示原始响应，便于日志排障。"""

        if raw_response is None:
            return "None"

        preview = str(raw_response).replace("\n", " ").strip()
        if len(preview) > max_length:
            return preview[:max_length] + "..."
        return preview


__all__ = ["StructuredOutputRunner"]
