"""面试统一评估汇总器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import config
from app.core.structured_output import StructuredOutputRunner
from app.models.interview import InterviewQuestionEvaluationDTO

DEFAULT_EVALUATION_PROMPT_DIR = Path(__file__).resolve().parents[3] / "prompts" / "evaluation"


class _InterviewSummaryOutput(BaseModel):
    """汇总结果结构化输出。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    summary_text: str = Field(..., min_length=1, description="报告摘要")
    overall_score: float = Field(..., ge=0, le=100, description="总体评分")
    overall_rating: str = Field(..., min_length=1, description="总体评级")
    strengths: list[str] = Field(default_factory=list, description="整体亮点")
    weaknesses: list[str] = Field(default_factory=list, description="整体短板")
    suggestions: list[str] = Field(default_factory=list, description="整体建议")
    dimension_scores: dict[str, float] = Field(default_factory=dict, description="维度分")

    @model_validator(mode="before")
    @classmethod
    def normalize_summary_payload(cls, value: Any) -> Any:
        """兼容字段偏移与字符串数组化。"""

        if not isinstance(value, dict):
            return value

        normalized = dict(value)

        if "summary_text" not in normalized and "summary" in normalized:
            normalized["summary_text"] = normalized.pop("summary")

        alias_map = {
            "strengths": "highlights",
            "weaknesses": "shortcomings",
            "suggestions": "improvement_suggestions",
            "dimension_scores": "dimensions",
        }
        for canonical_name, alias_name in alias_map.items():
            if canonical_name not in normalized and alias_name in normalized:
                normalized[canonical_name] = normalized.pop(alias_name)

        for field_name in ("strengths", "weaknesses", "suggestions"):
            field_value = normalized.get(field_name)
            if isinstance(field_value, str):
                normalized[field_name] = [field_value] if field_value.strip() else []

        if "dimension_scores" not in normalized:
            dimension_scores = {
                key.replace("_score", ""): value
                for key, value in normalized.items()
                if key.endswith("_score") and isinstance(value, (int, float))
            }
            if dimension_scores:
                normalized["dimension_scores"] = dimension_scores

        return normalized


class InterviewSummarizer:
    """基于逐题评估生成整场面试汇总。"""

    def __init__(
        self,
        *,
        prompt_dir: Path | None = None,
        enable_llm: bool = True,
        llm_factory_fn: Any | None = None,
    ) -> None:
        self._prompt_dir = prompt_dir or DEFAULT_EVALUATION_PROMPT_DIR
        self._structured_output_runner = StructuredOutputRunner(
            enable_llm=enable_llm,
            llm_factory_fn=llm_factory_fn,
            max_retries=config.interview.structured_output_max_retries,
        )

    async def summarize(
        self,
        *,
        skill_name: str,
        skill_markdown: str,
        reference_markdown: str,
        rubric_markdown: str,
        question_evaluations: list[InterviewQuestionEvaluationDTO],
        model: str | None = None,
    ) -> dict[str, Any]:
        """生成汇总报告，失败时回退到规则聚合。"""

        if not question_evaluations:
            return self._fallback_summary(skill_name=skill_name, question_evaluations=question_evaluations)

        try:
            prompt_text = self._render_prompt(
                skill_name=skill_name,
                skill_markdown=skill_markdown,
                reference_markdown=reference_markdown,
                rubric_markdown=rubric_markdown,
                question_evaluations=question_evaluations,
            )
            output = await self._structured_output_runner.ainvoke(
                prompt_text=prompt_text,
                schema=_InterviewSummaryOutput,
                model=model,
                temperature=0.2,
            )
            return {
                "summary_text": output.summary_text.strip(),
                "overall_score": float(output.overall_score),
                "overall_rating": output.overall_rating.strip(),
                "strengths": [item.strip() for item in output.strengths if item.strip()],
                "weaknesses": [item.strip() for item in output.weaknesses if item.strip()],
                "suggestions": [item.strip() for item in output.suggestions if item.strip()],
                "dimension_scores": {
                    key: float(value) for key, value in output.dimension_scores.items()
                },
                "generation_mode": "llm",
            }
        except Exception as exc:
            logger.warning(
                "评估汇总触发规则兜底: fallback_applied=true, fallback_type=rule, stage=summarizer, error={}",
                exc,
            )
            fallback = self._fallback_summary(
                skill_name=skill_name,
                question_evaluations=question_evaluations,
            )
            fallback["generation_mode"] = "fallback"
            return fallback

    def _fallback_summary(
        self,
        *,
        skill_name: str,
        question_evaluations: list[InterviewQuestionEvaluationDTO],
    ) -> dict[str, Any]:
        """基于题目级结果聚合出稳定汇总。"""

        scores = [evaluation.score for evaluation in question_evaluations]
        overall_score = round(sum(scores) / len(scores), 1) if scores else 0.0
        strengths = self._collect_unique_items([item for evaluation in question_evaluations for item in evaluation.strengths])
        weaknesses = self._collect_unique_items([item for evaluation in question_evaluations for item in evaluation.weaknesses])
        suggestions = self._collect_unique_items([item for evaluation in question_evaluations for item in evaluation.suggestions])

        if not strengths:
            strengths = ["能围绕题目持续作答，具备基本工程表达能力"]
        if not weaknesses:
            weaknesses = ["需要进一步补充实现细节和权衡依据"]
        if not suggestions:
            suggestions = ["结合真实项目复盘，补充关键设计决策与故障处理过程"]

        summary_text = (
            f"{skill_name} 面试已完成，共评估 {len(question_evaluations)} 道题，"
            f"整体评分 {overall_score} 分。"
        )
        return {
            "summary_text": summary_text,
            "overall_score": overall_score,
            "overall_rating": self._rating_from_score(overall_score),
            "strengths": strengths[:5],
            "weaknesses": weaknesses[:5],
            "suggestions": suggestions[:5],
            "dimension_scores": self._build_dimension_scores(question_evaluations),
            "generation_mode": "fallback",
        }

    def _render_prompt(
        self,
        *,
        skill_name: str,
        skill_markdown: str,
        reference_markdown: str,
        rubric_markdown: str,
        question_evaluations: list[InterviewQuestionEvaluationDTO],
    ) -> str:
        """渲染汇总 prompt。"""

        template = self._load_template("summarizer.st")
        variables = {
            "skill_name": skill_name,
            "skill_markdown": self._wrap("skill_markdown", skill_markdown),
            "reference_markdown": self._wrap("reference_markdown", reference_markdown),
            "rubric_markdown": self._wrap("rubric_markdown", rubric_markdown),
            "question_evaluations_section": self._render_question_evaluations(question_evaluations),
        }
        return template.format_map(_SafePromptVariables(variables))

    def _render_question_evaluations(
        self,
        question_evaluations: list[InterviewQuestionEvaluationDTO],
    ) -> str:
        """将题目评估渲染成稳定文本。"""

        lines: list[str] = []
        for evaluation in question_evaluations:
            lines.append("<question_evaluation>")
            lines.append(f"  <question_key>{evaluation.question_key}</question_key>")
            lines.append(f"  <round_index>{evaluation.round_index}</round_index>")
            lines.append(f"  <score>{evaluation.score}</score>")
            lines.append(f"  <rating>{self._escape_text(evaluation.rating)}</rating>")
            lines.append(f"  <strengths>{self._escape_text('；'.join(evaluation.strengths))}</strengths>")
            lines.append(f"  <weaknesses>{self._escape_text('；'.join(evaluation.weaknesses))}</weaknesses>")
            lines.append(f"  <suggestions>{self._escape_text('；'.join(evaluation.suggestions))}</suggestions>")
            lines.append(f"  <rationale>{self._escape_text(evaluation.rationale)}</rationale>")
            lines.append("</question_evaluation>")
        return "\n".join(lines)

    @staticmethod
    def _build_dimension_scores(question_evaluations: list[InterviewQuestionEvaluationDTO]) -> dict[str, float]:
        """基于题目级评估构造简单维度分。"""

        if not question_evaluations:
            return {}

        average_score = round(sum(evaluation.score for evaluation in question_evaluations) / len(question_evaluations), 1)
        return {
            "technical_depth": average_score,
            "implementation_clarity": max(0.0, min(100.0, round(average_score + 2.0, 1))),
            "problem_solving": max(0.0, min(100.0, round(average_score - 1.0, 1))),
        }

    @staticmethod
    def _collect_unique_items(items: list[str]) -> list[str]:
        """去重并保持顺序。"""

        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    @staticmethod
    def _rating_from_score(score: float) -> str:
        """根据分数生成评级。"""

        if score >= 90:
            return "excellent"
        if score >= 80:
            return "good"
        if score >= 70:
            return "pass"
        if score >= 60:
            return "needs_improvement"
        return "weak"

    def _load_template(self, template_name: str) -> str:
        """读取评估 prompt。"""

        return (self._prompt_dir / template_name).read_text(encoding="utf-8")

    @staticmethod
    def _wrap(tag_name: str, content: str) -> str:
        """给不可信文本加边界。"""

        return f"<{tag_name}>\n{content.strip()}\n</{tag_name}>"

    @staticmethod
    def _escape_text(value: str) -> str:
        """避免 prompt 中的标签被意外截断。"""

        return value.replace("<", "＜").replace(">", "＞").strip()


class _SafePromptVariables(dict[str, Any]):
    """prompt 渲染时提供空字符串兜底。"""

    def __missing__(self, key: str) -> str:
        return ""


__all__ = ["InterviewSummarizer"]
