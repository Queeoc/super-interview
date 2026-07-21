"""Interview answer observer that updates plan evidence before replanning."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from .prompts import InterviewPromptRunner
from .state import (
    AnswerObservation,
    InterviewState,
    QuestionBlueprint,
    TopicCoverageStatus,
    clone_state,
    get_current_blueprint,
    get_current_question,
    get_main_question_coverage,
    hydrate_plan_state,
    resolve_main_question_key,
    utc_now_iso,
)


class _PlanObservationOutput(BaseModel):
    """Structured answer observation output."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    answer_text_summary: str = Field(default="", description="One-sentence answer summary")
    observed_signals: list[str] = Field(default_factory=list, description="Signals already observed")
    missing_signals: list[str] = Field(default_factory=list, description="Signals still missing")
    main_question_status: str = Field(
        default="partial",
        description="Main question status: not_started / partial / covered",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Coverage confidence")
    suggested_action: str = Field(
        default="follow_up",
        description="Suggested next step from the observer perspective",
    )
    reasoning: str = Field(default="", description="Why the main question is or is not complete")


class InterviewPlanObserver:
    """Convert the latest raw answer into plan evidence and coverage updates."""

    def __init__(self, prompt_runner: InterviewPromptRunner) -> None:
        self._prompt_runner = prompt_runner

    async def run(self, state: InterviewState) -> InterviewState:
        """Observe the latest answer and update plan progress."""

        working_state = clone_state(state)
        hydrate_plan_state(working_state)
        current_question = get_current_question(working_state)
        blueprint = get_current_blueprint(working_state)
        if current_question is None or blueprint is None:
            return working_state

        main_question_key = resolve_main_question_key(
            working_state,
            question=current_question,
        ) or str(blueprint.get("question_key", current_question.get("question_key", "")))
        previous_coverage = get_main_question_coverage(
            working_state,
            main_question_key,
        ) or TopicCoverageStatus(
            main_question_key=main_question_key,
            question_key=str(blueprint.get("question_key", "")),
            required=not bool(blueprint.get("can_skip", False)),
            status="not_started",
            confidence=0.0,
            observed_signals=[],
            missing_signals=list(blueprint.get("must_observe_signals", [])),
            follow_up_count=int(working_state.get("follow_up_count", 0)),
            evidence_count=0,
            completed=False,
            last_updated=None,
        )

        observation = await self._observe_answer(
            state=working_state,
            blueprint=blueprint,
            current_question=current_question,
            previous_coverage=previous_coverage,
            main_question_key=main_question_key,
        )
        self._apply_observation(
            working_state,
            blueprint=blueprint,
            previous_coverage=previous_coverage,
            observation=observation,
            main_question_key=main_question_key,
        )
        logger.info(
            "interview plan observation updated session_id={}, main_question_key={}, main_question_status={}, confidence={}",
            working_state["session_id"],
            main_question_key,
            observation["main_question_status"],
            observation["confidence"],
        )
        return working_state

    async def _observe_answer(
        self,
        *,
        state: InterviewState,
        blueprint: QuestionBlueprint,
        current_question: dict[str, Any],
        previous_coverage: TopicCoverageStatus,
        main_question_key: str,
    ) -> AnswerObservation:
        """Prefer LLM observation and fall back to deterministic heuristics."""

        skill = state["skill"]
        variables = {
            "skill_name": skill["display_name"],
            "current_question": self._prompt_runner.wrap_untrusted_text(
                "current_question",
                str(current_question.get("question_text", "")),
            ),
            "question_intent": self._prompt_runner.wrap_untrusted_text(
                "question_intent",
                str(blueprint.get("intent", "")),
            ),
            "must_observe_signals": self._prompt_runner.wrap_untrusted_text(
                "must_observe_signals",
                "\n".join(f"- {item}" for item in blueprint.get("must_observe_signals", [])),
            ),
            "completion_criteria": self._prompt_runner.wrap_untrusted_text(
                "completion_criteria",
                "\n".join(f"- {item}" for item in blueprint.get("completion_criteria", [])),
            ),
            "existing_observed_signals": self._prompt_runner.wrap_untrusted_text(
                "existing_observed_signals",
                "\n".join(f"- {item}" for item in previous_coverage.get("observed_signals", [])),
            ),
            "existing_missing_signals": self._prompt_runner.wrap_untrusted_text(
                "existing_missing_signals",
                "\n".join(f"- {item}" for item in previous_coverage.get("missing_signals", [])),
            ),
            "user_answer": self._prompt_runner.wrap_untrusted_text(
                "user_answer",
                str(state.get("latest_answer_text", "")),
            ),
        }
        try:
            output = await self._prompt_runner.ainvoke_structured(
                template_name="plan_observer_system.st",
                schema=_PlanObservationOutput,
                variables=variables,
                temperature=0.1,
            )
            return AnswerObservation(
                question_key=str(current_question.get("question_key", "")),
                main_question_key=main_question_key,
                answer_text_summary=output.answer_text_summary.strip(),
                observed_signals=[item.strip() for item in output.observed_signals if item.strip()],
                missing_signals=[item.strip() for item in output.missing_signals if item.strip()],
                main_question_status=output.main_question_status,
                confidence=float(output.confidence),
                suggested_action=output.suggested_action,
                reasoning=output.reasoning.strip(),
                created_at=utc_now_iso(),
            )
        except Exception as exc:
            logger.warning(
                "interview answer observation fallback applied=true, stage=plan_observer, error={}",
                exc,
            )
        return self._fallback_observation(
            state=state,
            blueprint=blueprint,
            current_question=current_question,
            previous_coverage=previous_coverage,
            main_question_key=main_question_key,
        )

    def _fallback_observation(
        self,
        *,
        state: InterviewState,
        blueprint: QuestionBlueprint,
        current_question: dict[str, Any],
        previous_coverage: TopicCoverageStatus,
        main_question_key: str,
    ) -> AnswerObservation:
        """Use simple heuristics to keep plan fields complete when LLM is unavailable."""

        answer_text = str(state.get("latest_answer_text", "")).strip()
        answer_lower = answer_text.lower()
        required_signals = list(blueprint.get("must_observe_signals", []))
        observed_signals = list(previous_coverage.get("observed_signals", []))
        matched_signals: list[str] = []

        for signal in required_signals:
            tokens = [token for token in signal.lower().replace("/", " ").replace("-", " ").split() if token]
            if tokens and any(token in answer_lower for token in tokens):
                matched_signals.append(signal)

        if len(answer_text) >= 45 and required_signals:
            for fallback_signal in required_signals[:2]:
                if fallback_signal not in matched_signals:
                    matched_signals.append(fallback_signal)

        if len(answer_text) >= 80:
            for fallback_signal in required_signals:
                if fallback_signal not in matched_signals:
                    matched_signals.append(fallback_signal)

        observed_signals = _unique_list(observed_signals + matched_signals)
        missing_signals = [
            signal for signal in required_signals if signal not in observed_signals
        ]
        confidence = 0.0
        if required_signals:
            confidence = len(observed_signals) / len(required_signals)
        elif len(answer_text) >= 80:
            confidence = 0.8
        elif len(answer_text) >= 40:
            confidence = 0.55
        elif answer_text:
            confidence = 0.3

        main_question_status = "partial"
        suggested_action = "follow_up"
        if confidence >= 0.75 and not missing_signals:
            main_question_status = "covered"
            suggested_action = "next_question"
        elif not answer_text:
            main_question_status = "not_started"
        elif len(answer_text) >= 45 and confidence >= 0.5:
            suggested_action = "next_question"

        return AnswerObservation(
            question_key=str(current_question.get("question_key", "")),
            main_question_key=main_question_key,
            answer_text_summary=answer_text[:120],
            observed_signals=observed_signals,
            missing_signals=missing_signals,
            main_question_status=main_question_status,
            confidence=min(1.0, round(confidence, 2)),
            suggested_action=suggested_action,
            reasoning=(
                "Fallback observation inferred evidence from answer length and signal matching."
            ),
            created_at=utc_now_iso(),
        )

    def _apply_observation(
        self,
        state: InterviewState,
        *,
        blueprint: QuestionBlueprint,
        previous_coverage: TopicCoverageStatus,
        observation: AnswerObservation,
        main_question_key: str,
    ) -> None:
        """Apply the observation back into plan state."""

        coverage_status = dict(state.get("coverage_status", {}))
        existing = deepcopy(previous_coverage)
        existing["observed_signals"] = _unique_list(
            list(previous_coverage.get("observed_signals", []))
            + list(observation.get("observed_signals", []))
        )
        existing["missing_signals"] = list(observation.get("missing_signals", []))
        existing["status"] = str(observation.get("main_question_status", "partial"))
        existing["confidence"] = max(
            float(previous_coverage.get("confidence", 0.0) or 0.0),
            float(observation.get("confidence", 0.0) or 0.0),
        )
        existing["follow_up_count"] = int(state.get("follow_up_count", 0))
        existing["evidence_count"] = int(previous_coverage.get("evidence_count", 0) or 0) + 1
        existing["completed"] = str(observation.get("main_question_status", "partial")) == "covered"
        existing["last_updated"] = str(observation.get("created_at"))
        existing["main_question_key"] = main_question_key
        existing["question_key"] = str(blueprint.get("question_key", main_question_key))
        coverage_status[main_question_key] = existing
        state["coverage_status"] = coverage_status
        state["current_main_question_key"] = main_question_key

        observations = list(state.get("answer_observations", []))
        observations.append(observation)
        state["answer_observations"] = observations[-20:]

        progress = dict(state.get("plan_progress", {}))
        covered_main_question_keys = list(progress.get("covered_main_question_keys", []))
        if existing["completed"] and main_question_key not in covered_main_question_keys:
            covered_main_question_keys.append(main_question_key)
        progress["covered_main_question_keys"] = covered_main_question_keys

        closed_main_question_keys = list(progress.get("closed_main_question_keys", []))
        if existing["completed"] and main_question_key not in closed_main_question_keys:
            closed_main_question_keys.append(main_question_key)
        progress["closed_main_question_keys"] = closed_main_question_keys

        remaining_main_question_keys = [
            question_key
            for question_key in list(state.get("remaining_main_question_keys", []))
            if question_key != main_question_key or not existing["completed"]
        ]
        if not existing["completed"] and main_question_key not in remaining_main_question_keys:
            remaining_main_question_keys.insert(0, main_question_key)
        progress["remaining_main_question_keys"] = remaining_main_question_keys
        progress["current_main_question_key"] = main_question_key
        progress["rounds_used"] = int(state.get("current_round", 0))
        progress["evidence_count"] = len(observations)
        history = list(progress.get("history", []))
        history.append(
            {
                "event": "answer_observed",
                "main_question_key": main_question_key,
                "question_key": observation.get("question_key"),
                "main_question_status": observation.get("main_question_status"),
                "confidence": observation.get("confidence"),
                "created_at": observation.get("created_at"),
            }
        )
        progress["history"] = history[-50:]
        state["plan_progress"] = progress
        state["remaining_main_question_keys"] = remaining_main_question_keys

        blueprints = list(state.get("interview_plan", {}).get("question_blueprints", []))
        required_main_question_keys = [
            str(item.get("question_key"))
            for item in blueprints
            if not bool(item.get("can_skip", False))
        ]
        optional_main_question_keys = [
            str(item.get("question_key"))
            for item in blueprints
            if bool(item.get("can_skip", False))
        ]

        termination_context = dict(state.get("termination_decision_context", {}))
        termination_context["current_main_question_key"] = main_question_key
        termination_context["all_required_main_questions_covered"] = all(
            state["coverage_status"].get(required_main_question_key, {}).get("completed")
            for required_main_question_key in required_main_question_keys
        )
        termination_context["remaining_required_main_question_keys"] = [
            required_main_question_key
            for required_main_question_key in required_main_question_keys
            if not state["coverage_status"].get(required_main_question_key, {}).get("completed")
        ]
        termination_context["remaining_optional_main_question_keys"] = [
            optional_main_question_key
            for optional_main_question_key in optional_main_question_keys
            if not state["coverage_status"].get(optional_main_question_key, {}).get("completed")
        ]
        termination_context["remaining_round_budget"] = max(
            0,
            int(state.get("max_rounds", 0)) - int(state.get("current_round", 0)),
        )
        termination_context["latest_decision"] = {
            "observer_suggested_action": observation.get("suggested_action"),
            "observer_reasoning": observation.get("reasoning"),
            "main_question_status": observation.get("main_question_status"),
        }
        state["termination_decision_context"] = termination_context
        hydrate_plan_state(state)


def _unique_list(values: list[str]) -> list[str]:
    """Keep list order while removing blanks and duplicates."""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
