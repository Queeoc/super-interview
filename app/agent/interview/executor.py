"""面试提问、追问与结束执行节点。"""

from __future__ import annotations

from typing import Any

from loguru import logger
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.models.interview import (
    InterviewQuestionSnapshot,
    InterviewQuestionSource,
    InterviewQuestionStatus,
    InterviewWorkflowAction,
)

from .prompts import InterviewPromptRunner
from .state import (
    InterviewState,
    clone_state,
    find_next_main_question,
    get_current_question,
    get_follow_up_count_for_round,
    get_main_questions,
    mark_question_asked,
    upsert_question,
)


class _FollowUpOutput(BaseModel):
    """追问题结构化输出。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    question_text: str = Field(
        ...,
        validation_alias=AliasChoices("question_text", "follow_up_question"),
        min_length=1,
        description="追问内容",
    )


class _CompletionOutput(BaseModel):
    """结束语结构化输出。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    closing_message: str = Field(..., min_length=1, description="面试结束时对候选人的说明")


class InterviewExecutor:
    """根据 next_action 将状态推进到下一题或结束。"""

    def __init__(self, prompt_runner: InterviewPromptRunner) -> None:
        self._prompt_runner = prompt_runner

    async def run(self, state: InterviewState) -> InterviewState:
        """执行一次面试流转动作。"""

        working_state = clone_state(state)
        action = working_state.get("next_action", InterviewWorkflowAction.INITIAL_ASK.value)

        if action == InterviewWorkflowAction.INITIAL_ASK.value:
            return self._ask_initial_question(working_state)
        if action == InterviewWorkflowAction.FOLLOW_UP.value:
            return await self._ask_follow_up_question(working_state)
        if action == InterviewWorkflowAction.NEXT_QUESTION.value:
            return await self._ask_next_main_question(working_state)
        return await self._complete_interview(working_state)

    def _ask_initial_question(self, state: InterviewState) -> InterviewState:
        """提问首个主问题。"""

        first_question = get_main_questions(state)[0] if get_main_questions(state) else None
        if first_question is None:
            state["next_action"] = InterviewWorkflowAction.COMPLETE.value
            state["action_reason"] = "未生成任何主问题，直接结束会话"
            return state

        asked_question = mark_question_asked(state, first_question["question_key"])
        state["current_question_key"] = first_question["question_key"]
        state["current_round"] = first_question["round_index"]
        state["assistant_message"] = asked_question["question_text"] if asked_question else first_question["question_text"]
        return state

    async def _ask_follow_up_question(self, state: InterviewState) -> InterviewState:
        """围绕当前主问题生成追问。"""

        current_question = get_current_question(state)
        skill = state["skill"]
        if current_question is None:
            state["next_action"] = InterviewWorkflowAction.COMPLETE.value
            state["action_reason"] = "当前题目不存在，无法继续追问"
            return state

        main_question_key = (
            current_question.get("parent_question_key")
            if current_question.get("is_follow_up")
            else current_question["question_key"]
        ) or current_question["question_key"]
        round_index = int(current_question["round_index"])
        follow_up_index = get_follow_up_count_for_round(state, round_index) + 1
        follow_up_key = f"q-{round_index}-f-{follow_up_index}"

        follow_up_text = await self._generate_follow_up_text(
            skill=skill,
            current_question=current_question,
            answer_text=state.get("latest_answer_text", ""),
            resume_markdown=str(state.get("resume_markdown", "")),
            resume_metadata=state.get("resume_metadata", {}),
        )
        follow_up_question = InterviewQuestionSnapshot(
            question_key=follow_up_key,
            round_index=round_index,
            category_key=current_question["category_key"],
            question_text=follow_up_text,
            parent_question_key=main_question_key,
            source=InterviewQuestionSource.FOLLOW_UP.value,
            status=InterviewQuestionStatus.ASKED.value,
            is_follow_up=True,
        )
        normalized_question = follow_up_question.model_dump(mode="json")
        normalized_question["asked_at"] = normalized_question.get("asked_at") or None
        upsert_question(state, normalized_question)
        mark_question_asked(state, follow_up_key)

        state["current_question_key"] = follow_up_key
        state["current_round"] = round_index
        state["follow_up_count"] = follow_up_index
        state["assistant_message"] = follow_up_text
        return state

    async def _ask_next_main_question(self, state: InterviewState) -> InterviewState:
        """切换到下一条主问题。"""

        next_question = find_next_main_question(state)
        if next_question is None:
            state["next_action"] = InterviewWorkflowAction.COMPLETE.value
            state["action_reason"] = "没有剩余主问题，准备结束面试"
            return await self._complete_interview(state)

        asked_question = mark_question_asked(state, next_question["question_key"])
        state["current_question_key"] = next_question["question_key"]
        state["current_round"] = next_question["round_index"]
        state["follow_up_count"] = 0
        state["assistant_message"] = asked_question["question_text"] if asked_question else next_question["question_text"]
        return state

    async def _complete_interview(self, state: InterviewState) -> InterviewState:
        """生成结束语。"""

        completion = await self._generate_completion_payload(state)
        state["completed"] = True
        state["current_question_key"] = None
        state["assistant_message"] = completion.closing_message
        state["completion_message"] = completion.closing_message
        state["report_summary"] = {}
        return state

    async def _generate_follow_up_text(
        self,
        *,
        skill: dict[str, Any],
        current_question: dict[str, Any],
        answer_text: str,
        resume_markdown: str | None = None,
        resume_metadata: dict[str, Any] | None = None,
    ) -> str:
        """优先借助 LLM 生成追问。"""

        resume_markdown_text = (
            resume_markdown if resume_markdown is not None else str(skill.get("resume_markdown", ""))
        )
        resume_metadata_value = (
            resume_metadata if resume_metadata is not None else skill.get("resume_metadata", {})
        )
        variables = {
            "skill_name": skill["display_name"],
            "resume_markdown": self._prompt_runner.wrap_untrusted_text(
                "resume_markdown",
                str(resume_markdown_text),
            ),
            "resume_metadata": self._prompt_runner.wrap_untrusted_text(
                "resume_metadata",
                str(resume_metadata_value),
            ),
            "skill_markdown": self._prompt_runner.wrap_untrusted_text(
                "skill_markdown",
                skill["content_markdown"],
            ),
            "reference_markdown": self._prompt_runner.wrap_untrusted_text(
                "reference_markdown",
                skill["reference_markdown"],
            ),
            "current_question": self._prompt_runner.wrap_untrusted_text(
                "current_question",
                current_question["question_text"],
            ),
            "user_answer": self._prompt_runner.wrap_untrusted_text(
                "user_answer",
                answer_text,
            ),
        }
        try:
            output = await self._prompt_runner.ainvoke_structured(
                template_name="executor_system.st",
                schema=_FollowUpOutput,
                variables=variables,
                temperature=0.3,
            )
            if output.question_text.strip():
                return output.question_text.strip()
        except Exception as exc:
            logger.warning(
                "追问题生成触发规则兜底: fallback_applied=true, fallback_type=rule, stage=executor_follow_up, error={}",
                exc,
            )

        return (
            "你刚才的回答还不够具体。"
            f"请结合题目“{current_question['question_text']}”，补充你的实现思路、关键权衡以及实际落地经验。"
        )

    async def _generate_completion_payload(self, state: InterviewState) -> _CompletionOutput:
        """优先借助 LLM 生成结束语，否则回退到规则模板。"""

        skill = state["skill"]
        answered_questions = [
            question
            for question in state.get("questions", [])
            if question.get("status") == InterviewQuestionStatus.ANSWERED.value
        ]
        question_summary = "\n".join(
            [
                f"- [{question['question_key']}] {question['question_text']}"
                for question in answered_questions
            ]
        ) or "- 本次会话尚未形成有效问答。"
        variables = {
            "skill_name": skill["display_name"],
            "answered_question_count": len(answered_questions),
            "question_summary": self._prompt_runner.wrap_untrusted_text(
                "question_summary",
                question_summary,
            ),
        }

        try:
            output = await self._prompt_runner.ainvoke_structured(
                template_name="completion_system.st",
                schema=_CompletionOutput,
                variables=variables,
                temperature=0.2,
            )
            return output
        except Exception as exc:
            logger.warning(
                "结束语生成触发规则兜底: fallback_applied=true, fallback_type=rule, stage=executor_completion, error={}",
                exc,
            )

        return _CompletionOutput(
            closing_message=(
                f"{skill['display_name']} 文字面试先到这里。"
                "本轮回答已经保存，稍后会生成统一评估报告。"
            ),
        )
