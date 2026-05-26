"""Phase 5 文字面试工作流状态与辅助函数。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, TypedDict

from app.models.interview import (
    InterviewQuestionSnapshot,
    InterviewQuestionStatus,
)


def utc_now_iso() -> str:
    """返回 ISO 格式的 UTC 时间字符串。"""

    return datetime.now(timezone.utc).isoformat()


class InterviewSkillContext(TypedDict):
    """面试工作流依赖的 Skill 上下文。"""

    skill_id: str
    display_name: str
    description: str
    content_markdown: str
    reference_markdown: str
    categories: list[dict[str, Any]]
    reference_files: list[str]


class InterviewState(TypedDict, total=False):
    """面试工作流运行态。

    说明：
    - 仅保存可序列化字段，便于写入 Redis 和 session_context_json。
    - questions 列表既包含主问题，也包含运行时生成的追问题。
    """

    session_id: str
    user_id: str | None
    resume_id: str | None
    title: str | None
    language: str
    max_rounds: int
    skill: InterviewSkillContext
    questions: list[dict[str, Any]]
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
    user_id: str | None,
    resume_id: str | None,
) -> InterviewState:
    """创建一个可直接进入初始化工作流的状态对象。"""

    return {
        "session_id": session_id,
        "user_id": user_id,
        "resume_id": resume_id,
        "title": title,
        "language": language,
        "max_rounds": max_rounds,
        "skill": skill,
        "questions": [],
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


def clone_state(state: InterviewState) -> InterviewState:
    """深拷贝状态，避免节点内直接污染上游输入。"""

    return deepcopy(state)


def serialize_state(state: InterviewState) -> dict[str, Any]:
    """把状态转换为稳定的可 JSON 序列化字典。"""

    return deepcopy(state)


def normalize_question_snapshot(question: dict[str, Any] | InterviewQuestionSnapshot) -> dict[str, Any]:
    """将题目快照统一转换为标准字典结构。"""

    if isinstance(question, InterviewQuestionSnapshot):
        return question.model_dump(mode="json")
    return InterviewQuestionSnapshot.model_validate(question).model_dump(mode="json")


def list_questions(state: InterviewState) -> list[dict[str, Any]]:
    """返回当前状态中的题目列表。"""

    return [normalize_question_snapshot(question) for question in state.get("questions", [])]


def get_current_question(state: InterviewState) -> dict[str, Any] | None:
    """根据 current_question_key 读取当前题目快照。"""

    current_question_key = state.get("current_question_key")
    if not current_question_key:
        return None

    for question in list_questions(state):
        if question["question_key"] == current_question_key:
            return question
    return None


def upsert_question(state: InterviewState, question: dict[str, Any] | InterviewQuestionSnapshot) -> None:
    """向状态中追加或替换一个题目快照。"""

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
    """把指定题目标记为已提问。"""

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
    """把指定题目标记为已回答。"""

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
    """返回所有主问题，并按轮次排序。"""

    questions = [
        question
        for question in list_questions(state)
        if not bool(question.get("is_follow_up"))
    ]
    return sorted(questions, key=lambda item: (item["round_index"], item["question_key"]))


def get_follow_up_count_for_round(state: InterviewState, round_index: int) -> int:
    """统计指定主问题轮次已生成的追问题数量。"""

    return sum(
        1
        for question in list_questions(state)
        if bool(question.get("is_follow_up")) and question["round_index"] == round_index
    )


def find_next_main_question(state: InterviewState) -> dict[str, Any] | None:
    """查找下一条尚未提问的主问题。"""

    current_round = state.get("current_round", 0)
    for question in get_main_questions(state):
        if question["round_index"] <= current_round:
            continue
        return question
    return None


def build_session_context_payload(state: InterviewState) -> dict[str, Any]:
    """提取用于 session_context_json 的运行态快照。"""

    questions = list_questions(state)
    return {
        "current_question_key": state.get("current_question_key"),
        "follow_up_count": state.get("follow_up_count", 0),
        "planned_question_keys": [
            question["question_key"]
            for question in questions
            if not bool(question.get("is_follow_up"))
        ],
        "last_draft_answer": deepcopy(state.get("last_draft_answer", {})),
        "latest_feedback": deepcopy(state.get("feedback", {})),
        "next_action": state.get("next_action"),
        "action_reason": state.get("action_reason"),
        "completion_message": state.get("completion_message"),
        "assistant_message": state.get("assistant_message"),
        "workflow_state": serialize_state(state),
    }

