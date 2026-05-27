"""批量题目评估器。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from app.config import config
from app.core.structured_output import StructuredOutputRunner
from app.models.interview import InterviewQuestionEvaluationDTO

DEFAULT_EVALUATION_PROMPT_DIR = Path(__file__).resolve().parents[3] / "prompts" / "evaluation"

_KEYWORDS_FOR_SCORE = ("实现", "落地", "权衡", "监控", "回滚", "故障", "性能", "事务", "缓存", "一致性")


class _BatchQuestionEvaluationOutput(BaseModel):
    """单批题目评估结构化输出。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    question_key: str = Field(..., min_length=1, description="题目标识")
    score: float = Field(..., ge=0, le=100, description="单题评分")
    rating: str = Field(..., min_length=1, description="评级标签")
    strengths: list[str] = Field(default_factory=list, description="本题亮点")
    weaknesses: list[str] = Field(default_factory=list, description="本题短板")
    suggestions: list[str] = Field(default_factory=list, description="改进建议")
    rationale: str = Field(..., min_length=1, description="评分理由")

    @model_validator(mode="before")
    @classmethod
    def normalize_sequence_fields(cls, value: Any) -> Any:
        """兼容模型把数组字段错误写成字符串的情况。"""

        if not isinstance(value, dict):
            return value

        normalized = dict(value)
        for field_name in ("strengths", "weaknesses", "suggestions"):
            field_value = normalized.get(field_name)
            if isinstance(field_value, str):
                normalized[field_name] = [field_value] if field_value.strip() else []
        return normalized


class _BatchQuestionEvaluationsOutput(BaseModel):
    """批次输出结构。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    question_evaluations: list[_BatchQuestionEvaluationOutput] = Field(
        default_factory=list,
        validation_alias=AliasChoices("question_evaluations", "evaluations"),
        description="逐题评估结果",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_batch_payload(cls, value: Any) -> Any:
        """兼容旧字段名并移除无关批次元数据。"""

        if not isinstance(value, dict):
            return value

        normalized = dict(value)
        normalized.pop("batch_index", None)
        normalized.pop("batch_total", None)
        return normalized


@dataclass(slots=True)
class _NormalizedQuestion:
    """标准化后的待评估题目。"""

    question_key: str
    round_index: int
    question_text: str
    answer_text: str


class BatchEvaluator:
    """按批次对面试问答做逐题评估。"""

    def __init__(
        self,
        *,
        prompt_dir: Path | None = None,
        enable_llm: bool = True,
        llm_factory_fn: Any | None = None,
        batch_size: int | None = None,
    ) -> None:
        self._prompt_dir = prompt_dir or DEFAULT_EVALUATION_PROMPT_DIR
        self._structured_output_runner = StructuredOutputRunner(
            enable_llm=enable_llm,
            llm_factory_fn=llm_factory_fn,
            max_retries=config.interview.structured_output_max_retries,
        )
        self._batch_size = batch_size or config.interview.evaluation_batch_size

    async def evaluate_questions(
        self,
        *,
        skill_name: str,
        skill_markdown: str,
        reference_markdown: str,
        rubric_markdown: str,
        question_items: list[dict[str, Any]],
        model: str | None = None,
    ) -> list[InterviewQuestionEvaluationDTO]:
        """对一组问答按批次做结构化评估。"""

        normalized_items = [self._normalize_question_item(item) for item in question_items]
        if not normalized_items:
            return []

        evaluated_by_key: dict[str, InterviewQuestionEvaluationDTO] = {}
        for batch_index, batch_items in enumerate(self._chunk_items(normalized_items), start=1):
            batch_results = await self._evaluate_single_batch(
                skill_name=skill_name,
                skill_markdown=skill_markdown,
                reference_markdown=reference_markdown,
                rubric_markdown=rubric_markdown,
                batch_items=batch_items,
                batch_index=batch_index,
                batch_total=self._batch_count(len(normalized_items)),
                model=model,
            )
            for evaluation in batch_results:
                evaluated_by_key[evaluation.question_key] = evaluation

        ordered_results: list[InterviewQuestionEvaluationDTO] = []
        for item in normalized_items:
            ordered_results.append(
                evaluated_by_key.get(item.question_key)
                or self._build_fallback_evaluation(item)
            )
        return ordered_results

    async def _evaluate_single_batch(
        self,
        *,
        skill_name: str,
        skill_markdown: str,
        reference_markdown: str,
        rubric_markdown: str,
        batch_items: list[_NormalizedQuestion],
        batch_index: int,
        batch_total: int,
        model: str | None,
    ) -> list[InterviewQuestionEvaluationDTO]:
        """评估单批问答，失败时回退到规则结果。"""

        try:
            prompt_text = self._render_prompt(
                skill_name=skill_name,
                skill_markdown=skill_markdown,
                reference_markdown=reference_markdown,
                rubric_markdown=rubric_markdown,
                batch_items=batch_items,
                batch_index=batch_index,
                batch_total=batch_total,
            )
            output = await self._structured_output_runner.ainvoke(
                prompt_text=prompt_text,
                schema=_BatchQuestionEvaluationsOutput,
                model=model,
                temperature=0.2,
            )
            return self._merge_batch_output(batch_items, output)
        except Exception as exc:
            logger.warning(
                "批量评估触发规则兜底: fallback_applied=true, fallback_type=rule, stage=batch_evaluator, batch_index={}, batch_total={}, error={}",
                batch_index,
                batch_total,
                exc,
            )
            return [self._build_fallback_evaluation(item) for item in batch_items]

    def _merge_batch_output(
        self,
        batch_items: list[_NormalizedQuestion],
        output: _BatchQuestionEvaluationsOutput,
    ) -> list[InterviewQuestionEvaluationDTO]:
        """把模型输出与原始批次对齐。"""

        output_by_key = {item.question_key: item for item in output.question_evaluations}
        merged_results: list[InterviewQuestionEvaluationDTO] = []
        for item in batch_items:
            result = output_by_key.get(item.question_key)
            if result is None:
                logger.warning(
                    "批量评估结果缺项，补齐规则兜底: fallback_applied=true, fallback_type=rule, stage=batch_evaluator_merge, question_key={}",
                    item.question_key,
                )
                merged_results.append(self._build_fallback_evaluation(item))
                continue

            merged_results.append(
                InterviewQuestionEvaluationDTO(
                    question_key=item.question_key,
                    round_index=item.round_index,
                    question_text=item.question_text,
                    answer_text=item.answer_text,
                    score=float(result.score),
                    rating=result.rating.strip() or self._rating_from_score(float(result.score)),
                    strengths=[text.strip() for text in result.strengths if text.strip()],
                    weaknesses=[text.strip() for text in result.weaknesses if text.strip()],
                    suggestions=[text.strip() for text in result.suggestions if text.strip()],
                    rationale=result.rationale.strip() or "基于统一评分基线给出评估。",
                    source="llm",
                )
            )
        return merged_results

    def _build_fallback_evaluation(self, item: _NormalizedQuestion) -> InterviewQuestionEvaluationDTO:
        """基于规则生成稳定的题目评估。"""

        answer_text = item.answer_text.strip()
        score = 35.0 + min(len(answer_text) / 5.0, 35.0)
        keyword_hits = sum(1 for keyword in _KEYWORDS_FOR_SCORE if keyword in answer_text)
        score += min(keyword_hits * 8.0, 24.0)
        if len(answer_text) < 40:
            score -= 10.0
        score = max(10.0, min(100.0, round(score, 1)))

        strengths: list[str] = []
        if len(answer_text) >= 80:
            strengths.append("回答包含较多实现细节")
        if keyword_hits >= 2:
            strengths.append("覆盖了多个工程落地点")
        if not strengths:
            strengths.append("表达整体清晰")

        weaknesses: list[str] = []
        if len(answer_text) < 80:
            weaknesses.append("细节展开仍然不足")
        if keyword_hits == 0:
            weaknesses.append("缺少具体实现或权衡说明")
        if not weaknesses:
            weaknesses.append("可进一步补充边界条件")

        suggestions = [
            "补充一次真实项目中的实现过程或故障处理细节",
            "说明关键技术选择背后的权衡理由",
        ]
        rationale = (
            "基于回答长度、工程关键词覆盖度和落地细节进行规则评分，"
            "用于在模型失效时提供稳定兜底。"
        )
        return InterviewQuestionEvaluationDTO(
            question_key=item.question_key,
            round_index=item.round_index,
            question_text=item.question_text,
            answer_text=answer_text,
            score=score,
            rating=self._rating_from_score(score),
            strengths=strengths,
            weaknesses=weaknesses,
            suggestions=suggestions,
            rationale=rationale,
            source="fallback",
        )

    def _render_prompt(
        self,
        *,
        skill_name: str,
        skill_markdown: str,
        reference_markdown: str,
        rubric_markdown: str,
        batch_items: list[_NormalizedQuestion],
        batch_index: int,
        batch_total: int,
    ) -> str:
        """渲染批次评估 prompt。"""

        template = self._load_template("batch_evaluator.st")
        variables = {
            "skill_name": skill_name,
            "skill_markdown": self._wrap("skill_markdown", skill_markdown),
            "reference_markdown": self._wrap("reference_markdown", reference_markdown),
            "rubric_markdown": self._wrap("rubric_markdown", rubric_markdown),
            "batch_index": batch_index,
            "batch_total": batch_total,
            "batch_question_section": self._render_batch_items(batch_items),
        }
        return template.format_map(_SafePromptVariables(variables))

    def _render_batch_items(self, batch_items: list[_NormalizedQuestion]) -> str:
        """把单批问答渲染成稳定文本。"""

        lines: list[str] = []
        for item in batch_items:
            lines.append("<question_item>")
            lines.append(f"  <question_key>{item.question_key}</question_key>")
            lines.append(f"  <round_index>{item.round_index}</round_index>")
            lines.append(f"  <question_text>{self._escape_text(item.question_text)}</question_text>")
            lines.append(f"  <answer_text>{self._escape_text(item.answer_text)}</answer_text>")
            lines.append("</question_item>")
        return "\n".join(lines)

    @staticmethod
    def _escape_text(value: str) -> str:
        """避免 prompt 中的标签被意外截断。"""

        return value.replace("<", "＜").replace(">", "＞").strip()

    @staticmethod
    def _wrap(tag_name: str, content: str) -> str:
        """给不可信文本加边界。"""

        return f"<{tag_name}>\n{content.strip()}\n</{tag_name}>"

    def _load_template(self, template_name: str) -> str:
        """读取评估 prompt 模板。"""

        return (self._prompt_dir / template_name).read_text(encoding="utf-8")

    @staticmethod
    def _normalize_question_item(item: dict[str, Any]) -> _NormalizedQuestion:
        """规范化输入数据。"""

        return _NormalizedQuestion(
            question_key=str(item.get("question_key", "")).strip(),
            round_index=int(item.get("round_index", 0)),
            question_text=str(item.get("question_text", "")).strip(),
            answer_text=str(item.get("answer_text", "")).strip(),
        )

    def _chunk_items(self, items: list[_NormalizedQuestion]) -> list[list[_NormalizedQuestion]]:
        """按配置批量切分。"""

        batch_size = max(1, int(self._batch_size))
        return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]

    def _batch_count(self, total_items: int) -> int:
        """返回批次数量。"""

        batch_size = max(1, int(self._batch_size))
        return max(1, (total_items + batch_size - 1) // batch_size)

    @staticmethod
    def _rating_from_score(score: float) -> str:
        """根据分数计算评级。"""

        if score >= 90:
            return "excellent"
        if score >= 80:
            return "good"
        if score >= 70:
            return "pass"
        if score >= 60:
            return "needs_improvement"
        return "weak"


class _SafePromptVariables(dict[str, Any]):
    """prompt 渲染时提供空字符串兜底。"""

    def __missing__(self, key: str) -> str:
        return ""


__all__ = ["BatchEvaluator"]
