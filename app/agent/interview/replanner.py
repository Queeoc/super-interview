"""面试下一步动作决策节点。"""

from __future__ import annotations

from typing import Literal

from loguru import logger
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.config import config
from app.models.interview import InterviewWorkflowAction

from .prompts import InterviewPromptRunner
from .state import InterviewState, clone_state, find_next_main_question, get_current_question


class _ReplanDecision(BaseModel):
    """Replanner 结构化输出。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    action: Literal["follow_up", "next_question", "complete"] = Field(
        ...,
        validation_alias=AliasChoices("next_action", "action"),
        description="下一步动作",
    )
    reason: str = Field(..., min_length=1, description="决策原因")
    next_question: str | None = Field(
        default=None,
        min_length=1,
        description="模型偶尔附带的下一题文本，业务层忽略",
    )


class InterviewReplanner:
    """根据最新答案决定追问、切题或结束。"""

    def __init__(self, prompt_runner: InterviewPromptRunner) -> None:
        self._prompt_runner = prompt_runner

    async def run(self, state: InterviewState) -> InterviewState:
        """为当前答案做下一步动作决策。"""

        working_state = clone_state(state)
        current_question = get_current_question(working_state)
        if current_question is None:
            working_state["next_action"] = InterviewWorkflowAction.COMPLETE.value
            working_state["action_reason"] = "当前题目缺失，结束面试"
            return working_state

        decision = await self._decide_next_action(working_state)
        working_state["next_action"] = decision.action
        working_state["action_reason"] = decision.reason
        logger.info(
            "面试下一步决策完成: session_id={}, action={}, reason={}",
            working_state["session_id"],
            decision.action,
            decision.reason,
        )
        return working_state

    async def _decide_next_action(self, state: InterviewState) -> _ReplanDecision:
        """优先使用 LLM 结构化决策，失败时回退到规则判断。"""

        current_question = get_current_question(state)
        if current_question is None:
            return _ReplanDecision(
                action=InterviewWorkflowAction.COMPLETE.value,
                reason="当前题目不存在，无法继续",
            )

        skill = state["skill"]
        variables = {
            "skill_name": skill["display_name"],
            "current_round": state.get("current_round", 0),
            "max_rounds": state.get("max_rounds", 1),
            "follow_up_count": state.get("follow_up_count", 0),
            "max_follow_up_questions": config.interview.max_follow_up_questions,
            "current_question": self._prompt_runner.wrap_untrusted_text(
                "current_question",
                current_question["question_text"],
            ),
            "user_answer": self._prompt_runner.wrap_untrusted_text(
                "user_answer",
                state.get("latest_answer_text", ""),
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
                "面试决策触发规则兜底: fallback_applied=true, fallback_type=rule, stage=replanner, error={}",
                exc,
            )

        return self._fallback_decision(state)

    def _fallback_decision(self, state: InterviewState) -> _ReplanDecision:
        """按预设规则给出稳定决策。"""

        latest_answer_text = state.get("latest_answer_text", "").strip()
        follow_up_count = state.get("follow_up_count", 0)
        next_main_question = find_next_main_question(state)

        if (
            len(latest_answer_text) < 40
            and follow_up_count < config.interview.max_follow_up_questions
        ):
            return _ReplanDecision(
                action=InterviewWorkflowAction.FOLLOW_UP.value,
                reason="答案偏短，且当前主问题仍可继续追问",
            )

        if next_main_question is None:
            return _ReplanDecision(
                action=InterviewWorkflowAction.COMPLETE.value,
                reason="主问题已全部完成，结束面试",
            )

        if state.get("current_round", 0) >= state.get("max_rounds", 1):
            return _ReplanDecision(
                action=InterviewWorkflowAction.COMPLETE.value,
                reason="已达到最大轮次，结束面试",
            )

        return _ReplanDecision(
            action=InterviewWorkflowAction.NEXT_QUESTION.value,
            reason="答案已满足最小信息量，进入下一题",
        )
