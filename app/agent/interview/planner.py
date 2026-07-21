"""Interview planner that generates a stable global interview plan."""

from __future__ import annotations

import re
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.interview import (
    InterviewQuestionSnapshot,
    InterviewQuestionSource,
    InterviewQuestionStatus,
)

from .prompts import InterviewPromptRunner
from .state import (
    InterviewState,
    TopicCoverageStatus,
    clone_state,
    hydrate_plan_state,
)

_PHRASE_SPLIT_PATTERN = re.compile(r"[,，、;\n]+")


def _normalize_phrase_list(value: Any) -> list[str]:
    """Normalize comma-separated text or loose arrays into stable phrase lists."""

    if value is None:
        return []

    items: list[str] = []
    raw_values = value if isinstance(value, (list, tuple, set)) else [value]
    for raw_value in raw_values:
        if raw_value is None:
            continue
        if isinstance(raw_value, str):
            candidates = _PHRASE_SPLIT_PATTERN.split(raw_value)
        else:
            candidates = [str(raw_value)]
        for candidate in candidates:
            normalized = candidate.strip()
            if normalized:
                items.append(normalized)

    deduplicated: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduplicated.append(item)
    return deduplicated


class _PlannedQuestionItem(BaseModel):
    """One planned main question with blueprint metadata."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    category_key: str = Field(..., min_length=1, description="Skill category key")
    question_text: str = Field(..., min_length=1, description="Main question text")
    intent: str = Field(..., min_length=1, description="Why this question exists")
    must_observe_signals: list[str] = Field(default_factory=list, description="Required evidence signals")
    follow_up_focus: list[str] = Field(default_factory=list, description="Preferred follow-up angles")
    completion_criteria: list[str] = Field(default_factory=list, description="Main question close conditions")
    priority: int = Field(default=1, ge=1, description="Question priority")
    can_skip: bool = Field(default=False, description="Whether this main question is optional")

    @model_validator(mode="before")
    @classmethod
    def normalize_sequence_fields(cls, value: Any) -> Any:
        """Allow the model to recover when array fields are returned as comma-separated strings."""

        if not isinstance(value, dict):
            return value

        normalized = dict(value)
        for field_name in ("must_observe_signals", "follow_up_focus", "completion_criteria"):
            normalized[field_name] = _normalize_phrase_list(normalized.get(field_name))
        return normalized


class _PlannerOutput(BaseModel):
    """Structured planner output with global plan and main question skeleton."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    plan_summary: str = Field(default="", description="Global interview strategy summary")
    questions: list[_PlannedQuestionItem] = Field(default_factory=list, description="Main question plan")


class InterviewPlanner:
    """Generate a stable interview plan and initial main questions."""

    def __init__(self, prompt_runner: InterviewPromptRunner) -> None:
        self._prompt_runner = prompt_runner

    async def run(self, state: InterviewState) -> InterviewState:
        """Build the initial plan and write it back into workflow state."""

        working_state = clone_state(state)
        skill = working_state["skill"]
        max_rounds = int(working_state["max_rounds"])

        planner_output = await self._build_question_plan(working_state)
        question_snapshots: list[dict[str, Any]] = []
        blueprints: list[dict[str, Any]] = []
        coverage_status: dict[str, TopicCoverageStatus] = {}

        for index, planned_question in enumerate(planner_output.questions[:max_rounds], start=1):
            question_key = f"q-{index}"
            category_key = planned_question.category_key.strip() or "GENERAL"
            question_text = planned_question.question_text.strip()
            question_snapshots.append(
                InterviewQuestionSnapshot(
                    question_key=question_key,
                    round_index=index,
                    category_key=category_key,
                    question_text=question_text,
                    parent_question_key=None,
                    source=InterviewQuestionSource.PLANNED.value,
                    status=InterviewQuestionStatus.PLANNED.value,
                    is_follow_up=False,
                ).model_dump(mode="json")
            )
            signals = [signal.strip() for signal in planned_question.must_observe_signals if signal.strip()]
            follow_up_focus = [item.strip() for item in planned_question.follow_up_focus if item.strip()]
            completion_criteria = [
                item.strip() for item in planned_question.completion_criteria if item.strip()
            ]
            blueprints.append(
                {
                    "question_key": question_key,
                    "category_key": category_key,
                    "question_text": question_text,
                    "round_index": index,
                    "intent": planned_question.intent.strip(),
                    "must_observe_signals": signals,
                    "follow_up_focus": follow_up_focus or signals,
                    "completion_criteria": completion_criteria or signals,
                    "priority": planned_question.priority,
                    "can_skip": planned_question.can_skip,
                }
            )
            coverage_status[question_key] = TopicCoverageStatus(
                main_question_key=question_key,
                question_key=question_key,
                required=not planned_question.can_skip,
                status="not_started",
                confidence=0.0,
                observed_signals=[],
                missing_signals=signals,
                follow_up_count=0,
                evidence_count=0,
                completed=False,
                last_updated=None,
            )

        main_question_keys = [item["question_key"] for item in blueprints]
        required_main_question_keys = [
            item["question_key"]
            for item in blueprints
            if not bool(item.get("can_skip", False))
        ]
        optional_main_question_keys = [
            item["question_key"]
            for item in blueprints
            if bool(item.get("can_skip", False))
        ]

        working_state["questions"] = question_snapshots
        working_state["interview_plan"] = {
            "plan_version": "v3",
            "plan_summary": planner_output.plan_summary.strip(),
            "question_blueprints": blueprints,
            "termination_policy": {
                "max_rounds": max_rounds,
                "max_follow_up_per_main_question": 2,
                "coverage_threshold": 0.7,
                "early_finish_rule": "all required main questions covered and current main question closed",
            },
            "interview_strategy": {
                "style": "structured",
                "difficulty": "adaptive",
                "max_rounds": max_rounds,
                "max_follow_up_per_main_question": 2,
                "plan_stability": "stable_main_plan",
            },
        }
        working_state["coverage_status"] = coverage_status
        working_state["remaining_main_question_keys"] = list(main_question_keys)
        working_state["current_main_question_key"] = main_question_keys[0] if main_question_keys else None
        working_state["plan_progress"] = {
            "covered_main_question_keys": [],
            "closed_main_question_keys": [],
            "current_main_question_key": working_state["current_main_question_key"],
            "remaining_main_question_keys": list(working_state["remaining_main_question_keys"]),
            "rounds_used": 0,
            "evidence_count": 0,
            "history": [
                {
                    "event": "plan_created",
                    "question_count": len(question_snapshots),
                    "created_for_skill": skill["skill_id"],
                }
            ],
        }
        working_state["termination_decision_context"] = {
            "current_main_question_key": working_state["current_main_question_key"],
            "all_required_main_questions_covered": False if required_main_question_keys else True,
            "remaining_required_main_question_keys": list(required_main_question_keys),
            "remaining_optional_main_question_keys": list(optional_main_question_keys),
            "remaining_round_budget": max_rounds,
            "latest_decision": {},
        }
        hydrate_plan_state(working_state)
        working_state["action_reason"] = (
            f"Generated a global interview plan for {skill['display_name']} with "
            f"{len(question_snapshots)} main questions."
        )
        logger.info(
            "interview planner generated session_id={}, question_count={}",
            working_state["session_id"],
            len(question_snapshots),
        )
        return working_state

    async def _build_question_plan(self, state: InterviewState) -> _PlannerOutput:
        """Prefer LLM structured planning and fall back to deterministic planning."""

        skill = state["skill"]
        variables = {
            "skill_name": skill["display_name"],
            "skill_description": skill["description"],
            "language": state["language"],
            "max_rounds": state["max_rounds"],
            "planning_snapshot_markdown": self._prompt_runner.wrap_untrusted_text(
                "planning_snapshot_markdown",
                str(skill.get("planning_snapshot_markdown", "")),
            ),
            "reference_markdown": self._prompt_runner.wrap_untrusted_text(
                "reference_markdown",
                skill["reference_markdown"],
            ),
            "category_section": self._prompt_runner.render_categories(skill["categories"]),
        }

        try:
            output = await self._prompt_runner.ainvoke_structured(
                template_name="planner_system.st",
                schema=_PlannerOutput,
                variables=variables,
                temperature=0.2,
            )
            normalized_questions = [
                _PlannedQuestionItem(
                    category_key=item.category_key.strip() or "GENERAL",
                    question_text=item.question_text.strip(),
                    intent=item.intent.strip() or "Assess the candidate on this main question.",
                    must_observe_signals=[signal.strip() for signal in item.must_observe_signals if signal.strip()],
                    follow_up_focus=[signal.strip() for signal in item.follow_up_focus if signal.strip()],
                    completion_criteria=[
                        signal.strip() for signal in item.completion_criteria if signal.strip()
                    ],
                    priority=max(1, int(item.priority)),
                    can_skip=bool(item.can_skip),
                )
                for item in output.questions
                if item.question_text.strip()
            ]
            if normalized_questions:
                return _PlannerOutput(
                    plan_summary=output.plan_summary.strip(),
                    questions=normalized_questions,
                )
        except Exception as exc:
            logger.warning(
                "planner fallback applied=true, stage=planner, error={}",
                exc,
            )

        return self._build_fallback_plan(
            display_name=skill["display_name"],
            categories=skill["categories"],
            max_rounds=state["max_rounds"],
        )

    def _build_fallback_plan(
        self,
        *,
        display_name: str,
        categories: list[dict[str, Any]],
        max_rounds: int,
    ) -> _PlannerOutput:
        """Build a deterministic stable plan when the LLM is unavailable."""

        category_pool = categories or [
            {"key": "GENERAL", "label": "General", "priority": "NORMAL"}
        ]
        ordered_categories = sorted(
            category_pool,
            key=lambda item: (
                self._priority_rank(str(item.get("priority", "NORMAL"))),
                category_pool.index(item),
            ),
        )

        questions: list[_PlannedQuestionItem] = []
        for round_index in range(1, max_rounds + 1):
            category = ordered_categories[(round_index - 1) % len(ordered_categories)]
            label = str(category.get("label", "General"))
            key = str(category.get("key", "GENERAL")) or "GENERAL"
            questions.append(
                _PlannedQuestionItem(
                    category_key=key,
                    question_text=self._build_fallback_question_text(
                        display_name=display_name,
                        category_key=key,
                        category_label=label,
                        round_index=round_index,
                    ),
                    intent=f"Assess the candidate's depth on {label}.",
                    must_observe_signals=[
                        "implementation details",
                        "tradeoffs",
                        "results",
                    ],
                    follow_up_focus=[
                        "implementation details",
                        "tradeoffs",
                        "pitfalls",
                    ],
                    completion_criteria=[
                        "candidate explains what they implemented",
                        "candidate explains key tradeoffs",
                        "candidate explains outcomes or lessons",
                    ],
                    priority=round_index,
                    can_skip=False,
                )
            )

        return _PlannerOutput(
            plan_summary=(
                f"Cover {display_name} through a stable sequence of main questions, "
                "and use follow-up only to collect missing evidence."
            ),
            questions=questions,
        )

    def _build_fallback_question_text(
        self,
        *,
        display_name: str,
        category_key: str,
        category_label: str,
        round_index: int,
    ) -> str:
        """Build natural fallback main questions by category."""

        if "PROJECT" in category_key:
            return (
                f"Question {round_index}: share one project that best shows your {display_name} ability, "
                "including the background, your role, the technical design, and the final outcome."
            )
        if "SYSTEM_DESIGN" in category_key:
            return (
                f"Question {round_index}: design a {category_label} scenario you know well, "
                "and explain the core architecture, capacity estimation, and key tradeoffs."
            )
        if "DEPLOY" in category_key:
            return (
                f"Question {round_index}: explain an end-to-end workflow from development to deployment "
                f"for a {display_name} service, including rollback and incident handling."
            )
        return (
            f"Question {round_index}: around {category_label}, explain your understanding, "
            f"practical approach, and lessons learned in {display_name} work."
        )

    @staticmethod
    def _priority_rank(priority: str) -> int:
        """Map skill priorities to a stable ordering weight."""

        priority_mapping = {
            "ALWAYS_ONE": 0,
            "CORE": 1,
            "NORMAL": 2,
        }
        return priority_mapping.get(priority.upper(), 3)
