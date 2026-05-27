"""统一评估引擎服务。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.evaluator.batch_evaluator import BatchEvaluator
from app.agent.evaluator.summarizer import InterviewSummarizer
from app.config import config
from app.models.interview import (
    InterviewAnswerEntity,
    InterviewAnswerStatus,
    InterviewQuestionEvaluationDTO,
    InterviewReportDTO,
    InterviewReportEntity,
    InterviewReportExportDTO,
    InterviewReportStatus,
    InterviewSessionEntity,
)
from app.repositories.interview_repository import InterviewRepository
from app.utils.exceptions import BusinessException, ErrorCode


def _utc_now() -> datetime:
    """返回带时区的当前 UTC 时间。"""

    return datetime.now(timezone.utc)


class EvaluationService:
    """面试统一评估与报告生成服务。"""

    def __init__(
        self,
        *,
        batch_evaluator: BatchEvaluator | None = None,
        summarizer: InterviewSummarizer | None = None,
        repository_factory: Callable[[AsyncSession], InterviewRepository] = InterviewRepository,
        rubric_root_dir: Path | None = None,
    ) -> None:
        enable_llm = bool(config.dashscope_api_key)
        self._batch_evaluator = batch_evaluator or BatchEvaluator(enable_llm=enable_llm)
        self._summarizer = summarizer or InterviewSummarizer(enable_llm=enable_llm)
        self._repository_factory = repository_factory
        self._rubric_root_dir = Path(rubric_root_dir or config.interview.rubric_root_dir).resolve()

    async def generate_report(
        self,
        session: AsyncSession,
        session_id: str,
    ) -> InterviewReportEntity:
        """为完整面试生成或更新结构化报告。"""

        repository = self._repository_factory(session)
        interview_session, answers, existing_report = await repository.get_session_snapshot(session_id)
        if interview_session is None:
            raise BusinessException(
                code=ErrorCode.INTERVIEW_SESSION_NOT_FOUND,
                message="面试会话不存在",
                http_status=404,
                details={"session_id": session_id},
            )

        if existing_report and existing_report.status == InterviewReportStatus.GENERATED.value:
            return existing_report

        report_entity = existing_report or InterviewReportEntity(session_id=session_id)
        try:
            report_dto = await self._build_generated_report(
                interview_session=interview_session,
                answers=answers,
            )
            report_entity.status = InterviewReportStatus.GENERATED.value
            report_entity.summary_text = report_dto.summary_text
            report_entity.report_json = report_dto.model_dump(mode="json")
            report_entity.score_json = {
                "overall_score": report_dto.overall_score,
                "overall_rating": report_dto.overall_rating,
                "dimension_scores": report_dto.dimension_scores,
                "generation_mode": report_dto.generation_mode,
            }
            report_entity.error_message = None
            report_entity.generated_at = _utc_now()
            self._apply_question_evaluations(answers, report_dto.question_evaluations)
        except Exception as exc:
            logger.exception("统一评估失败: session_id={}", session_id)
            report_dto = self._build_failed_report_dto(
                interview_session=interview_session,
                error_message=str(exc),
            )
            report_entity.status = InterviewReportStatus.FAILED.value
            report_entity.summary_text = report_dto.summary_text
            report_entity.report_json = report_dto.model_dump(mode="json")
            report_entity.score_json = {
                "status": InterviewReportStatus.FAILED.value,
                "message": "统一评估失败",
            }
            report_entity.error_message = str(exc)
            report_entity.generated_at = None

        await repository.upsert_report(report_entity)
        return report_entity

    async def get_report(
        self,
        session: AsyncSession,
        session_id: str,
    ) -> InterviewReportDTO:
        """读取结构化面试报告。"""

        repository = self._repository_factory(session)
        interview_session, answers, report = await repository.get_session_snapshot(session_id)
        if interview_session is None:
            raise BusinessException(
                code=ErrorCode.INTERVIEW_SESSION_NOT_FOUND,
                message="面试会话不存在",
                http_status=404,
                details={"session_id": session_id},
            )
        return self._build_report_dto(
            interview_session=interview_session,
            answers=answers,
            report=report,
        )

    async def export_report(
        self,
        session: AsyncSession,
        session_id: str,
    ) -> InterviewReportExportDTO:
        """导出 Markdown 报告占位内容。"""

        report_dto = await self.get_report(session, session_id)
        file_name = f"interview-report-{session_id}.md"
        if report_dto.status == InterviewReportStatus.GENERATED.value and report_dto.markdown_content:
            return InterviewReportExportDTO(
                session_id=session_id,
                report_status=report_dto.status,
                export_format=config.interview.report_export_format,
                file_name=file_name,
                content=report_dto.markdown_content,
                message="Markdown 报告已生成，可直接用于导出占位。",
            )

        placeholder_content = self._build_placeholder_export_content(report_dto)
        return InterviewReportExportDTO(
            session_id=session_id,
            report_status=report_dto.status,
            export_format=config.interview.report_export_format,
            file_name=file_name,
            content=placeholder_content,
            message="当前返回的是 Markdown 导出占位内容。",
        )

    async def _build_generated_report(
        self,
        *,
        interview_session: InterviewSessionEntity,
        answers: list[InterviewAnswerEntity],
    ) -> InterviewReportDTO:
        """构建完整结构化报告 DTO。"""

        metadata = interview_session.metadata_json or {}
        skill_name = str(metadata.get("skill_display_name", interview_session.skill_id or "通用面试"))
        skill_markdown = str(metadata.get("skill_markdown", ""))
        reference_markdown = str(metadata.get("reference_markdown", ""))
        rubric_name, rubric_path, rubric_markdown = self._load_rubric(interview_session.skill_id or "")
        question_items = self._build_question_items(answers)

        question_evaluations = await self._batch_evaluator.evaluate_questions(
            skill_name=skill_name,
            skill_markdown=skill_markdown,
            reference_markdown=reference_markdown,
            rubric_markdown=rubric_markdown,
            question_items=question_items,
        )
        summary = await self._summarizer.summarize(
            skill_name=skill_name,
            skill_markdown=skill_markdown,
            reference_markdown=reference_markdown,
            rubric_markdown=rubric_markdown,
            question_evaluations=question_evaluations,
        )
        generation_mode = self._merge_generation_mode(question_evaluations, summary.get("generation_mode", "fallback"))
        markdown_content = self._build_markdown_report(
            session_id=interview_session.id,
            skill_name=skill_name,
            question_evaluations=question_evaluations,
            summary=summary,
            rubric_name=rubric_name,
            generation_mode=generation_mode,
        )

        return InterviewReportDTO(
            session_id=interview_session.id,
            skill_id=interview_session.skill_id or "",
            status=InterviewReportStatus.GENERATED.value,
            summary_text=str(summary.get("summary_text", "")).strip() or "统一评估已完成。",
            overall_score=float(summary.get("overall_score", 0.0)),
            overall_rating=str(summary.get("overall_rating", "fallback")).strip(),
            strengths=list(summary.get("strengths", [])),
            weaknesses=list(summary.get("weaknesses", [])),
            suggestions=list(summary.get("suggestions", [])),
            dimension_scores={
                key: float(value) for key, value in dict(summary.get("dimension_scores", {})).items()
            },
            question_evaluations=question_evaluations,
            rubric_name=rubric_name,
            rubric_path=rubric_path,
            generation_mode=generation_mode,
            markdown_content=markdown_content,
            error_message=None,
            generated_at=_utc_now(),
        )

    def _build_report_dto(
        self,
        *,
        interview_session: InterviewSessionEntity,
        answers: list[InterviewAnswerEntity],
        report: InterviewReportEntity | None,
    ) -> InterviewReportDTO:
        """把报告实体转换为稳定 DTO。"""

        if report is None:
            return InterviewReportDTO(
                session_id=interview_session.id,
                skill_id=interview_session.skill_id or "",
                status=InterviewReportStatus.PENDING.value,
                summary_text="面试尚未完成，正式评估报告将在完成后统一生成。",
                overall_score=None,
                overall_rating=None,
                strengths=[],
                weaknesses=[],
                suggestions=[],
                dimension_scores={},
                question_evaluations=[],
                rubric_name=None,
                rubric_path=None,
                generation_mode="pending",
                markdown_content=self._build_pending_markdown(interview_session),
                error_message=None,
                generated_at=None,
            )

        report_json = report.report_json or {}
        question_evaluations = [
            InterviewQuestionEvaluationDTO.model_validate(item)
            for item in report_json.get("question_evaluations", [])
        ]
        if report.status == InterviewReportStatus.GENERATED.value:
            return InterviewReportDTO(
                session_id=interview_session.id,
                skill_id=interview_session.skill_id or "",
                status=report.status,
                summary_text=report.summary_text,
                overall_score=self._coerce_optional_float(report_json.get("overall_score")),
                overall_rating=self._coerce_optional_string(report_json.get("overall_rating")),
                strengths=list(report_json.get("strengths", [])),
                weaknesses=list(report_json.get("weaknesses", [])),
                suggestions=list(report_json.get("suggestions", [])),
                dimension_scores={
                    key: float(value)
                    for key, value in dict(report_json.get("dimension_scores", {})).items()
                },
                question_evaluations=question_evaluations,
                rubric_name=self._coerce_optional_string(report_json.get("rubric_name")),
                rubric_path=self._coerce_optional_string(report_json.get("rubric_path")),
                generation_mode=self._coerce_optional_string(report_json.get("generation_mode")) or "fallback",
                markdown_content=str(report_json.get("markdown_content", "")),
                error_message=report.error_message,
                generated_at=report.generated_at,
            )

        if report.status == InterviewReportStatus.FAILED.value:
            return self._build_failed_report_dto(
                interview_session=interview_session,
                error_message=report.error_message or "统一评估失败",
            )

        return InterviewReportDTO(
            session_id=interview_session.id,
            skill_id=interview_session.skill_id or "",
            status=report.status,
            summary_text=report.summary_text or "报告正在生成中，请稍后刷新。",
            overall_score=None,
            overall_rating=None,
            strengths=[],
            weaknesses=[],
            suggestions=[],
            dimension_scores={},
            question_evaluations=question_evaluations,
            rubric_name=self._coerce_optional_string(report_json.get("rubric_name")),
            rubric_path=self._coerce_optional_string(report_json.get("rubric_path")),
            generation_mode="pending",
            markdown_content=self._build_pending_markdown(interview_session),
            error_message=report.error_message,
            generated_at=None,
        )

    def _build_failed_report_dto(
        self,
        *,
        interview_session: InterviewSessionEntity,
        error_message: str,
    ) -> InterviewReportDTO:
        """构建失败态 DTO。"""

        return InterviewReportDTO(
            session_id=interview_session.id,
            skill_id=interview_session.skill_id or "",
            status=InterviewReportStatus.FAILED.value,
            summary_text="统一评估未能完成，请稍后重试或查看错误信息。",
            overall_score=None,
            overall_rating=None,
            strengths=[],
            weaknesses=[],
            suggestions=[],
            dimension_scores={},
            question_evaluations=[],
            rubric_name=None,
            rubric_path=None,
            generation_mode="failed",
            markdown_content=(
                "# 面试报告生成失败\n\n"
                f"- session_id: {interview_session.id}\n"
                f"- skill_id: {interview_session.skill_id or 'unknown'}\n"
                f"- error: {error_message}\n"
            ),
            error_message=error_message,
            generated_at=None,
        )

    def _apply_question_evaluations(
        self,
        answers: list[InterviewAnswerEntity],
        question_evaluations: list[InterviewQuestionEvaluationDTO],
    ) -> None:
        """把逐题评估结果回写到答案实体。"""

        evaluation_map = {evaluation.question_key: evaluation for evaluation in question_evaluations}
        for answer in answers:
            if not answer.question_key:
                continue
            evaluation = evaluation_map.get(answer.question_key)
            if evaluation is None:
                continue
            answer.answer_status = InterviewAnswerStatus.REVIEWED.value
            answer.score_json = {
                "score": evaluation.score,
                "rating": evaluation.rating,
                "source": evaluation.source,
            }
            answer.feedback_json = {
                "strengths": evaluation.strengths,
                "weaknesses": evaluation.weaknesses,
                "suggestions": evaluation.suggestions,
                "rationale": evaluation.rationale,
            }

    def _build_question_items(self, answers: list[InterviewAnswerEntity]) -> list[dict[str, Any]]:
        """从答案实体提取评估输入。"""

        return [
            {
                "question_key": answer.question_key or f"round-{answer.round_index}",
                "round_index": answer.round_index,
                "question_text": answer.question_text,
                "answer_text": answer.answer_text,
            }
            for answer in answers
        ]

    def _load_rubric(self, skill_id: str) -> tuple[str, str, str]:
        """按 skill_id 优先读取 rubric，不存在时回退 common。"""

        candidate_paths = []
        if skill_id:
            candidate_paths.append(self._rubric_root_dir / f"{skill_id}.md")
        candidate_paths.append(self._rubric_root_dir / "common.md")

        for candidate_path in candidate_paths:
            if candidate_path.exists():
                return (
                    candidate_path.stem,
                    candidate_path.as_posix(),
                    candidate_path.read_text(encoding="utf-8"),
                )

        return (
            "common",
            (self._rubric_root_dir / "common.md").as_posix(),
            (
                "# 通用评分基线\n\n"
                "- 关注技术深度、工程落地、表达清晰度与问题拆解能力。\n"
                "- 回答应尽量体现真实项目经验、边界处理和关键权衡。\n"
            ),
        )

    def _build_markdown_report(
        self,
        *,
        session_id: str,
        skill_name: str,
        question_evaluations: list[InterviewQuestionEvaluationDTO],
        summary: dict[str, Any],
        rubric_name: str,
        generation_mode: str,
    ) -> str:
        """渲染 Markdown 报告正文。"""

        lines = [
            "# 面试评估报告",
            "",
            f"- session_id: {session_id}",
            f"- skill: {skill_name}",
            f"- rubric: {rubric_name}",
            f"- generation_mode: {generation_mode}",
            "",
            "## 总览",
            "",
            f"- overall_score: {summary.get('overall_score', 0.0)}",
            f"- overall_rating: {summary.get('overall_rating', 'fallback')}",
            "",
            str(summary.get("summary_text", "")).strip() or "统一评估已完成。",
            "",
            "## 亮点",
            "",
        ]
        lines.extend([f"- {item}" for item in summary.get("strengths", [])] or ["- 暂无"])
        lines.extend(["", "## 待提升点", ""])
        lines.extend([f"- {item}" for item in summary.get("weaknesses", [])] or ["- 暂无"])
        lines.extend(["", "## 建议", ""])
        lines.extend([f"- {item}" for item in summary.get("suggestions", [])] or ["- 暂无"])
        lines.extend(["", "## 维度分", ""])
        lines.extend(
            [
                f"- {key}: {value}"
                for key, value in dict(summary.get("dimension_scores", {})).items()
            ]
            or ["- 暂无"]
        )
        lines.extend(["", "## 逐题评估", ""])
        if not question_evaluations:
            lines.append("- 本次未形成有效问答。")
            return "\n".join(lines).strip()

        for evaluation in question_evaluations:
            lines.extend(
                [
                    f"### {evaluation.question_key}",
                    "",
                    f"- round_index: {evaluation.round_index}",
                    f"- score: {evaluation.score}",
                    f"- rating: {evaluation.rating}",
                    f"- rationale: {evaluation.rationale}",
                    "",
                    "**亮点**",
                    "",
                ]
            )
            lines.extend([f"- {item}" for item in evaluation.strengths] or ["- 暂无"])
            lines.extend(["", "**短板**", ""])
            lines.extend([f"- {item}" for item in evaluation.weaknesses] or ["- 暂无"])
            lines.extend(["", "**建议**", ""])
            lines.extend([f"- {item}" for item in evaluation.suggestions] or ["- 暂无"])
            lines.append("")
        return "\n".join(lines).strip()

    def _build_pending_markdown(self, interview_session: InterviewSessionEntity) -> str:
        """渲染 pending 状态的 Markdown 占位内容。"""

        return (
            "# 面试报告生成中\n\n"
            f"- session_id: {interview_session.id}\n"
            f"- skill_id: {interview_session.skill_id or 'unknown'}\n"
            "- 当前会话尚未形成正式统一评估，请在面试完成后查看。\n"
        )

    def _build_placeholder_export_content(self, report_dto: InterviewReportDTO) -> str:
        """为 pending 或 failed 状态生成稳定导出内容。"""

        title = "面试报告导出占位"
        if report_dto.status == InterviewReportStatus.FAILED.value:
            title = "面试报告导出失败占位"
        return (
            f"# {title}\n\n"
            f"- session_id: {report_dto.session_id}\n"
            f"- report_status: {report_dto.status}\n"
            f"- message: {report_dto.summary_text or '暂无正式内容'}\n"
        )

    @staticmethod
    def _merge_generation_mode(
        question_evaluations: list[InterviewQuestionEvaluationDTO],
        summary_mode: str,
    ) -> str:
        """汇总题目级与总览级的生成模式。"""

        sources = {evaluation.source for evaluation in question_evaluations}
        if summary_mode:
            sources.add(summary_mode)
        if sources == {"llm"}:
            return "llm"
        if "llm" in sources and len(sources) > 1:
            return "mixed"
        if "failed" in sources:
            return "failed"
        return "fallback"

    @staticmethod
    def _coerce_optional_string(value: Any) -> str | None:
        """把可选值安全转成字符串。"""

        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _coerce_optional_float(value: Any) -> float | None:
        """把可选值安全转成浮点数。"""

        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


evaluation_service = EvaluationService()


__all__ = ["EvaluationService", "evaluation_service"]
