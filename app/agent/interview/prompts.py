"""Interview prompt runner and structured output wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.structured_output import StructuredOutputRunner
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
        self._structured_output_runner = StructuredOutputRunner(
            enable_llm=enable_llm,
            llm_factory_fn=self._llm_factory_fn,
        )

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

        prompt_text = self.render(template_name, **variables)
        return await self._structured_output_runner.ainvoke(
            prompt_text=prompt_text,
            schema=schema,
            model=model,
            temperature=temperature,
        )

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
