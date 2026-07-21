"""Plan-aware interview replanner."""

from __future__ import annotations

import re
from typing import Any, Literal

from loguru import logger
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.config import config
from app.models.interview import InterviewWorkflowAction

from .prompts import InterviewPromptRunner
from .state import (
    InterviewState,
    choose_next_main_question,
    clone_state,
    get_current_blueprint,
    get_current_question,
    get_follow_up_count_for_round,
    get_latest_observation,
    get_main_question_coverage,
    hydrate_plan_state,
    resolve_main_question_key,
)

_GITHUB_URL_PATTERN = re.compile(
    r"(https?://)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
    re.IGNORECASE,
)


class _ReplanDecision(BaseModel):
    """Structured decision output for the next interview action."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    action: Literal["follow_up", "next_question", "complete"] = Field(
        ...,
        validation_alias=AliasChoices("next_action", "action"),
        description="Next workflow action",
    )
    reason: str = Field(..., min_length=1, description="Reason for the next action")
    next_question: str | None = Field(default=None, min_length=1, description="Optional next question preview")
    main_question_status: str = Field(default="partial", description="Current main question status")
    coverage_update: str = Field(default="", description="Coverage update summary")
    remaining_risk: str = Field(default="", description="What evidence or questions are still missing")
    next_main_question_key: str | None = Field(
        default=None,
        description="Next main question to move to when switching questions",
    )
    plan_adjustment: str | None = Field(default=None, description="Local plan adjustment note")

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> _ReplanDecision:  # type: ignore[override]
        """在严格校验前对少量已知弱契约字段做归一化。"""

        if isinstance(obj, dict):
            normalized = dict(obj)
            if normalized.get("remaining_risk") is None:
                normalized["remaining_risk"] = ""
            obj = normalized
        return super().model_validate(obj, *args, **kwargs)


class InterviewReplanner:
    """Decide whether to follow up, move to the next main question, or complete."""

    def __init__(self, prompt_runner: InterviewPromptRunner) -> None:
        self._prompt_runner = prompt_runner

    async def run(self, state: InterviewState) -> InterviewState:
        """Make a plan-aware decision for the latest answer."""

        working_state = clone_state(state)
        hydrate_plan_state(working_state, max_follow_up_questions=config.interview.max_follow_up_questions)
        current_question = get_current_question(working_state)
        if current_question is None:
            working_state["next_action"] = InterviewWorkflowAction.COMPLETE.value
            working_state["action_reason"] = "Current question is missing, so the interview must end."
            working_state["completed"] = True
            return working_state

        decision = await self._decide_next_action(working_state)
        decision = self._normalize_decision(working_state, decision)
        working_state["next_action"] = decision.action
        working_state["action_reason"] = decision.reason
        working_state["termination_decision_context"] = {
            **dict(working_state.get("termination_decision_context", {})),
            "latest_decision": {
                "action": decision.action,
                "reason": decision.reason,
                "main_question_status": decision.main_question_status,
                "coverage_update": decision.coverage_update,
                "remaining_risk": decision.remaining_risk,
                "next_main_question_key": decision.next_main_question_key,
                "plan_adjustment": decision.plan_adjustment,
            },
        }
        if decision.next_main_question_key:
            working_state["current_main_question_key"] = decision.next_main_question_key
        if decision.action == InterviewWorkflowAction.COMPLETE.value:
            working_state["completed"] = True
        logger.info(
            "interview replanner completed session_id={}, action={}, main_question_status={}, next_main_question_key={}",
            working_state["session_id"],
            decision.action,
            decision.main_question_status,
            decision.next_main_question_key,
        )
        return working_state

    def _normalize_decision(self, state: InterviewState, decision: Any) -> _ReplanDecision:
        """Normalize mocked or legacy decision payloads to the current schema."""

        if isinstance(decision, _ReplanDecision):
            return self._enforce_follow_up_limit(
                state,
                self._maybe_force_github_follow_up(state, decision),
            )

        current_question = get_current_question(state)
        main_question_key = resolve_main_question_key(state, question=current_question)
        coverage = get_main_question_coverage(state, main_question_key) or {}
        payload = {
            "action": getattr(decision, "action", InterviewWorkflowAction.NEXT_QUESTION.value),
            "reason": getattr(decision, "reason", "Decision normalized from legacy payload."),
            "next_question": getattr(decision, "next_question", None),
            "main_question_status": getattr(
                decision,
                "main_question_status",
                getattr(decision, "topic_status", str(coverage.get("status", "partial"))),
            ),
            "coverage_update": getattr(decision, "coverage_update", ""),
            "remaining_risk": getattr(decision, "remaining_risk", "") or "",
            "next_main_question_key": getattr(
                decision,
                "next_main_question_key",
                getattr(decision, "next_topic_key", None),
            ),
            "plan_adjustment": getattr(decision, "plan_adjustment", None),
        }
        normalized = _ReplanDecision.model_validate(payload)
        return self._enforce_follow_up_limit(
            state,
            self._maybe_force_github_follow_up(state, normalized),
        )

    def _enforce_follow_up_limit(self, state: InterviewState, decision: _ReplanDecision) -> _ReplanDecision:
        """Hard-stop follow-up decisions once the current main question reaches its limit."""

        if decision.action != InterviewWorkflowAction.FOLLOW_UP.value:
            return decision

        current_question = get_current_question(state)
        if current_question is None:
            return decision

        round_index = int(current_question.get("round_index", state.get("current_round", 0)) or 0)
        follow_up_count = get_follow_up_count_for_round(state, round_index)
        max_follow_ups = int(config.interview.max_follow_up_questions)
        if follow_up_count < max_follow_ups:
            return decision

        next_main_question = choose_next_main_question(state)
        main_question_key = resolve_main_question_key(state, question=current_question) or str(
            current_question.get("question_key", "")
        )
        logger.info(
            "replanner follow-up limit enforced session_id={}, main_question_key={}, follow_up_count={}, max_follow_ups={}, next_main_question_key={}",
            state.get("session_id"),
            main_question_key,
            follow_up_count,
            max_follow_ups,
            next_main_question.get("question_key") if next_main_question else None,
        )
        if next_main_question is None:
            return _ReplanDecision(
                action=InterviewWorkflowAction.COMPLETE.value,
                reason=f"当前主问题已达到追问上限（{max_follow_ups} 次），且没有后续主问题可继续，面试结束。",
                next_question=None,
                main_question_status=decision.main_question_status,
                coverage_update=decision.coverage_update,
                remaining_risk=(
                    decision.remaining_risk
                    or f"Main question {main_question_key} stopped because the follow-up limit was reached."
                ),
                next_main_question_key=None,
                plan_adjustment="Stop follow-up loop after reaching the configured limit.",
            )

        return _ReplanDecision(
            action=InterviewWorkflowAction.NEXT_QUESTION.value,
            reason=f"当前主问题已达到追问上限（{max_follow_ups} 次），转入下一主问题。",
            next_question=None,
            main_question_status=decision.main_question_status,
            coverage_update=decision.coverage_update,
            remaining_risk=(
                decision.remaining_risk
                or f"Main question {main_question_key} stopped because the follow-up limit was reached."
            ),
            next_main_question_key=str(next_main_question["question_key"]),
            plan_adjustment="Switch to the next main question after reaching the follow-up limit.",
        )

    async def _decide_next_action(self, state: InterviewState) -> _ReplanDecision:
        """Prefer LLM structured decisions and fall back to plan-aware rules."""

        current_question = get_current_question(state)
        blueprint = get_current_blueprint(state)
        if current_question is None or blueprint is None:
            return _ReplanDecision(
                action=InterviewWorkflowAction.COMPLETE.value,
                reason="The current planned main question is unavailable, so the interview cannot continue.",
                main_question_status="missing",
                coverage_update="No current question or blueprint was available.",
                remaining_risk="The workflow lost the active main-question context.",
            )

        main_question_key = resolve_main_question_key(state, question=current_question) or str(
            blueprint.get("question_key", current_question.get("question_key", ""))
        )
        coverage = get_main_question_coverage(state, main_question_key) or {}
        latest_observation = get_latest_observation(
            state,
            question_key=current_question["question_key"],
            main_question_key=main_question_key,
        ) or {}
        next_main_question = choose_next_main_question(state)
        remaining_required_main_question_keys = list(
            state.get("termination_decision_context", {}).get("remaining_required_main_question_keys", [])
        )
        answered_question_count = len(
            [
                question
                for question in state.get("questions", [])
                if question.get("status") == "answered"
            ]
        )
        question_summary = "\n".join(
            [
                f"- [{question.get('question_key')}] {question.get('question_text')}"
                for question in state.get("questions", [])
                if question.get("status") == "answered"
            ]
        ) or "- no answered questions yet"
        variables = {
            "skill_name": state["skill"]["display_name"],
            "current_round": state.get("current_round", 0),
            "max_rounds": state.get("max_rounds", 1),
            "follow_up_count": state.get("follow_up_count", 0),
            "max_follow_up_questions": config.interview.max_follow_up_questions,
            "current_question": self._prompt_runner.wrap_untrusted_text(
                "current_question",
                current_question["question_text"],
            ),
            "current_blueprint": self._prompt_runner.wrap_untrusted_text(
                "current_blueprint",
                str(blueprint),
            ),
            "main_question_coverage": self._prompt_runner.wrap_untrusted_text(
                "main_question_coverage",
                str(coverage),
            ),
            "remaining_main_questions": self._prompt_runner.wrap_untrusted_text(
                "remaining_main_questions",
                "\n".join(f"- {item}" for item in state.get("remaining_main_question_keys", [])),
            ),
            "latest_observation": self._prompt_runner.wrap_untrusted_text(
                "latest_observation",
                str(latest_observation),
            ),
            "user_answer": self._prompt_runner.wrap_untrusted_text(
                "user_answer",
                state.get("latest_answer_text", ""),
            ),
            "answered_question_count": answered_question_count,
            "question_summary": self._prompt_runner.wrap_untrusted_text(
                "question_summary",
                question_summary,
            ),
            "remaining_required_main_questions": self._prompt_runner.wrap_untrusted_text(
                "remaining_required_main_questions",
                "\n".join(f"- {item}" for item in remaining_required_main_question_keys),
            ),
            "next_main_question_preview": self._prompt_runner.wrap_untrusted_text(
                "next_main_question_preview",
                next_main_question["question_text"] if next_main_question else "",
            ),
            "github_follow_up_recommended": self._prompt_runner.wrap_untrusted_text(
                "github_follow_up_recommended",
                "yes" if self._should_force_github_follow_up(state) else "no",
            ),
        }

        try:
            output = await self._prompt_runner.ainvoke_structured(
                template_name="replanner_system.st",
                schema=_ReplanDecision,
                variables=variables,
                temperature=0.1,
            )
            return output
        except Exception as exc:
            logger.warning(
                "replanner fallback applied=true, stage=replanner, error={}",
                exc,
            )
            mocked_decision = getattr(self, "_decide_next_action_mock_compat", None)
            if mocked_decision:
                return mocked_decision(state)

        return self._fallback_decision(state)

    def _fallback_decision(self, state: InterviewState) -> _ReplanDecision:
        """Use plan-aware rules when LLM decision is unavailable."""

        current_question = get_current_question(state)
        blueprint = get_current_blueprint(state)
        if current_question is None or blueprint is None:
            return _ReplanDecision(
                action=InterviewWorkflowAction.COMPLETE.value,
                reason="The current question context is missing, so the interview ends.",
                main_question_status="missing",
                coverage_update="No current main-question context.",
                remaining_risk="Workflow state is incomplete.",
            )

        main_question_key = resolve_main_question_key(state, question=current_question) or str(
            blueprint.get("question_key", current_question.get("question_key", ""))
        )
        coverage = get_main_question_coverage(state, main_question_key) or {}
        latest_observation = get_latest_observation(
            state,
            question_key=current_question["question_key"],
            main_question_key=main_question_key,
        ) or {}
        latest_answer_text = str(state.get("latest_answer_text", "") or "").strip()
        remaining_required_main_question_keys = list(
            state.get("termination_decision_context", {}).get("remaining_required_main_question_keys", [])
        )
        remaining_round_budget = int(
            state.get("termination_decision_context", {}).get("remaining_round_budget", 0)
        )
        next_main_question = choose_next_main_question(state)
        main_question_completed = bool(coverage.get("completed", False))
        missing_signals = list(coverage.get("missing_signals", []))
        confidence = float(coverage.get("confidence", 0.0) or 0.0)
        follow_up_count = int(state.get("follow_up_count", 0))
        max_follow_ups = int(config.interview.max_follow_up_questions)
        latest_decision_context = dict(
            state.get("termination_decision_context", {}).get("latest_decision", {})
        )
        observer_suggested_action = str(
            latest_observation.get(
                "suggested_action",
                latest_decision_context.get("observer_suggested_action", ""),
            )
            or ""
        )
        all_required_covered = bool(
            state.get("termination_decision_context", {}).get("all_required_main_questions_covered", False)
        )
        github_follow_up_recommended = self._should_force_github_follow_up(state)

        logger.info(
            "replanner fallback evaluating session_id={}, main_question_key={}, missing_signals={}, confidence={}, follow_up_count={}, github_follow_up_recommended={}",
            state.get("session_id"),
            main_question_key,
            missing_signals[:4],
            round(confidence, 3),
            follow_up_count,
            github_follow_up_recommended,
        )

        if all_required_covered and main_question_completed and (
            next_main_question is None or remaining_round_budget <= 0
        ):
            return _ReplanDecision(
                action=InterviewWorkflowAction.COMPLETE.value,
                reason="All required main questions are covered and the current main question is already closed.",
                main_question_status="covered",
                coverage_update=f"Main question {main_question_key} is complete.",
                remaining_risk="No required main-question risk remains.",
                next_main_question_key=None,
                plan_adjustment="Early finish triggered by required main-question coverage.",
            )

        if github_follow_up_recommended:
            return _ReplanDecision(
                action=InterviewWorkflowAction.FOLLOW_UP.value,
                reason=(
                    "The current project-style main question has a GitHub repository link and no follow-up yet, "
                    "so one repository-anchored follow-up should be attempted before moving on."
                ),
                main_question_status=str(coverage.get("status", "partial")),
                coverage_update=(
                    f"Main question {main_question_key} gets one GitHub-anchored follow-up before closure."
                ),
                remaining_risk="Repository-backed implementation evidence has not been probed yet.",
                next_main_question_key=main_question_key,
                plan_adjustment="Reserve one follow-up for GitHub evidence anchoring.",
            )

        if main_question_completed:
            if next_main_question is None:
                return _ReplanDecision(
                    action=InterviewWorkflowAction.COMPLETE.value,
                    reason="The current main question is complete and there are no remaining main questions.",
                    main_question_status="covered",
                    coverage_update=f"Main question {main_question_key} is complete.",
                    remaining_risk="No remaining main questions.",
                    next_main_question_key=None,
                    plan_adjustment=None,
                )
            next_main_question_key = str(next_main_question["question_key"])
            return _ReplanDecision(
                action=InterviewWorkflowAction.NEXT_QUESTION.value,
                reason="The current main question already has enough evidence, so the interview should move on.",
                main_question_status="covered",
                coverage_update=f"Main question {main_question_key} is complete with confidence {confidence:.2f}.",
                remaining_risk=(
                    "Still need to cover required main questions: "
                    + ", ".join(remaining_required_main_question_keys)
                    if remaining_required_main_question_keys
                    else "Move on to the next planned main question."
                ),
                next_main_question_key=next_main_question_key,
                plan_adjustment=None,
            )

        should_follow_up = (
            bool(missing_signals)
            and observer_suggested_action != InterviewWorkflowAction.NEXT_QUESTION.value
            and follow_up_count < max_follow_ups
            and (
                remaining_round_budget > 0
                or not remaining_required_main_question_keys
                or main_question_key in remaining_required_main_question_keys
            )
        )
        if should_follow_up:
            return _ReplanDecision(
                action=InterviewWorkflowAction.FOLLOW_UP.value,
                reason="The current main question still lacks required evidence, so one targeted follow-up is worthwhile.",
                main_question_status=str(
                    latest_observation.get("main_question_status", coverage.get("status", "partial"))
                ),
                coverage_update=f"Missing signals: {', '.join(missing_signals)}",
                remaining_risk=f"Current main question {main_question_key} is not complete yet.",
                next_main_question_key=main_question_key,
                plan_adjustment="Spend one more follow-up on the current main question.",
            )

        if next_main_question is None or int(state.get("current_round", 0)) >= int(state.get("max_rounds", 1)):
            return _ReplanDecision(
                action=InterviewWorkflowAction.COMPLETE.value,
                reason="No safe remaining plan budget exists, so the interview should end.",
                main_question_status=str(coverage.get("status", "partial")),
                coverage_update=f"Main question {main_question_key} stopped at confidence {confidence:.2f}.",
                remaining_risk=(
                    "Uncovered required main questions remain: "
                    + ", ".join(remaining_required_main_question_keys)
                    if remaining_required_main_question_keys
                    else "No further question budget remains."
                ),
                next_main_question_key=None,
                plan_adjustment="Ended because of round budget.",
            )

        next_main_question_key = str(next_main_question["question_key"])
        return _ReplanDecision(
            action=InterviewWorkflowAction.NEXT_QUESTION.value,
            reason=(
                "The current answer provided enough baseline evidence, and preserving remaining required main-question coverage is more important than another follow-up."
            ),
            main_question_status=str(coverage.get("status", "partial")),
            coverage_update=f"Current main question confidence is {confidence:.2f}.",
            remaining_risk=(
                "Still need to preserve rounds for required main questions: "
                + ", ".join(remaining_required_main_question_keys)
                if remaining_required_main_question_keys
                else "Continue with the next planned main question."
            ),
            next_main_question_key=next_main_question_key,
            plan_adjustment="Switch to the next main question to preserve the global plan.",
        )

    def _maybe_force_github_follow_up(
        self,
        state: InterviewState,
        decision: _ReplanDecision,
    ) -> _ReplanDecision:
        """Override the next action when GitHub-backed project follow-up should happen first."""

        if decision.action == InterviewWorkflowAction.FOLLOW_UP.value:
            return decision
        if not self._should_force_github_follow_up(state):
            return decision

        current_question = get_current_question(state)
        blueprint = get_current_blueprint(state)
        main_question_key = resolve_main_question_key(state, question=current_question) or str(
            blueprint.get("question_key", current_question.get("question_key", ""))
        )
        logger.info(
            "replanner github follow-up override applied session_id={}, original_action={}, main_question_key={}",
            state.get("session_id"),
            decision.action,
            main_question_key,
        )
        return _ReplanDecision(
            action=InterviewWorkflowAction.FOLLOW_UP.value,
            reason=(
                "The current project-oriented main question has a GitHub repository link, and no repository-backed "
                "follow-up has been attempted yet."
            ),
            next_question=decision.next_question,
            main_question_status=decision.main_question_status,
            coverage_update=(
                decision.coverage_update
                or f"Keep the current main question open for one GitHub-anchored follow-up."
            ),
            remaining_risk=(
                decision.remaining_risk or "Repository-backed implementation evidence has not been probed yet."
            ),
            next_main_question_key=main_question_key,
            plan_adjustment="Insert one GitHub-anchored follow-up before moving to the next main question.",
        )

    def _should_force_github_follow_up(self, state: InterviewState) -> bool:
        """Return whether the current main question should receive one GitHub-backed follow-up first."""

        current_question = get_current_question(state)
        blueprint = get_current_blueprint(state)
        if current_question is None or blueprint is None:
            return False
        if bool(current_question.get("is_follow_up")):
            return False
        if int(state.get("follow_up_count", 0) or 0) > 0:
            return False
        if not self._has_github_repo_link(str(state.get("resume_markdown", "") or "")):
            return False

        remaining_round_budget = int(
            state.get("termination_decision_context", {}).get("remaining_round_budget", 0)
        )
        max_rounds = int(state.get("max_rounds", 0) or 0)
        main_question_key = resolve_main_question_key(state, question=current_question) or str(
            blueprint.get("question_key", current_question.get("question_key", ""))
        )
        remaining_required_main_question_keys = list(
            state.get("termination_decision_context", {}).get("remaining_required_main_question_keys", [])
        )
        has_budget = (
            max_rounds <= 0
            or remaining_round_budget > 0
            or int(state.get("current_round", 0) or 0) <= 0
            or not remaining_required_main_question_keys
            or main_question_key in remaining_required_main_question_keys
        )
        if not has_budget:
            return False
        return self._is_project_oriented_question(current_question=current_question, blueprint=blueprint)

    def _has_github_repo_link(self, resume_markdown: str) -> bool:
        """Return whether the resume contains at least one GitHub repository link."""

        return bool(_GITHUB_URL_PATTERN.search(resume_markdown or ""))

    def _is_project_oriented_question(
        self,
        *,
        current_question: dict[str, Any],
        blueprint: dict[str, Any],
    ) -> bool:
        """Heuristically decide whether the current main question is project/repository oriented."""

        category_key = str(current_question.get("category_key", "") or "").upper()
        combined_text = "\n".join(
            [
                str(current_question.get("question_text", "") or ""),
                str(blueprint.get("intent", "") or ""),
                "\n".join(str(item) for item in blueprint.get("follow_up_focus", []) or []),
            ]
        ).lower()
        if "PROJECT" in category_key:
            return True
        project_terms = (
            "project",
            "repo",
            "repository",
            "github",
            "项目",
            "工程",
            "仓库",
            "实现",
            "维护",
            "架构",
            "模块",
            "设计",
            "readme",
            "structure",
            "architecture",
            "implementation",
            "maintenance",
        )
        return any(term in combined_text for term in project_terms)
