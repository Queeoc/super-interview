"""Phase 5 interview prompt runner and structured output wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

from loguru import logger
from pydantic import BaseModel

from app.core.llm_factory import llm_factory

TModel = TypeVar("TModel", bound=BaseModel)

DEFAULT_INTERVIEW_PROMPT_DIR = Path(__file__).resolve().parents[3] / "prompts" / "interview"


class InterviewPromptRunner:
    """统一管理 interview 目录下的 prompt 模板和结构化调用。"""

    def __init__(
        self,
        *,
        prompt_dir: Path | None = None,
        enable_llm: bool = True,
        llm_factory_fn: Any | None = None,
    ) -> None:
        self._prompt_dir = prompt_dir or DEFAULT_INTERVIEW_PROMPT_DIR
        self._enable_llm = enable_llm
        self._llm_factory_fn = llm_factory_fn or llm_factory.create_chat_model
        self._template_cache: dict[str, str] = {}

    def load_template(self, template_name: str) -> str:
        """读取并缓存 prompt 模板。"""

        if template_name in self._template_cache:
            return self._template_cache[template_name]

        template_path = self._prompt_dir / template_name
        template_text = template_path.read_text(encoding="utf-8")
        self._template_cache[template_name] = template_text
        return template_text

    def render(self, template_name: str, **variables: Any) -> str:
        """按模板变量渲染 prompt。"""

        template_text = self.load_template(template_name)
        safe_variables = _SafePromptVariables({key: value for key, value in variables.items()})
        return template_text.format_map(safe_variables)

    async def ainvoke_structured(
        self,
        *,
        template_name: str,
        schema: type[TModel],
        variables: dict[str, Any],
        model: str | None = None,
        temperature: float = 0.2,
    ) -> TModel:
        """调用 LLM，优先使用 provider-native `json_schema` 输出。"""

        if not self._enable_llm:
            raise RuntimeError("LLM 已禁用，使用规则降级")

        prompt_text = self._build_structured_prompt(
            template_name=template_name,
            schema=schema,
            variables=variables,
        )
        llm = self._llm_factory_fn(
            model=model,
            temperature=temperature,
            streaming=False,
        )
        logger.info("执行结构化 interview prompt: template={}", template_name)
        structured_llm = llm.with_structured_output(
            schema,
            method="json_schema",
            strict=True,
            include_raw=True,
        )

        response = await structured_llm.ainvoke(prompt_text)
        if isinstance(response, schema):
            return response

        if not isinstance(response, dict):
            raise ValueError(f"structured output 返回类型异常: {type(response).__name__}")

        parsing_error = response.get("parsing_error")
        if parsing_error is not None:
            logger.warning(
                "interview structured output 解析失败: template={}, schema={}, raw={}, error={}",
                template_name,
                schema.__name__,
                self._preview_raw_response(response.get("raw")),
                parsing_error,
            )
            if isinstance(parsing_error, BaseException):
                raise ValueError("structured output 解析失败") from parsing_error
            raise ValueError(f"structured output 解析失败: {parsing_error}")

        parsed = response.get("parsed")
        if parsed is None:
            raise ValueError(
                f"structured output 缺少 parsed 结果: template={template_name}, schema={schema.__name__}"
            )

        if isinstance(parsed, schema):
            return parsed
        return schema.model_validate(parsed, strict=True)

    def _build_structured_prompt(
        self,
        *,
        template_name: str,
        schema: type[TModel],
        variables: dict[str, Any],
    ) -> str:
        """为结构化输出补充最小化的 JSON 兼容提示。"""

        rendered_prompt = self.render(template_name, **variables)
        json_instruction = (
            "\n\n## Structured Output\n"
            "Return only a JSON object.\n"
            f"The JSON object must satisfy schema `{schema.__name__}` exactly.\n"
            "Do not add markdown, code fences, explanations, or extra keys.\n"
        )
        return rendered_prompt + json_instruction

    @staticmethod
    def _preview_raw_response(raw_response: Any, *, max_length: int = 240) -> str:
        """压缩展示原始响应，便于日志排障。"""

        if raw_response is None:
            return "None"

        content = getattr(raw_response, "content", raw_response)
        preview = str(content).replace("\n", " ").strip()
        if len(preview) > max_length:
            return preview[:max_length] + "..."
        return preview

    @staticmethod
    def wrap_untrusted_text(tag_name: str, content: str) -> str:
        """给不可信文本加显式边界。"""

        return f"<{tag_name}>\n{content.strip()}\n</{tag_name}>"

    @staticmethod
    def render_categories(categories: list[dict[str, Any]]) -> str:
        """把 Skill 分类配置渲染成稳定文本。"""

        if not categories:
            return "- GENERAL | 通用问答 | NORMAL"

        lines: list[str] = []
        for category in categories:
            lines.append(
                "- {key} | {label} | {priority}".format(
                    key=category.get("key", "GENERAL"),
                    label=category.get("label", "通用问答"),
                    priority=category.get("priority", "NORMAL"),
                )
            )
        return "\n".join(lines)


class _SafePromptVariables(dict[str, Any]):
    """prompt 渲染时提供空字符串兜底。"""

    def __missing__(self, key: str) -> str:
        return ""
