"""面试主问题计划节点。"""

from __future__ import annotations

from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from app.models.interview import (
    InterviewQuestionSnapshot,
    InterviewQuestionSource,
    InterviewQuestionStatus,
)

from .prompts import InterviewPromptRunner
from .state import InterviewState, clone_state


class _PlannedQuestionItem(BaseModel):
    """LLM 生成的主问题条目。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    category_key: str = Field(
        ...,
        validation_alias="category",
        description="Skill 分类标识",
    )
    question_text: str = Field(
        ...,
        validation_alias="question",
        min_length=1,
        description="主问题正文",
    )


class _PlannerOutput(BaseModel):
    """Planner 的结构化输出。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    questions: list[_PlannedQuestionItem] = Field(default_factory=list, description="主问题计划")


class InterviewPlanner:
    """根据 Skill 和 reference 生成主问题计划。"""

    def __init__(self, prompt_runner: InterviewPromptRunner) -> None:
        self._prompt_runner = prompt_runner

    async def run(self, state: InterviewState) -> InterviewState:
        """生成主问题列表并写回状态。"""

        working_state = clone_state(state)
        skill = working_state["skill"]
        max_rounds = working_state["max_rounds"]

        planned_questions = await self._build_question_plan(working_state)
        question_snapshots: list[dict[str, Any]] = []

        for index, planned_question in enumerate(planned_questions[:max_rounds], start=1):
            question_snapshots.append(
                InterviewQuestionSnapshot(
                    question_key=f"q-{index}",
                    round_index=index,
                    category_key=planned_question.category_key,
                    question_text=planned_question.question_text,
                    parent_question_key=None,
                    source=InterviewQuestionSource.PLANNED.value,
                    status=InterviewQuestionStatus.PLANNED.value,
                    is_follow_up=False,
                ).model_dump(mode="json")
            )

        working_state["questions"] = question_snapshots
        working_state["action_reason"] = (
            f"已基于 Skill {skill['display_name']} 生成 {len(question_snapshots)} 个主问题"
        )
        logger.info(
            "面试主问题计划生成完成: session_id={}, question_count={}",
            working_state["session_id"],
            len(question_snapshots),
        )
        return working_state

    async def _build_question_plan(self, state: InterviewState) -> list[_PlannedQuestionItem]:
        """优先用 LLM 生成主问题计划，失败时回退到规则模板。"""

        skill = state["skill"]
        variables = {
            "skill_name": skill["display_name"],
            "skill_description": skill["description"],
            "language": state["language"],
            "max_rounds": state["max_rounds"],
            "skill_markdown": self._prompt_runner.wrap_untrusted_text(
                "skill_markdown",
                skill["content_markdown"],
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
                )
                for item in output.questions
                if item.question_text.strip()
            ]
            if normalized_questions:
                return normalized_questions
        except Exception as exc:
            logger.warning(
                "主问题计划触发规则兜底: fallback_applied=true, fallback_type=rule, stage=planner, error={}",
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
    ) -> list[_PlannedQuestionItem]:
        """根据 Skill 分类生成可预测的降级主问题计划。"""

        category_pool = categories or [
            {"key": "GENERAL", "label": "通用问答", "priority": "NORMAL"}
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
            label = str(category.get("label", "通用问答"))
            key = str(category.get("key", "GENERAL"))
            questions.append(
                _PlannedQuestionItem(
                    category_key=key,
                    question_text=self._build_fallback_question_text(
                        display_name=display_name,
                        category_key=key,
                        category_label=label,
                        round_index=round_index,
                    ),
                )
            )
        return questions

    def _build_fallback_question_text(
        self,
        *,
        display_name: str,
        category_key: str,
        category_label: str,
        round_index: int,
    ) -> str:
        """按分类生成更自然的降级主问题。"""

        if "PROJECT" in category_key:
            return f"第 {round_index} 题：请挑一个最能代表你 {display_name} 能力的项目，说明背景、职责、技术方案与结果。"
        if "SYSTEM_DESIGN" in category_key:
            return f"第 {round_index} 题：请设计一个你熟悉的 {category_label} 场景，并说明核心架构、容量预估与关键权衡。"
        if "DEPLOY" in category_key:
            return f"第 {round_index} 题：请结合 {display_name} 场景，说明服务从开发到部署上线的完整流程，以及如何处理回滚与故障。"
        return f"第 {round_index} 题：请围绕 {category_label}，介绍你在 {display_name} 中的理解、常见实践与避坑经验。"

    @staticmethod
    def _priority_rank(priority: str) -> int:
        """把 Skill priority 映射成稳定排序权重。"""

        priority_mapping = {
            "ALWAYS_ONE": 0,
            "CORE": 1,
            "NORMAL": 2,
        }
        return priority_mapping.get(priority.upper(), 3)
