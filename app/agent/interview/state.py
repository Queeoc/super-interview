"""Shared interview workflow state and helpers."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, TypedDict

from app.models.interview import InterviewQuestionSnapshot, InterviewQuestionStatus


def utc_now_iso() -> str:
    """Return an ISO formatted UTC timestamp."""

    return datetime.now(timezone.utc).isoformat()


class InterviewSkillContext(TypedDict):
    """Skill and resume context used by the interview workflow."""

    skill_id: str
    display_name: str
    description: str
    content_markdown: str
    reference_markdown: str
    planning_snapshot_markdown: str
    planning_snapshot_sources: list[dict[str, Any]]
    categories: list[dict[str, Any]]
    reference_files: list[str]
    resume_markdown: str
    resume_metadata: dict[str, Any]


class QuestionBlueprint(TypedDict, total=False):
    """Plan metadata for one main question."""

    question_key: str
    category_key: str
    question_text: str
    round_index: int
    intent: str
    must_observe_signals: list[str]
    follow_up_focus: list[str]
    completion_criteria: list[str]
    priority: int
    can_skip: bool


class TopicCoverageStatus(TypedDict, total=False):
    """Rolling coverage status keyed by main_question_key."""

    main_question_key: str
    question_key: str | None
    required: bool
    status: str
    confidence: float
    observed_signals: list[str]
    missing_signals: list[str]
    follow_up_count: int
    evidence_count: int
    completed: bool
    last_updated: str | None


class AnswerObservation(TypedDict, total=False):
    """Structured evidence extracted from one answer."""

    question_key: str
    main_question_key: str
    answer_text_summary: str
    search_keywords: list[str]
    observed_signals: list[str]
    missing_signals: list[str]
    main_question_status: str
    confidence: float
    suggested_action: str
    reasoning: str
    created_at: str


class InterviewPlan(TypedDict, total=False):
    """Global interview plan."""

    plan_version: str
    plan_summary: str
    question_blueprints: list[QuestionBlueprint]
    termination_policy: dict[str, Any]
    interview_strategy: dict[str, Any]


class PlanProgress(TypedDict, total=False):
    """Rolling progress for the global interview plan."""

    covered_main_question_keys: list[str]
    closed_main_question_keys: list[str]
    current_main_question_key: str | None
    remaining_main_question_keys: list[str]
    rounds_used: int
    evidence_count: int
    history: list[dict[str, Any]]


class TerminationDecisionContext(TypedDict, total=False):
    """Derived context used by replanning and completion decisions."""

    current_main_question_key: str | None
    all_required_main_questions_covered: bool
    remaining_required_main_question_keys: list[str]
    remaining_optional_main_question_keys: list[str]
    remaining_round_budget: int
    latest_decision: dict[str, Any]


class LatestToolContext(TypedDict, total=False):
    """最近一次面试工具调用上下文。"""

    tool_name: str | None
    tool_source: str | None
    decision_reason: str | None
    arguments: dict[str, Any]
    success: bool
    result: dict[str, Any]
    error_message: str | None


class InterviewState(TypedDict, total=False):
    """Interview workflow runtime state."""

    session_id: str
    visitor_id: str | None
    resume_id: str | None
    resume_markdown: str
    resume_metadata: dict[str, Any]
    title: str | None
    language: str
    max_rounds: int
    skill: InterviewSkillContext
    questions: list[dict[str, Any]]
    interview_plan: InterviewPlan
    plan_progress: PlanProgress
    current_main_question_key: str | None
    remaining_main_question_keys: list[str]
    coverage_status: dict[str, TopicCoverageStatus]
    answer_observations: list[AnswerObservation]
    termination_decision_context: TerminationDecisionContext
    latest_tool_context: LatestToolContext
    current_question_key: str | None
    current_round: int
    follow_up_count: int
    latest_answer_text: str
    latest_answer_metadata: dict[str, Any]
    next_action: str
    action_reason: str | None
    feedback: dict[str, Any]
    report_summary: dict[str, Any]
    completed: bool
    completion_message: str | None
    assistant_message: str | None
    last_draft_answer: dict[str, Any]
    answer_count: int


def build_initial_state(
    *,
    session_id: str,
    skill: InterviewSkillContext,
    language: str,
    max_rounds: int,
    title: str | None,
    visitor_id: str | None,
    resume_id: str | None,
    resume_markdown: str = "",
    resume_metadata: dict[str, Any] | None = None,
) -> InterviewState:
    """Create the initial workflow state."""

    state: InterviewState = {
        "session_id": session_id,
        "visitor_id": visitor_id,
        "resume_id": resume_id,
        "resume_markdown": resume_markdown,
        "resume_metadata": deepcopy(resume_metadata or {}),
        "title": title,
        "language": language,
        "max_rounds": max_rounds,
        "skill": skill,
        "questions": [],
        "interview_plan": {
            "plan_version": "v3",
            "plan_summary": "",
            "question_blueprints": [],
            "termination_policy": {},
            "interview_strategy": {},
        },
        "plan_progress": {
            "covered_main_question_keys": [],
            "closed_main_question_keys": [],
            "current_main_question_key": None,
            "remaining_main_question_keys": [],
            "rounds_used": 0,
            "evidence_count": 0,
            "history": [],
        },
        "current_main_question_key": None,
        "remaining_main_question_keys": [],
        "coverage_status": {},
        "answer_observations": [],
        "termination_decision_context": {},
        "latest_tool_context": {},
        "current_question_key": None,
        "current_round": 0,
        "follow_up_count": 0,
        "latest_answer_text": "",
        "latest_answer_metadata": {},
        "next_action": "initial_ask",
        "action_reason": None,
        "feedback": {},
        "report_summary": {},
        "completed": False,
        "completion_message": None,
        "assistant_message": None,
        "last_draft_answer": {},
        "answer_count": 0,
    }
    hydrate_plan_state(state)
    return state


def clone_state(state: InterviewState) -> InterviewState:
    """Deep copy workflow state to avoid accidental mutation."""

    return deepcopy(state)


def serialize_state(state: InterviewState) -> dict[str, Any]:
    """Convert to a stable JSON serializable dictionary."""

    sanitized_state = deepcopy(state)
    hydrate_plan_state(sanitized_state)
    return deepcopy(sanitized_state)


def normalize_question_snapshot(question: dict[str, Any] | InterviewQuestionSnapshot) -> dict[str, Any]:
    """Normalize any question payload to the shared snapshot format."""

    if isinstance(question, InterviewQuestionSnapshot):
        return question.model_dump(mode="json")
    return InterviewQuestionSnapshot.model_validate(question).model_dump(mode="json")


def list_questions(state: InterviewState) -> list[dict[str, Any]]:
    """Return all normalized question snapshots in the state."""

    return [normalize_question_snapshot(question) for question in state.get("questions", [])]
               

def get_current_question(state: InterviewState) -> dict[str, Any] | None:
    """Read the current question by current_question_key."""

    current_question_key = state.get("current_question_key")
    if not current_question_key:
        return None

    for question in list_questions(state):
        if question["question_key"] == current_question_key:
            return question
    return None


def upsert_question(state: InterviewState, question: dict[str, Any] | InterviewQuestionSnapshot) -> None:
    """Insert or update one question snapshot."""

    normalized_question = normalize_question_snapshot(question)
    questions = list_questions(state)
    for index, existing_question in enumerate(questions):
        if existing_question["question_key"] == normalized_question["question_key"]:
            questions[index] = normalized_question
            state["questions"] = questions
            return

    questions.append(normalized_question)
    state["questions"] = questions


def mark_question_asked(state: InterviewState, question_key: str) -> dict[str, Any] | None:
    """Mark one question as asked."""

    questions = list_questions(state)
    for question in questions:
        if question["question_key"] != question_key:
            continue
        question["status"] = InterviewQuestionStatus.ASKED.value
        question["asked_at"] = question.get("asked_at") or utc_now_iso()
        state["questions"] = questions
        return question
    return None


def mark_question_answered(state: InterviewState, question_key: str) -> dict[str, Any] | None:
    """Mark one question as answered."""

    questions = list_questions(state)
    for question in questions:
        if question["question_key"] != question_key:
            continue
        question["status"] = InterviewQuestionStatus.ANSWERED.value
        question["answered_at"] = utc_now_iso()
        state["questions"] = questions
        return question
    return None


def get_main_questions(state: InterviewState) -> list[dict[str, Any]]:
    """Return all main questions sorted by round index."""

    questions = [
        question
        for question in list_questions(state)
        if not bool(question.get("is_follow_up"))
    ]
    return sorted(questions, key=lambda item: (int(item["round_index"]), item["question_key"]))


def get_follow_up_count_for_round(state: InterviewState, round_index: int) -> int:
    """Count follow-up questions generated for one main round."""

    return sum(
        1
        for question in list_questions(state)
        if bool(question.get("is_follow_up")) and int(question["round_index"]) == round_index
    )


def find_next_main_question(state: InterviewState) -> dict[str, Any] | None:
    """Find the next main question by round index only."""

    current_round = int(state.get("current_round", 0))
    for question in get_main_questions(state):
        if int(question["round_index"]) <= current_round:
            continue
        return question
    return None


def get_question_blueprints(state: InterviewState) -> list[QuestionBlueprint]:
    """Return normalized question blueprints from the current plan."""

    plan = deepcopy(state.get("interview_plan", {}))
    blueprints = list(plan.get("question_blueprints", []))
    if blueprints:
        return [_normalize_blueprint(item) for item in blueprints if item.get("question_key")]

    derived_blueprints: list[QuestionBlueprint] = []
    for question in get_main_questions(state):
        category_key = str(question.get("category_key", "GENERAL") or "GENERAL")
        derived_blueprints.append(
            QuestionBlueprint(
                question_key=str(question["question_key"]),
                category_key=category_key,
                question_text=str(question["question_text"]),
                round_index=int(question["round_index"]),
                intent=f"Assess the candidate's ability on main question {question['question_key']}.",
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
                    "candidate explains what they did",
                    "candidate explains why they did it",
                    "candidate describes outcomes or lessons",
                ],
                priority=int(question["round_index"]),
                can_skip=False,
            )
        )
    return derived_blueprints


def get_question_blueprint(
    state: InterviewState,
    question_key: str | None = None,
) -> QuestionBlueprint | None:
    """Get the blueprint matching a main question or one of its follow-ups."""

    main_question_key = resolve_main_question_key(state, question_key=question_key)
    if not main_question_key:
        return None

    for blueprint in get_question_blueprints(state):
        if str(blueprint.get("question_key")) == main_question_key:
            return blueprint
    return None


def get_current_blueprint(state: InterviewState) -> QuestionBlueprint | None:
    """Get the blueprint for the current question."""

    return get_question_blueprint(state, state.get("current_question_key"))


def resolve_main_question_key(
    state: InterviewState,
    *,
    question_key: str | None = None,
    question: dict[str, Any] | None = None,
) -> str | None:
    """Resolve the owning main question key from any question context."""

    if question is None and question_key:
        question = next(
            (item for item in list_questions(state) if item["question_key"] == question_key),
            None,
        )
    if question is None and not question_key:
        question = get_current_question(state)
    if question is None:
        return question_key
    if bool(question.get("is_follow_up")) and question.get("parent_question_key"):
        return str(question["parent_question_key"])
    return str(question.get("question_key")) if question.get("question_key") else None


def get_main_question_coverage(
    state: InterviewState,
    main_question_key: str | None,
) -> TopicCoverageStatus | None:
    """Read coverage by main_question_key."""

    if not main_question_key:
        return None
    coverage_status = state.get("coverage_status", {})
    return deepcopy(coverage_status.get(main_question_key))


def get_topic_coverage(
    state: InterviewState,
    topic_key: str | None,
) -> TopicCoverageStatus | None:
    """Backward-compatible alias for coverage lookup."""

    return get_main_question_coverage(state, topic_key)


def get_latest_observation(
    state: InterviewState,
    *,
    question_key: str | None = None,
    main_question_key: str | None = None,
    topic_key: str | None = None,
) -> AnswerObservation | None:
    """Read the latest answer observation filtered by question or main question."""

    effective_main_question_key = main_question_key or topic_key
    observations = list(state.get("answer_observations", []))
    for observation in reversed(observations):
        if question_key and observation.get("question_key") != question_key:
            continue
        if effective_main_question_key and observation.get("main_question_key") != effective_main_question_key:
            continue
        return deepcopy(observation)
    return None


def choose_next_main_question(
    state: InterviewState,
    *,
    preferred_main_question_key: str | None = None,
    preferred_topic_key: str | None = None,
) -> dict[str, Any] | None:
    """Choose the next main question using the plan first, then round order."""

    main_questions = {question["question_key"]: question for question in get_main_questions(state)}
    preferred_key = preferred_main_question_key or preferred_topic_key

    def _is_unfinished(question: dict[str, Any]) -> bool:
        return question.get("status") != InterviewQuestionStatus.ANSWERED.value

    if preferred_key:
        candidate = main_questions.get(preferred_key)
        if candidate and _is_unfinished(candidate):
            return candidate

    remaining_keys = list(state.get("remaining_main_question_keys", []))
    for main_question_key in remaining_keys:
        candidate = main_questions.get(main_question_key)
        if candidate and _is_unfinished(candidate):
            return candidate

    for blueprint in get_question_blueprints(state):
        candidate = main_questions.get(str(blueprint.get("question_key")))
        if candidate and _is_unfinished(candidate):
            return candidate

    return find_next_main_question(state)


def hydrate_plan_state(
    state: InterviewState,
    *,
    max_follow_up_questions: int = 2,
) -> None:
    """Backfill and normalize global plan fields for old or partial states."""

    questions = list_questions(state)
    blueprint_inputs = list(state.get("interview_plan", {}).get("question_blueprints", []))
    if not blueprint_inputs:
        blueprint_inputs = _derive_blueprints_from_questions(questions)
    blueprints = [_normalize_blueprint(item) for item in blueprint_inputs if item.get("question_key")]
    ordered_blueprints = sorted(
        blueprints,
        key=lambda item: (int(item.get("round_index", 0) or 0), str(item.get("question_key", ""))),
    )
    main_question_keys = [str(item["question_key"]) for item in ordered_blueprints]
    raw_blueprint_by_question_key = {
        str(item.get("question_key", "")).strip(): item
        for item in blueprint_inputs
        if str(item.get("question_key", "")).strip()
    }
    topic_to_main_question_key = _build_legacy_topic_mapping(
        raw_blueprints=blueprint_inputs,
        normalized_blueprints=ordered_blueprints,
    )

    existing_plan = deepcopy(state.get("interview_plan", {}))
    interview_plan: InterviewPlan = {
        "plan_version": str(existing_plan.get("plan_version", "v3")),
        "plan_summary": str(existing_plan.get("plan_summary", "")).strip(),
        "question_blueprints": deepcopy(ordered_blueprints),
        "termination_policy": {
            "max_rounds": int(
                state.get("max_rounds", existing_plan.get("termination_policy", {}).get("max_rounds", 0)) or 0
            ),
            "max_follow_up_per_main_question": max_follow_up_questions,
            "coverage_threshold": float(
                existing_plan.get("termination_policy", {}).get("coverage_threshold", 0.7)
            ),
            "early_finish_rule": str(
                existing_plan.get("termination_policy", {}).get(
                    "early_finish_rule",
                    "all required main questions covered and current main question closed",
                )
            ),
        },
        "interview_strategy": {
            "style": str(existing_plan.get("interview_strategy", {}).get("style", "structured")),
            "difficulty": str(existing_plan.get("interview_strategy", {}).get("difficulty", "adaptive")),
            "max_rounds": int(state.get("max_rounds", 0)),
            "max_follow_up_per_main_question": max_follow_up_questions,
            "plan_stability": str(
                existing_plan.get("interview_strategy", {}).get("plan_stability", "stable_main_plan")
            ),
        },
    }

    required_main_question_keys = [
        str(blueprint["question_key"])
        for blueprint in ordered_blueprints
        if not bool(blueprint.get("can_skip", False))
    ]
    optional_main_question_keys = [
        str(blueprint["question_key"])
        for blueprint in ordered_blueprints
        if bool(blueprint.get("can_skip", False))
    ]

    raw_coverage_status = deepcopy(state.get("coverage_status", {}))
    normalized_coverage_status: dict[str, TopicCoverageStatus] = {}
    for blueprint in ordered_blueprints:
        main_question_key = str(blueprint["question_key"])
        raw_blueprint = raw_blueprint_by_question_key.get(main_question_key, {})
        legacy_topic_key = str(raw_blueprint.get("topic_key", "")).strip() or main_question_key
        existing = _pick_coverage_entry(
            raw_coverage_status,
            keys=[main_question_key, legacy_topic_key],
        )
        required = main_question_key in required_main_question_keys
        default_signals = list(blueprint.get("must_observe_signals", []))
        observed_signals = _unique_list(existing.get("observed_signals", []))
        missing_signals = _unique_list(existing.get("missing_signals", default_signals))
        status = str(existing.get("status", "not_started"))
        completed = bool(existing.get("completed", False) or status == "covered")
        if completed:
            status = "covered"
        normalized_coverage_status[main_question_key] = TopicCoverageStatus(
            main_question_key=main_question_key,
            question_key=main_question_key,
            required=required,
            status=status,
            confidence=float(existing.get("confidence", 0.0) or 0.0),
            observed_signals=observed_signals,
            missing_signals=missing_signals,
            follow_up_count=int(existing.get("follow_up_count", 0) or 0),
            evidence_count=int(existing.get("evidence_count", 0) or 0),
            completed=completed,
            last_updated=existing.get("last_updated"),
        )

    normalized_observations = [
        _normalize_observation(
            observation,
            topic_to_main_question_key=topic_to_main_question_key,
            state=state,
        )
        for observation in list(state.get("answer_observations", []))
    ]

    current_question = get_current_question(state)
    current_question_main_key = resolve_main_question_key(state, question=current_question)

    raw_progress = deepcopy(state.get("plan_progress", {}))
    history = [
        _normalize_history_event(item, topic_to_main_question_key=topic_to_main_question_key)
        for item in list(raw_progress.get("history", []))
        if isinstance(item, dict)
    ]

    covered_main_question_keys = _unique_list(
        list(raw_progress.get("covered_main_question_keys", []))
        + [
            topic_to_main_question_key.get(str(item), str(item))
            for item in raw_progress.get("covered_topics", [])
        ]
        + [
            main_question_key
            for main_question_key, coverage in normalized_coverage_status.items()
            if coverage.get("completed")
        ]
    )
    closed_main_question_keys = _unique_list(
        list(raw_progress.get("closed_main_question_keys", []))
        + list(raw_progress.get("closed_question_keys", []))
        + [
            topic_to_main_question_key.get(str(item), str(item))
            for item in raw_progress.get("closed_topics", [])
        ]
        + covered_main_question_keys
    )

    raw_remaining_main_keys = [
        str(item)
        for item in raw_progress.get("remaining_main_question_keys", state.get("remaining_main_question_keys", []))
        if str(item)
    ]
    raw_remaining_main_keys += [
        topic_to_main_question_key.get(str(item), str(item))
        for item in raw_progress.get("remaining_topics", state.get("remaining_topics", []))
        if str(item)
    ]
    remaining_main_question_keys = _merge_remaining_main_question_keys(
        explicit_keys=raw_remaining_main_keys,
        ordered_keys=main_question_keys,
        coverage_status=normalized_coverage_status,
    )

    current_main_question_key = (
        state.get("current_main_question_key")
        or topic_to_main_question_key.get(str(state.get("current_topic_key")), None)
        or raw_progress.get("current_main_question_key")
        or topic_to_main_question_key.get(str(raw_progress.get("current_topic_key")), None)
        or current_question_main_key
        or (remaining_main_question_keys[0] if remaining_main_question_keys else None)
        or (main_question_keys[0] if main_question_keys else None)
    )

    plan_progress: PlanProgress = {
        "covered_main_question_keys": covered_main_question_keys,
        "closed_main_question_keys": closed_main_question_keys,
        "current_main_question_key": current_main_question_key,
        "remaining_main_question_keys": remaining_main_question_keys,
        "rounds_used": int(max(state.get("current_round", 0), raw_progress.get("rounds_used", 0) or 0)),
        "evidence_count": max(
            int(len(normalized_observations)),
            int(raw_progress.get("evidence_count", 0) or 0),
        ),
        "history": history[-50:],
    }

    raw_termination = deepcopy(state.get("termination_decision_context", {}))
    remaining_required_main_question_keys = [
        main_question_key
        for main_question_key in required_main_question_keys
        if not normalized_coverage_status.get(main_question_key, {}).get("completed")
    ]
    remaining_optional_main_question_keys = [
        main_question_key
        for main_question_key in optional_main_question_keys
        if not normalized_coverage_status.get(main_question_key, {}).get("completed")
    ]
    latest_decision = _normalize_latest_decision(
        raw_termination.get("latest_decision", {}),
        topic_to_main_question_key=topic_to_main_question_key,
    )

    termination_context: TerminationDecisionContext = {
        "current_main_question_key": current_main_question_key,
        "all_required_main_questions_covered": not remaining_required_main_question_keys,
        "remaining_required_main_question_keys": remaining_required_main_question_keys,
        "remaining_optional_main_question_keys": remaining_optional_main_question_keys,
        "remaining_round_budget": max(
            0,
            int(state.get("max_rounds", 0)) - int(state.get("current_round", 0)),
        ),
        "latest_decision": latest_decision,
    }

    state["questions"] = questions
    state["interview_plan"] = interview_plan
    state["coverage_status"] = normalized_coverage_status
    state["answer_observations"] = normalized_observations[-20:]
    state["current_main_question_key"] = current_main_question_key
    state["remaining_main_question_keys"] = remaining_main_question_keys
    state["plan_progress"] = plan_progress
    state["termination_decision_context"] = termination_context

    state.pop("current_topic_key", None)
    state.pop("remaining_topics", None)


def build_session_context_payload(state: InterviewState) -> dict[str, Any]:
    """Build a stable persisted workflow payload."""

    hydrate_plan_state(state)
    questions = list_questions(state)
    return {
        "current_question_key": state.get("current_question_key"),
        "current_main_question_key": state.get("current_main_question_key"),
        "follow_up_count": state.get("follow_up_count", 0),
        "planned_question_keys": [
            question["question_key"]
            for question in questions
            if not bool(question.get("is_follow_up"))
        ],
        "remaining_main_question_keys": deepcopy(state.get("remaining_main_question_keys", [])),
        "interview_plan": deepcopy(state.get("interview_plan", {})),
        "plan_progress": deepcopy(state.get("plan_progress", {})),
        "coverage_status": deepcopy(state.get("coverage_status", {})),
        "answer_observations": deepcopy(state.get("answer_observations", [])),
        "termination_decision_context": deepcopy(state.get("termination_decision_context", {})),
        "latest_tool_context": deepcopy(state.get("latest_tool_context", {})),
        "last_draft_answer": deepcopy(state.get("last_draft_answer", {})),
        "resume_markdown": state.get("resume_markdown", ""),
        "resume_metadata": deepcopy(state.get("resume_metadata", {})),
        "latest_feedback": deepcopy(state.get("feedback", {})),
        "next_action": state.get("next_action"),
        "action_reason": state.get("action_reason"),
        "completion_message": state.get("completion_message"),
        "assistant_message": state.get("assistant_message"),
        "workflow_state": serialize_state(state),
    }


def _normalize_blueprint(blueprint: dict[str, Any]) -> QuestionBlueprint:
    """Normalize planner output or legacy blueprint data."""

    question_key = str(blueprint.get("question_key", "")).strip()
    category_key = str(blueprint.get("category_key", "GENERAL") or "GENERAL").strip()
    question_text = str(blueprint.get("question_text", "")).strip()
    round_index = int(blueprint.get("round_index", 0) or 0)
    must_observe_signals = _unique_list(
        [str(item).strip() for item in blueprint.get("must_observe_signals", []) if str(item).strip()]
    )
    follow_up_focus = _unique_list(
        [str(item).strip() for item in blueprint.get("follow_up_focus", []) if str(item).strip()]
    )
    completion_criteria = _unique_list(
        [str(item).strip() for item in blueprint.get("completion_criteria", []) if str(item).strip()]
    )
    return QuestionBlueprint(
        question_key=question_key,
        category_key=category_key or "GENERAL",
        question_text=question_text,
        round_index=round_index,
        intent=str(blueprint.get("intent", "")).strip(),
        must_observe_signals=must_observe_signals,
        follow_up_focus=follow_up_focus or must_observe_signals,
        completion_criteria=completion_criteria or must_observe_signals,
        priority=max(1, int(blueprint.get("priority", round_index or 1) or 1)),
        can_skip=bool(blueprint.get("can_skip", False)),
    )


def _derive_blueprints_from_questions(questions: list[dict[str, Any]]) -> list[QuestionBlueprint]:
    """Derive a minimal plan when no explicit planner output exists."""

    derived_blueprints: list[QuestionBlueprint] = []
    for question in questions:
        if bool(question.get("is_follow_up")):
            continue
        category_key = str(question.get("category_key", "GENERAL") or "GENERAL")
        derived_blueprints.append(
            QuestionBlueprint(
                question_key=str(question["question_key"]),
                category_key=category_key,
                question_text=str(question["question_text"]),
                round_index=int(question["round_index"]),
                intent=f"Assess the candidate's ability on main question {question['question_key']}.",
                must_observe_signals=["implementation details", "tradeoffs", "results"],
                follow_up_focus=["implementation details", "tradeoffs", "pitfalls"],
                completion_criteria=[
                    "candidate explains what they did",
                    "candidate explains why they did it",
                    "candidate describes outcomes or lessons",
                ],
                priority=int(question["round_index"]),
                can_skip=False,
            )
        )
    return derived_blueprints


def _build_legacy_topic_mapping(
    *,
    raw_blueprints: list[dict[str, Any]],
    normalized_blueprints: list[QuestionBlueprint],
) -> dict[str, str]:
    """Map old topic keys to the new main question keys."""

    mapping: dict[str, str] = {}
    for raw_blueprint, normalized_blueprint in zip(raw_blueprints, normalized_blueprints, strict=False):
        question_key = str(normalized_blueprint.get("question_key", "")).strip()
        if not question_key:
            continue
        mapping[question_key] = question_key
        legacy_topic_key = str(raw_blueprint.get("topic_key", "")).strip()
        if legacy_topic_key:
            mapping[legacy_topic_key] = question_key
    return mapping


def _pick_coverage_entry(
    coverage_status: dict[str, Any],
    *,
    keys: list[str],
) -> dict[str, Any]:
    """Pick the first matching coverage entry from possible legacy keys."""

    for key in keys:
        if key and key in coverage_status and isinstance(coverage_status[key], dict):
            existing = deepcopy(coverage_status[key])
            if "main_question_key" not in existing and key:
                existing["main_question_key"] = key
            return existing
    return {}


def _normalize_observation(
    observation: dict[str, Any],
    *,
    topic_to_main_question_key: dict[str, str],
    state: InterviewState,
) -> AnswerObservation:
    """Normalize one answer observation from new or legacy schema."""

    question_key = str(observation.get("question_key", "")).strip()
    main_question_key = str(observation.get("main_question_key", "")).strip()
    if not main_question_key:
        legacy_topic_key = str(observation.get("topic_key", "")).strip()
        if legacy_topic_key:
            main_question_key = topic_to_main_question_key.get(legacy_topic_key, legacy_topic_key)
    if not main_question_key:
        main_question_key = resolve_main_question_key(state, question_key=question_key) or question_key

    return AnswerObservation(
        question_key=question_key,
        main_question_key=main_question_key,
        answer_text_summary=str(observation.get("answer_text_summary", "")).strip(),
        search_keywords=_unique_list(
            [str(item).strip() for item in observation.get("search_keywords", []) if str(item).strip()]
        ),
        observed_signals=_unique_list(
            [str(item).strip() for item in observation.get("observed_signals", []) if str(item).strip()]
        ),
        missing_signals=_unique_list(
            [str(item).strip() for item in observation.get("missing_signals", []) if str(item).strip()]
        ),
        main_question_status=str(
            observation.get("main_question_status", observation.get("topic_status", "partial"))
        ),
        confidence=float(observation.get("confidence", 0.0) or 0.0),
        suggested_action=str(observation.get("suggested_action", "follow_up")),
        reasoning=str(observation.get("reasoning", "")).strip(),
        created_at=str(observation.get("created_at", utc_now_iso())),
    )


def _normalize_history_event(
    event: dict[str, Any],
    *,
    topic_to_main_question_key: dict[str, str],
) -> dict[str, Any]:
    """Normalize history records to the main-question schema."""

    normalized = deepcopy(event)
    main_question_key = normalized.get("main_question_key")
    if not main_question_key and normalized.get("topic_key"):
        main_question_key = topic_to_main_question_key.get(
            str(normalized.get("topic_key")),
            str(normalized.get("topic_key")),
        )
    if main_question_key:
        normalized["main_question_key"] = str(main_question_key)
    normalized.pop("topic_key", None)

    if "topic_status" in normalized and "main_question_status" not in normalized:
        normalized["main_question_status"] = normalized["topic_status"]
    normalized.pop("topic_status", None)

    if "next_topic_key" in normalized and "next_main_question_key" not in normalized:
        next_key = topic_to_main_question_key.get(
            str(normalized["next_topic_key"]),
            str(normalized["next_topic_key"]),
        )
        normalized["next_main_question_key"] = next_key
    normalized.pop("next_topic_key", None)
    return normalized


def _normalize_latest_decision(
    latest_decision: dict[str, Any],
    *,
    topic_to_main_question_key: dict[str, str],
) -> dict[str, Any]:
    """Normalize replanner decision snapshots to the main-question schema."""

    if not isinstance(latest_decision, dict):
        return {}

    normalized = deepcopy(latest_decision)
    if "topic_status" in normalized and "main_question_status" not in normalized:
        normalized["main_question_status"] = normalized["topic_status"]
    normalized.pop("topic_status", None)

    if "next_topic_key" in normalized and "next_main_question_key" not in normalized:
        next_key = topic_to_main_question_key.get(
            str(normalized["next_topic_key"]),
            str(normalized["next_topic_key"]),
        )
        normalized["next_main_question_key"] = next_key
    normalized.pop("next_topic_key", None)
    return normalized


def _merge_remaining_main_question_keys(
    *,
    explicit_keys: list[str],
    ordered_keys: list[str],
    coverage_status: dict[str, TopicCoverageStatus],
) -> list[str]:
    """Merge explicit remaining keys with blueprint order while removing completed ones."""

    completed_keys = {
        key
        for key, coverage in coverage_status.items()
        if bool(coverage.get("completed", False))
    }
    result: list[str] = []
    for key in explicit_keys:
        if key not in ordered_keys or key in completed_keys:
            continue
        if key not in result:
            result.append(key)
    for key in ordered_keys:
        if key in completed_keys or key in result:
            continue
        result.append(key)
    return result


def _unique_list(values: list[Any]) -> list[Any]:
    """Keep list order while removing empty duplicates."""

    seen: set[Any] = set()
    result: list[Any] = []
    for value in values:
        normalized = value.strip() if isinstance(value, str) else value
        if normalized in (None, "", []):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
