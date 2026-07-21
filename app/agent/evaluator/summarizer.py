"""面试统一评估汇总器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import config
from app.core.structured_output import StructuredOutputRunner
from app.models.interview import InterviewQuestionEvaluationDTO

DEFAULT_EVALUATION_PROMPT_DIR = Path(__file__).resolve().parents[3] / "prompts" / "evaluation"

FIXED_DIMENSION_KEYS = (
    "project_experience",
    "technical_depth",
    "skill_match",
    "content_completeness",
    "communication_clarity",
)


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
        question_groups: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """生成汇总报告，失败时回退到规则聚合。"""

        question_group_summaries = self._build_question_group_summaries(
            question_evaluations,
            question_groups or [],
        )
        weighted_overall_score = self._calculate_grouped_overall_score(
            question_group_summaries,
            question_evaluations,
        )

        if not question_evaluations:
            return self._fallback_summary(
                skill_name=skill_name,
                question_evaluations=question_evaluations,
                question_groups=question_groups or [],
            )

        try:
            prompt_text = self._render_prompt(
                skill_name=skill_name,
                skill_markdown=skill_markdown,
                reference_markdown=reference_markdown,
                rubric_markdown=rubric_markdown,
                question_evaluations=question_evaluations,
                question_groups=question_groups or [],
                question_group_summaries=question_group_summaries,
                weighted_overall_score=weighted_overall_score,
            )
            output = await self._structured_output_runner.ainvoke(
                prompt_text=prompt_text,
                schema=_InterviewSummaryOutput,
                model=model,
                temperature=0.2,
            )
            dimension_scores = self._normalize_dimension_scores(
                output.dimension_scores,
                question_group_summaries=question_group_summaries,
                fallback_score=weighted_overall_score,
            )
            strengths, weaknesses, suggestions = self._curate_summary_lists(
                strengths=self._collect_unique_items(output.strengths),
                weaknesses=self._collect_unique_items(output.weaknesses),
                suggestions=self._collect_unique_items(output.suggestions),
                overall_score=weighted_overall_score,
            )
            return {
                "summary_text": output.summary_text.strip(),
                "overall_score": weighted_overall_score,
                "overall_rating": self._rating_from_score(weighted_overall_score),
                "strengths": strengths,
                "weaknesses": weaknesses,
                "suggestions": suggestions,
                "dimension_scores": dimension_scores,
                "question_group_summaries": question_group_summaries,
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
                question_groups=question_groups or [],
            )
            fallback["generation_mode"] = "fallback"
            return fallback

    def _fallback_summary(
        self,
        *,
        skill_name: str,
        question_evaluations: list[InterviewQuestionEvaluationDTO],
        question_groups: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """基于题目级结果聚合出稳定汇总。"""

        question_group_summaries = self._build_question_group_summaries(
            question_evaluations,
            question_groups or [],
        )
        overall_score = self._calculate_grouped_overall_score(
            question_group_summaries,
            question_evaluations,
        )
        strengths = self._collect_unique_items([item for evaluation in question_evaluations for item in evaluation.strengths])
        weaknesses = self._collect_unique_items([item for evaluation in question_evaluations for item in evaluation.weaknesses])
        suggestions = self._collect_unique_items([item for evaluation in question_evaluations for item in evaluation.suggestions])

        if not strengths:
            strengths = ["能围绕题目持续作答，具备基本工程表达能力"]
        if not weaknesses:
            weaknesses = ["工程细节、技术权衡和深层原理的展开仍有提升空间"]
        if not suggestions:
            suggestions = ["围绕真实项目复盘关键设计决策、故障处理和量化结果"]
        strengths, weaknesses, suggestions = self._curate_summary_lists(
            strengths=strengths,
            weaknesses=weaknesses,
            suggestions=suggestions,
            overall_score=overall_score,
        )

        summary_text = (
            f"{skill_name} 面试已完成，共评估 {len(question_evaluations)} 道题，"
            f"整体评分 {overall_score} 分。"
        )
        return {
            "summary_text": summary_text,
            "overall_score": overall_score,
            "overall_rating": self._rating_from_score(overall_score),
            "strengths": strengths,
            "weaknesses": weaknesses,
            "suggestions": suggestions,
            "dimension_scores": self._build_dimension_scores(
                question_group_summaries=question_group_summaries,
                fallback_score=overall_score,
            ),
            "question_group_summaries": question_group_summaries,
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
        question_groups: list[dict[str, Any]],
        question_group_summaries: list[dict[str, Any]],
        weighted_overall_score: float,
    ) -> str:
        """渲染汇总 prompt。"""

        template = self._load_template("summarizer.st")
        variables = {
            "skill_name": skill_name,
            "skill_markdown": self._wrap("skill_markdown", skill_markdown),
            "reference_markdown": self._wrap("reference_markdown", reference_markdown),
            "rubric_markdown": self._wrap("rubric_markdown", rubric_markdown),
            "question_evaluations_section": self._render_question_evaluations(question_evaluations),
            "question_groups_section": self._render_question_groups(question_groups, question_group_summaries),
            "weighted_overall_score": weighted_overall_score,
            "fixed_dimension_keys": ", ".join(FIXED_DIMENSION_KEYS),
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
    def _render_question_groups(
        question_groups: list[dict[str, Any]],
        question_group_summaries: list[dict[str, Any]],
    ) -> str:
        """Render layered main-question groups for the final summarizer."""

        summary_by_key = {
            str(item.get("main_question_key", "")): item
            for item in question_group_summaries
        }
        lines: list[str] = []
        for group in question_groups:
            group_key = str(group.get("main_question_key", ""))
            summary = summary_by_key.get(group_key, {})
            lines.append("<question_group>")
            lines.append(f"  <main_question_key>{group_key}</main_question_key>")
            lines.append(f"  <weighted_score>{summary.get('weighted_score', 'n/a')}</weighted_score>")
            lines.append(f"  <main_score>{summary.get('main_score', 'n/a')}</main_score>")
            lines.append(f"  <follow_up_score>{summary.get('follow_up_score', 'n/a')}</follow_up_score>")
            lines.append(f"  <coverage_status>{group.get('coverage_status', 'unknown')}</coverage_status>")
            lines.append(f"  <coverage_confidence>{group.get('coverage_confidence', 0.0)}</coverage_confidence>")
            lines.append(f"  <observed_signals>{'; '.join(group.get('observed_signals', []))}</observed_signals>")
            lines.append(f"  <missing_signals>{'; '.join(group.get('missing_signals', []))}</missing_signals>")
            for question in list(group.get("questions", [])):
                lines.append("  <group_question>")
                lines.append(f"    <question_key>{question.get('question_key', '')}</question_key>")
                lines.append(f"    <question_role>{question.get('question_role', 'main')}</question_role>")
                lines.append("  </group_question>")
            lines.append("</question_group>")
        return "\n".join(lines) or "<question_groups />"

    @classmethod
    def _build_question_group_summaries(
        cls,
        question_evaluations: list[InterviewQuestionEvaluationDTO],
        question_groups: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Aggregate per-question results into weighted main-question groups."""

        evaluation_by_key = {evaluation.question_key: evaluation for evaluation in question_evaluations}
        summaries: list[dict[str, Any]] = []
        if not question_groups:
            for evaluation in question_evaluations:
                summaries.append(
                    {
                        "main_question_key": evaluation.question_key,
                        "weighted_score": round(float(evaluation.score), 1),
                        "main_score": round(float(evaluation.score), 1),
                        "follow_up_score": None,
                        "question_count": 1,
                        "coverage_confidence": 0.0,
                        "missing_signal_count": 0,
                    }
                )
            return summaries

        for group in question_groups:
            questions = list(group.get("questions", []))
            main_scores: list[float] = []
            follow_up_scores: list[float] = []
            for question in questions:
                evaluation = evaluation_by_key.get(str(question.get("question_key", "")))
                if evaluation is None:
                    continue
                if str(question.get("question_role", "main")) == "follow_up":
                    follow_up_scores.append(float(evaluation.score))
                else:
                    main_scores.append(float(evaluation.score))
            all_scores = [*main_scores, *follow_up_scores]
            if not all_scores:
                continue
            main_score = sum(main_scores) / len(main_scores) if main_scores else sum(all_scores) / len(all_scores)
            follow_up_score = sum(follow_up_scores) / len(follow_up_scores) if follow_up_scores else None
            if follow_up_score is None:
                weighted_score = main_score
            elif main_scores:
                weighted_score = main_score * 0.65 + follow_up_score * 0.35
            else:
                weighted_score = follow_up_score
            summaries.append(
                {
                    "main_question_key": str(group.get("main_question_key", "")),
                    "weighted_score": round(weighted_score, 1),
                    "main_score": round(main_score, 1),
                    "follow_up_score": round(follow_up_score, 1) if follow_up_score is not None else None,
                    "question_count": len(all_scores),
                    "coverage_confidence": float(group.get("coverage_confidence", 0.0) or 0.0),
                    "missing_signal_count": len(group.get("missing_signals", [])),
                }
            )
        return summaries

    @staticmethod
    def _calculate_grouped_overall_score(
        question_group_summaries: list[dict[str, Any]],
        question_evaluations: list[InterviewQuestionEvaluationDTO],
    ) -> float:
        """Calculate final score by averaging main-question groups, not raw questions."""

        group_scores = [
            float(item.get("weighted_score", 0.0))
            for item in question_group_summaries
            if item.get("weighted_score") is not None
        ]
        if group_scores:
            return round(sum(group_scores) / len(group_scores), 1)
        scores = [float(evaluation.score) for evaluation in question_evaluations]
        return round(sum(scores) / len(scores), 1) if scores else 0.0

    @classmethod
    def _normalize_dimension_scores(
        cls,
        raw_scores: dict[str, Any],
        *,
        question_group_summaries: list[dict[str, Any]],
        fallback_score: float,
    ) -> dict[str, float]:
        """Force report dimensions into the stable radar-chart contract."""

        alias_map = {
            "project": "project_experience",
            "project_experience": "project_experience",
            "experience": "project_experience",
            "technical_depth": "technical_depth",
            "depth": "technical_depth",
            "skill_match": "skill_match",
            "skills": "skill_match",
            "content_completeness": "content_completeness",
            "completeness": "content_completeness",
            "communication_clarity": "communication_clarity",
            "clarity": "communication_clarity",
            "implementation_clarity": "communication_clarity",
            "problem_solving": "technical_depth",
        }
        normalized: dict[str, float] = {}
        for key, value in dict(raw_scores or {}).items():
            canonical_key = alias_map.get(str(key).strip(), str(key).strip())
            if canonical_key not in FIXED_DIMENSION_KEYS:
                continue
            try:
                normalized[canonical_key] = max(0.0, min(100.0, round(float(value), 1)))
            except (TypeError, ValueError):
                continue

        fallback_dimensions = cls._build_dimension_scores(
            question_group_summaries=question_group_summaries,
            fallback_score=fallback_score,
        )
        for key in FIXED_DIMENSION_KEYS:
            normalized.setdefault(key, fallback_dimensions[key])
        return {key: normalized[key] for key in FIXED_DIMENSION_KEYS}

    @staticmethod
    def _build_dimension_scores(
        *,
        question_group_summaries: list[dict[str, Any]],
        fallback_score: float,
    ) -> dict[str, float]:
        """Build stable dimension scores from grouped evaluation signals."""

        if not question_group_summaries:
            average_score = round(float(fallback_score), 1)
            return {key: average_score for key in FIXED_DIMENSION_KEYS}

        average_score = round(float(fallback_score), 1)
        confidence_values = [
            float(item.get("coverage_confidence", 0.0) or 0.0)
            for item in question_group_summaries
        ]
        average_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
        missing_signal_count = sum(
            int(item.get("missing_signal_count", 0) or 0)
            for item in question_group_summaries
        )
        completeness_score = max(
            0.0,
            min(100.0, round(average_score * 0.7 + average_confidence * 100 * 0.3 - missing_signal_count * 1.5, 1)),
        )
        return {
            "project_experience": max(0.0, min(100.0, round(average_score + 1.0, 1))),
            "technical_depth": average_score,
            "skill_match": max(0.0, min(100.0, round(average_score + average_confidence * 6.0 - 3.0, 1))),
            "content_completeness": completeness_score,
            "communication_clarity": max(0.0, min(100.0, round(average_score + 2.0, 1))),
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

    @classmethod
    def _curate_summary_lists(
        cls,
        *,
        strengths: list[str],
        weaknesses: list[str],
        suggestions: list[str],
        overall_score: float,
    ) -> tuple[list[str], list[str], list[str]]:
        """控制整场报告的亮点/不足/建议数量，避免高分报告显得失衡。"""

        strengths = cls._collect_unique_items(strengths)[:5]
        weaknesses = cls._collect_unique_items(
            [item for item in weaknesses if not cls._looks_like_answer_completion_instruction(item)]
        )
        suggestions = cls._collect_unique_items(
            [item for item in suggestions if not cls._looks_like_answer_completion_instruction(item)]
        )

        if not strengths:
            strengths = ["能围绕题目持续作答，具备基本工程表达能力"]
        if not weaknesses:
            weaknesses = ["工程细节、技术权衡和深层原理的展开仍有提升空间"]
        if not suggestions:
            suggestions = ["围绕真实项目复盘关键设计决策、故障处理和量化结果"]

        if overall_score >= 85:
            weakness_limit = min(2, max(1, len(strengths)))
        elif overall_score >= 80:
            weakness_limit = min(3, max(1, len(strengths)))
        elif overall_score >= 70:
            weakness_limit = 4
        else:
            weakness_limit = 5

        suggestion_limit = 2 if overall_score >= 85 else 3
        return strengths[:5], weaknesses[:weakness_limit], suggestions[:suggestion_limit]

    @staticmethod
    def _looks_like_answer_completion_instruction(item: str) -> bool:
        """识别“补充某个回答”的句式，这类内容不适合放在整场不足点。"""

        normalized = item.strip()
        completion_prefixes = ("补充", "提供", "举例说明", "说明一次", "展开说明")
        return normalized.startswith(completion_prefixes)

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
