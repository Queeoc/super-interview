"""Interview executor that asks questions according to the plan."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from loguru import logger
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.models.interview import (
    InterviewQuestionSnapshot,
    InterviewQuestionSource,
    InterviewQuestionStatus,
    InterviewWorkflowAction,
)
from app.tools.github_repo_evidence_tool import (
    GitHubRepoEvidenceItem,
    GitHubRepoEvidenceToolResult,
    github_repo_evidence_tool,
)
from app.tools.knowledge_evidence_tool import (
    KnowledgeEvidenceDocument,
    KnowledgeEvidenceItem,
    KnowledgeEvidenceToolResult,
    knowledge_evidence_tool,
)
from app.tools.resume_evidence_tool import ResumeEvidenceToolResult, resume_evidence_tool

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
    get_main_questions,
    hydrate_plan_state,
    list_questions,
    mark_question_asked,
    resolve_main_question_key,
    upsert_question,
)

_GITHUB_URL_PATTERN = re.compile(
    r"(https?://)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
    re.IGNORECASE,
)
_CAMEL_CASE_TOKEN_PATTERN = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:[A-Z][a-z0-9]+)[A-Za-z0-9]*\b")
_SNAKE_CASE_TOKEN_PATTERN = re.compile(r"\b[a-z][a-z0-9]+(?:_[a-z0-9]+)+\b")
_GITHUB_CODE_HINT_TERMS = (
    "api",
    "cache",
    "class",
    "config",
    "consumer",
    "controller",
    "dto",
    "entity",
    "function",
    "interface",
    "method",
    "producer",
    "repository",
    "service",
    "stream",
    "transaction",
    "worker",
    "websocket",
)
_GITHUB_CHINESE_CODE_HINTS = (
    "代码",
    "类",
    "方法",
    "接口",
    "配置",
    "模块",
    "组件",
    "源码",
)


class _FollowUpOutput(BaseModel):
    """Structured follow-up generation output."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    question_text: str = Field(
        ...,
        validation_alias=AliasChoices("question_text", "follow_up_question"),
        min_length=1,
        description="Follow-up question text",
    )


class _CompletionOutput(BaseModel):
    """Structured closing message output."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    closing_message: str = Field(..., min_length=1, description="Interview closing message")


class _ToolDecisionOutput(BaseModel):
    """Structured follow-up tool decision output."""

    model_config = ConfigDict(extra="forbid")

    should_call_tool: bool = Field(default=False, description="是否需要在追问前调用工具")
    tool_name: str | None = Field(default=None, description="工具名称")
    arguments: dict[str, Any] = Field(default_factory=dict, description="工具参数")
    reason: str = Field(default="", description="调用或跳过工具的原因")


class InterviewExecutor:
    """Advance the workflow by asking the initial, follow-up, or next question."""

    def __init__(self, prompt_runner: InterviewPromptRunner) -> None:
        self._prompt_runner = prompt_runner

    async def run(self, state: InterviewState) -> InterviewState:
        """Execute one transition in the interview workflow."""

        working_state = clone_state(state)
        hydrate_plan_state(working_state)
        action = working_state.get("next_action", InterviewWorkflowAction.INITIAL_ASK.value)

        if action == InterviewWorkflowAction.INITIAL_ASK.value:
            return self._ask_initial_question(working_state)
        if action == InterviewWorkflowAction.FOLLOW_UP.value:
            return await self._ask_follow_up_question(working_state)
        if action == InterviewWorkflowAction.NEXT_QUESTION.value:
            return await self._ask_next_main_question(working_state)
        return await self._complete_interview(working_state)

    def _ask_initial_question(self, state: InterviewState) -> InterviewState:
        """Ask the first planned main question."""

        first_question = get_main_questions(state)[0] if get_main_questions(state) else None
        if first_question is None:
            state["next_action"] = InterviewWorkflowAction.COMPLETE.value
            state["action_reason"] = "No main questions were planned, so the interview ends immediately."
            state["completed"] = True
            return state

        asked_question = mark_question_asked(state, first_question["question_key"])
        state["current_question_key"] = first_question["question_key"]
        state["current_round"] = int(first_question["round_index"])
        state["current_main_question_key"] = str(first_question["question_key"])
        state["assistant_message"] = (
            asked_question["question_text"] if asked_question else first_question["question_text"]
        )
        hydrate_plan_state(state)
        return state

    async def _ask_follow_up_question(self, state: InterviewState) -> InterviewState:
        """Generate a targeted follow-up for the current main question."""

        current_question = get_current_question(state)
        blueprint = get_current_blueprint(state)
        skill = state["skill"]
        if current_question is None or blueprint is None:
            state["next_action"] = InterviewWorkflowAction.COMPLETE.value
            state["action_reason"] = "The current main-question context is missing, so follow-up cannot continue."
            state["completed"] = True
            return state

        main_question_key = resolve_main_question_key(state, question=current_question) or current_question["question_key"]
        round_index = int(current_question["round_index"])
        follow_up_index = get_follow_up_count_for_round(state, round_index) + 1
        follow_up_key = f"q-{round_index}-f-{follow_up_index}"
        coverage = get_main_question_coverage(state, main_question_key) or {}
        observation = get_latest_observation(
            state,
            question_key=current_question["question_key"],
            main_question_key=main_question_key,
        ) or {}
        latest_tool_context = await self._prepare_follow_up_tool_context(
            state=state,
            current_question=current_question,
            blueprint=blueprint,
            coverage=coverage,
            observation=observation,
        )
        state["latest_tool_context"] = latest_tool_context

        follow_up_text = await self._generate_follow_up_text(
            skill=skill,
            current_question=current_question,
            blueprint=blueprint,
            coverage=coverage,
            observation=observation,
            answer_text=state.get("latest_answer_text", ""),
            resume_markdown=str(state.get("resume_markdown", "")),
            resume_metadata=state.get("resume_metadata", {}),
            evidence_context=self._build_follow_up_evidence_context(latest_tool_context),
            previous_follow_up_questions=self._build_previous_follow_up_questions(
                state,
                main_question_key=main_question_key,
            ),
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

        coverage_status = dict(state.get("coverage_status", {}))
        if main_question_key in coverage_status:
            coverage_status[main_question_key]["follow_up_count"] = follow_up_index
        state["coverage_status"] = coverage_status
        state["current_question_key"] = follow_up_key
        state["current_round"] = round_index
        state["follow_up_count"] = follow_up_index
        state["current_main_question_key"] = main_question_key
        state["assistant_message"] = follow_up_text
        hydrate_plan_state(state)
        return state

    async def _ask_next_main_question(self, state: InterviewState) -> InterviewState:
        """Move to the next main question following the global plan."""

        latest_decision = dict(state.get("termination_decision_context", {}).get("latest_decision", {}))
        preferred_main_question_key = latest_decision.get("next_main_question_key")
        next_question = choose_next_main_question(state, preferred_main_question_key=preferred_main_question_key)
        if next_question is None:
            state["next_action"] = InterviewWorkflowAction.COMPLETE.value
            state["action_reason"] = "No remaining main questions are available, so the interview will end."
            return await self._complete_interview(state)

        asked_question = mark_question_asked(state, next_question["question_key"])
        state["current_question_key"] = next_question["question_key"]
        state["current_round"] = int(next_question["round_index"])
        state["follow_up_count"] = 0
        state["current_main_question_key"] = str(next_question["question_key"])
        state["assistant_message"] = (
            asked_question["question_text"] if asked_question else next_question["question_text"]
        )
        hydrate_plan_state(state)
        return state

    async def _complete_interview(self, state: InterviewState) -> InterviewState:
        """Generate the final closing message."""

        completion = await self._generate_completion_payload(state)
        state["completed"] = True
        state["current_question_key"] = None
        state["assistant_message"] = completion.closing_message
        state["completion_message"] = completion.closing_message
        state["report_summary"] = {}
        hydrate_plan_state(state)
        return state

    async def _generate_follow_up_text(
        self,
        *,
        skill: dict[str, Any],
        current_question: dict[str, Any],
        blueprint: dict[str, Any] | None = None,
        coverage: dict[str, Any] | None = None,
        observation: dict[str, Any] | None = None,
        answer_text: str,
        resume_markdown: str | None = None,
        resume_metadata: dict[str, Any] | None = None,
        evidence_context: str = "",
        previous_follow_up_questions: str = "",
    ) -> str:
        """Prefer LLM follow-up generation and fall back to deterministic prompts."""

        resume_markdown_text = (
            resume_markdown if resume_markdown is not None else str(skill.get("resume_markdown", ""))
        )
        resume_metadata_value = (
            resume_metadata if resume_metadata is not None else skill.get("resume_metadata", {})
        )
        blueprint = blueprint or {}
        coverage = coverage or {}
        observation = observation or {}
        variables = {
            "skill_markdown": self._prompt_runner.wrap_untrusted_text(
                "skill_markdown",
                skill["content_markdown"],
            ),
            "resume_markdown": self._prompt_runner.wrap_untrusted_text(
                "resume_markdown",
                str(resume_markdown_text),
            ),
            "resume_metadata": self._prompt_runner.wrap_untrusted_text(
                "resume_metadata",
                str(resume_metadata_value),
            ),
            "reference_markdown": self._prompt_runner.wrap_untrusted_text(
                "reference_markdown",
                skill["reference_markdown"],
            ),
            "current_question": self._prompt_runner.wrap_untrusted_text(
                "current_question",
                current_question["question_text"],
            ),
            "question_intent": self._prompt_runner.wrap_untrusted_text(
                "question_intent",
                str(blueprint.get("intent", "")),
            ),
            "follow_up_focus": self._prompt_runner.wrap_untrusted_text(
                "follow_up_focus",
                "\n".join(f"- {item}" for item in blueprint.get("follow_up_focus", [])),
            ),
            "missing_signals": self._prompt_runner.wrap_untrusted_text(
                "missing_signals",
                "\n".join(f"- {item}" for item in coverage.get("missing_signals", [])),
            ),
            "latest_observation": self._prompt_runner.wrap_untrusted_text(
                "latest_observation",
                str(observation),
            ),
            "follow_up_evidence_context": self._prompt_runner.wrap_untrusted_text(
                "follow_up_evidence_context",
                evidence_context,
            ),
            "previous_follow_up_questions": self._prompt_runner.wrap_untrusted_text(
                "previous_follow_up_questions",
                previous_follow_up_questions or "- no previous follow-up questions",
            ),
            "resume_evidence_context": self._prompt_runner.wrap_untrusted_text(
                "resume_evidence_context",
                evidence_context.replace("无可用追问证据", "无可用简历证据"),
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
                "executor follow-up fallback applied=true, stage=executor_follow_up, error={}",
                exc,
            )

        missing_signals = list(coverage.get("missing_signals", []))
        follow_up_focus = list(blueprint.get("follow_up_focus", []))
        focus_hint = ", ".join(missing_signals[:2] or follow_up_focus[:2] or ["implementation details"])
        return (
            "请继续补充说明你刚才的回答。"
            f"这次请重点展开 {focus_hint}，讲清楚你具体做了什么、为什么这样做，以及最后带来了什么结果或经验。"
        )

    def _build_previous_follow_up_questions(self, state: InterviewState, *, main_question_key: str) -> str:
        """Build a compact list of previous follow-up questions for the current main question."""

        lines: list[str] = []
        for question in list_questions(state):
            if not bool(question.get("is_follow_up")):
                continue
            if str(question.get("parent_question_key") or "") != main_question_key:
                continue
            question_key = str(question.get("question_key") or "").strip()
            question_text = str(question.get("question_text") or "").strip()
            if not question_key or not question_text:
                continue
            lines.append(f"- {question_key}: {question_text}")
        return "\n".join(lines) if lines else "- no previous follow-up questions"

    async def _prepare_follow_up_tool_context(
        self,
        *,
        state: InterviewState,
        current_question: dict[str, Any],
        blueprint: dict[str, Any],
        coverage: dict[str, Any],
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        """Prepare local evidence context before generating the next follow-up."""

        resume_markdown = str(state.get("resume_markdown", "") or "")
        resume_metadata = state.get("resume_metadata", {})
        answer_text = str(state.get("latest_answer_text", ""))
        skill_id = str((state.get("skill") or {}).get("skill_id", "") or "").strip()
        follow_up_count = int(state.get("follow_up_count", 0) or 0)
        if not resume_markdown.strip():
            return {
                "tool_name": None,
                "tool_source": "local_tool",
                "decision_reason": "resume_missing",
                "arguments": {},
                "success": False,
                "result": {},
                "error_message": None,
            }

        decision = await self._decide_follow_up_tool(
            current_question=current_question,
            blueprint=blueprint,
            coverage=coverage,
            observation=observation,
            answer_text=answer_text,
            resume_markdown=resume_markdown,
            resume_metadata=resume_metadata,
            skill_id=skill_id,
        )
        should_prioritize_github = (
            follow_up_count == 0
            and self._has_github_repo_link(resume_markdown)
            and self._should_prefer_github_tool(
                question_text=str(current_question.get("question_text", "")),
                question_intent=str(blueprint.get("intent", "")),
                focus_topics=[str(item).strip() for item in blueprint.get("follow_up_focus", []) if str(item).strip()],
                missing_signals=[str(item).strip() for item in coverage.get("missing_signals", []) if str(item).strip()],
            )
        )
        observation_search_keywords = self._extract_observation_search_keywords(observation)
        if (
            should_prioritize_github
            and decision.tool_name != "github_repo_evidence_tool"
            and observation_search_keywords
        ):
            original_tool_name = decision.tool_name
            decision = _ToolDecisionOutput(
                should_call_tool=True,
                tool_name="github_repo_evidence_tool",
                arguments=self._build_github_evidence_tool_arguments(
                    current_question=current_question,
                    blueprint=blueprint,
                    coverage=coverage,
                    answer_text=answer_text,
                    top_k=2,
                    keyword_candidates=observation_search_keywords,
                ),
                reason="forced_first_github_follow_up",
            )
            logger.info(
                "follow-up tool decision overridden session_id={}, original_tool_name={}, github_context_available=yes, reason=first_project_follow_up",
                state.get("session_id"),
                original_tool_name,
            )

        supported_tools = {
            "resume_evidence_tool",
            "github_repo_evidence_tool",
            "knowledge_evidence_tool",
        }
        if decision.should_call_tool and decision.tool_name in supported_tools:
            decision = decision.model_copy(
                update={
                    "arguments": self._normalize_evidence_tool_arguments(
                        tool_name=decision.tool_name,
                        arguments=decision.arguments,
                        current_question=current_question,
                        blueprint=blueprint,
                        coverage=coverage,
                        answer_text=answer_text,
                        skill_id=skill_id,
                    )
                }
            )
        logger.info(
            "follow-up tool preparation session_id={}, selected_tool={}, should_call_tool={}, follow_up_count={}, github_context_available={}",
            state.get("session_id"),
            decision.tool_name,
            decision.should_call_tool,
            follow_up_count,
            self._has_github_repo_link(resume_markdown),
        )
        if not decision.should_call_tool or decision.tool_name not in supported_tools:
            return {
                "tool_name": decision.tool_name,
                "tool_source": "local_tool",
                "decision_reason": decision.reason or "tool_skipped",
                "arguments": deepcopy(decision.arguments),
                "success": False,
                "result": {},
                "error_message": None,
            }

        tool_context = await self._invoke_interview_tool(
            tool_name=decision.tool_name,
            arguments=decision.arguments,
            decision_reason=decision.reason,
            resume_markdown=resume_markdown,
            resume_metadata=resume_metadata,
            skill_id=skill_id,
        )
        if decision.tool_name != "github_repo_evidence_tool" or tool_context.get("success"):
            return tool_context

        fallback_arguments = self._build_resume_evidence_tool_arguments(
            current_question=current_question,
            blueprint=blueprint,
            coverage=coverage,
            answer_text=answer_text,
            top_k=3,
        )
        fallback_context = await self._invoke_interview_tool(
            tool_name="resume_evidence_tool",
            arguments=fallback_arguments,
            decision_reason="github_tool_fallback_to_resume",
            resume_markdown=resume_markdown,
            resume_metadata=resume_metadata,
            skill_id=skill_id,
        )
        fallback_reason = (
            tool_context.get("error_message")
            or tool_context.get("decision_reason")
            or tool_context.get("result", {}).get("retrieval_reason")
            or "github_tool_unavailable"
        )
        fallback_context["fallback_from_tool"] = "github_repo_evidence_tool"
        fallback_context["fallback_reason"] = fallback_reason
        fallback_context["upstream_tool_context"] = tool_context
        if fallback_context.get("error_message"):
            fallback_context["error_message"] = (
                f"github_repo_evidence_tool failed: {fallback_reason}; "
                f"resume fallback also failed: {fallback_context['error_message']}"
            )
        else:
            fallback_context["error_message"] = f"github_repo_evidence_tool failed: {fallback_reason}"
        return fallback_context

    async def _decide_follow_up_tool(
        self,
        *,
        current_question: dict[str, Any],
        blueprint: dict[str, Any],
        coverage: dict[str, Any],
        observation: dict[str, Any],
        answer_text: str,
        resume_markdown: str,
        resume_metadata: dict[str, Any],
        skill_id: str,
    ) -> _ToolDecisionOutput:
        """Decide whether the follow-up stage should call a local evidence tool."""

        github_context_available = self._has_github_repo_link(resume_markdown)
        variables = {
            "current_question": self._prompt_runner.wrap_untrusted_text(
                "current_question",
                str(current_question.get("question_text", "")),
            ),
            "category_key": self._prompt_runner.wrap_untrusted_text(
                "category_key",
                str(current_question.get("category_key", "")),
            ),
            "question_intent": self._prompt_runner.wrap_untrusted_text(
                "question_intent",
                str(blueprint.get("intent", "")),
            ),
            "follow_up_focus": self._prompt_runner.wrap_untrusted_text(
                "follow_up_focus",
                "\n".join(f"- {item}" for item in blueprint.get("follow_up_focus", [])),
            ),
            "missing_signals": self._prompt_runner.wrap_untrusted_text(
                "missing_signals",
                "\n".join(f"- {item}" for item in coverage.get("missing_signals", [])),
            ),
            "latest_observation": self._prompt_runner.wrap_untrusted_text(
                "latest_observation",
                str(observation),
            ),
            "user_answer": self._prompt_runner.wrap_untrusted_text("user_answer", answer_text),
            "resume_markdown": self._prompt_runner.wrap_untrusted_text("resume_markdown", resume_markdown[:4000]),
            "resume_metadata": self._prompt_runner.wrap_untrusted_text(
                "resume_metadata",
                str(resume_metadata),
            ),
            "skill_id": self._prompt_runner.wrap_untrusted_text("skill_id", skill_id),
            "github_context_available": self._prompt_runner.wrap_untrusted_text(
                "github_context_available",
                "yes" if github_context_available else "no",
            ),
        }
        try:
            decision = await self._prompt_runner.ainvoke_structured(
                template_name="tool_decision.st",
                schema=_ToolDecisionOutput,
                variables=variables,
                temperature=0.1,
            )
            if decision.tool_name and decision.tool_name not in {
                "resume_evidence_tool",
                "github_repo_evidence_tool",
                "knowledge_evidence_tool",
            }:
                return _ToolDecisionOutput(
                    should_call_tool=False,
                    tool_name=decision.tool_name,
                    arguments={},
                    reason="unsupported_tool",
                )
            if decision.tool_name == "github_repo_evidence_tool" and not github_context_available:
                return _ToolDecisionOutput(
                    should_call_tool=False,
                    tool_name="github_repo_evidence_tool",
                    arguments={},
                    reason="github_context_unavailable",
                )
            logger.info(
                "follow-up tool decision completed tool_name={}, should_call_tool={}, github_context_available={}, reason={}",
                decision.tool_name,
                decision.should_call_tool,
                github_context_available,
                decision.reason,
            )
            return decision
        except Exception as exc:
            logger.warning(
                "executor tool decision fallback applied=true, stage=tool_decision, error={}",
                exc,
            )
            return self._fallback_tool_decision(
                current_question=current_question,
                blueprint=blueprint,
                coverage=coverage,
                observation=observation,
                answer_text=answer_text,
                resume_markdown=resume_markdown,
                skill_id=skill_id,
            )

    def _fallback_tool_decision(
        self,
        *,
        current_question: dict[str, Any],
        blueprint: dict[str, Any],
        coverage: dict[str, Any],
        observation: dict[str, Any],
        answer_text: str,
        resume_markdown: str,
        skill_id: str,
    ) -> _ToolDecisionOutput:
        """Use stable rules when LLM tool decision falls back."""

        missing_signals = [str(item).strip() for item in coverage.get("missing_signals", []) if str(item).strip()]
        answer_too_short = len(answer_text.strip()) < 80
        should_call_tool = bool(missing_signals) or answer_too_short
        tool_name: str | None = None
        github_search_keywords = self._extract_observation_search_keywords(observation)
        if should_call_tool:
            if (
                github_search_keywords
                and self._has_github_repo_link(resume_markdown)
                and self._should_prefer_github_tool(
                    question_text=str(current_question.get("question_text", "")),
                    question_intent=str(blueprint.get("intent", "")),
                    focus_topics=[str(item).strip() for item in blueprint.get("follow_up_focus", []) if str(item).strip()],
                    missing_signals=missing_signals,
                )
            ):
                tool_name = "github_repo_evidence_tool"
            elif self._should_prefer_knowledge_tool(
                question_text=str(current_question.get("question_text", "")),
                question_intent=str(blueprint.get("intent", "")),
                focus_topics=[str(item).strip() for item in blueprint.get("follow_up_focus", []) if str(item).strip()],
                missing_signals=missing_signals,
                answer_text=answer_text,
            ):
                tool_name = "knowledge_evidence_tool"
            else:
                tool_name = "resume_evidence_tool"
        logger.info(
            "follow-up tool fallback decision tool_name={}, should_call_tool={}, missing_signals={}, answer_too_short={}, github_context_available={}",
            tool_name,
            should_call_tool,
            missing_signals[:4],
            answer_too_short,
            self._has_github_repo_link(resume_markdown),
        )
        return _ToolDecisionOutput(
            should_call_tool=should_call_tool,
            tool_name=tool_name,
            arguments=(
                self._build_github_evidence_tool_arguments(
                    current_question=current_question,
                    blueprint=blueprint,
                    coverage=coverage,
                    answer_text=answer_text,
                    top_k=2,
                    keyword_candidates=github_search_keywords,
                )
                if should_call_tool and tool_name == "github_repo_evidence_tool"
                else self._build_knowledge_evidence_tool_arguments(
                    current_question=current_question,
                    blueprint=blueprint,
                    coverage=coverage,
                    answer_text=answer_text,
                    skill_id=skill_id,
                    top_k=3,
                )
                if should_call_tool and tool_name == "knowledge_evidence_tool"
                else self._build_resume_evidence_tool_arguments(
                    current_question=current_question,
                    blueprint=blueprint,
                    coverage=coverage,
                    answer_text=answer_text,
                    top_k=3,
                )
                if should_call_tool
                else {}
            ),
            reason="fallback_rule_based_decision" if should_call_tool else "fallback_skip",
        )

    async def _invoke_interview_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        decision_reason: str,
        resume_markdown: str,
        resume_metadata: dict[str, Any],
        skill_id: str = "",
    ) -> dict[str, Any]:
        """Execute the chosen follow-up evidence tool and normalize the result."""

        merged_arguments = {
            **deepcopy(arguments),
            "resume_markdown": resume_markdown,
            "resume_metadata": deepcopy(resume_metadata),
        }
        if tool_name == "knowledge_evidence_tool":
            merged_arguments["skill_id"] = str(merged_arguments.get("skill_id") or skill_id).strip()
        try:
            if tool_name == "resume_evidence_tool":
                result = resume_evidence_tool(**merged_arguments)
                logger.info(
                    "interview tool executed session_phase=follow_up, tool_name={}, found={}, retrieval_reason={}, top_sections={}",
                    tool_name,
                    result.found,
                    result.retrieval_reason,
                    [item.source_section for item in result.items[:3]],
                )
            elif tool_name == "github_repo_evidence_tool":
                result = await github_repo_evidence_tool(**merged_arguments)
                logger.info(
                    "interview tool executed session_phase=follow_up, tool_name={}, found={}, retrieval_reason={}, matched_files={}",
                    tool_name,
                    result.found,
                    result.retrieval_reason,
                    result.matched_files[:4],
                )
            elif tool_name == "knowledge_evidence_tool":
                result = await knowledge_evidence_tool(**merged_arguments)
                logger.info(
                    "interview tool executed session_phase=follow_up, tool_name={}, found={}, retrieval_reason={}, matched_documents={}",
                    tool_name,
                    result.found,
                    result.retrieval_reason,
                    [item.file_name for item in result.matched_documents[:4]],
                )
            else:
                raise ValueError(f"unsupported tool: {tool_name}")
            return {
                "tool_name": tool_name,
                "tool_source": "local_tool",
                "decision_reason": decision_reason,
                "arguments": merged_arguments,
                "success": bool(result.found),
                "result": result.model_dump(mode="json"),
                "error_message": None,
            }
        except Exception as exc:
            logger.warning(
                "interview tool failed session_phase=follow_up, tool_name={}, error={}",
                tool_name,
                exc,
            )
            return {
                "tool_name": tool_name,
                "tool_source": "local_tool",
                "decision_reason": decision_reason,
                "arguments": merged_arguments,
                "success": False,
                "result": {},
                "error_message": str(exc),
            }

    def _build_common_evidence_tool_arguments(
        self,
        *,
        current_question: dict[str, Any],
        blueprint: dict[str, Any],
        coverage: dict[str, Any],
        answer_text: str,
    ) -> dict[str, Any]:
        """构建两类证据工具共用的基础参数。"""

        missing_signals = self._limit_string_list(coverage.get("missing_signals", []), limit=4)
        focus_topics = self._limit_string_list(blueprint.get("follow_up_focus", []), limit=4)
        return {
            "category_key": current_question.get("category_key", "GENERAL"),
            "question_text": current_question.get("question_text", ""),
            "answer_text": answer_text,
            "focus_topics": focus_topics,
            "missing_signals": missing_signals,
        }

    def _build_resume_evidence_tool_arguments(
        self,
        *,
        current_question: dict[str, Any],
        blueprint: dict[str, Any],
        coverage: dict[str, Any],
        answer_text: str,
        top_k: int,
    ) -> dict[str, Any]:
        """构建简历证据工具参数。"""

        arguments = self._build_common_evidence_tool_arguments(
            current_question=current_question,
            blueprint=blueprint,
            coverage=coverage,
            answer_text=answer_text,
        )
        arguments["top_k"] = max(1, int(top_k or 1))
        return arguments

    def _build_github_evidence_tool_arguments(
        self,
        *,
        current_question: dict[str, Any],
        blueprint: dict[str, Any],
        coverage: dict[str, Any],
        answer_text: str,
        top_k: int,
        keyword_candidates: list[str] | None = None,
    ) -> dict[str, Any]:
        """构建 GitHub 代码证据工具参数。"""

        arguments = self._build_common_evidence_tool_arguments(
            current_question=current_question,
            blueprint=blueprint,
            coverage=coverage,
            answer_text=answer_text,
        )
        search_keywords = self._normalize_github_search_keywords(keyword_candidates or [])
        arguments["search_keywords"] = search_keywords
        arguments["keywords"] = list(search_keywords)
        arguments["top_k"] = self._clamp_github_top_k(top_k)
        return arguments

    def _normalize_evidence_tool_arguments(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any] | None,
        current_question: dict[str, Any],
        blueprint: dict[str, Any],
        coverage: dict[str, Any],
        answer_text: str,
        skill_id: str,
    ) -> dict[str, Any]:
        """对工具参数做兜底归一化，避免 LLM 传入不稳定结构。"""

        raw_arguments = arguments if isinstance(arguments, dict) else {}
        common_arguments = self._build_common_evidence_tool_arguments(
            current_question=current_question,
            blueprint=blueprint,
            coverage=coverage,
            answer_text=answer_text,
        )
        common_arguments["focus_topics"] = self._limit_string_list(
            raw_arguments.get("focus_topics", common_arguments["focus_topics"]),
            limit=4,
            fallback=common_arguments["focus_topics"],
        )
        common_arguments["missing_signals"] = self._limit_string_list(
            raw_arguments.get("missing_signals", common_arguments["missing_signals"]),
            limit=4,
            fallback=common_arguments["missing_signals"],
        )
        if tool_name == "github_repo_evidence_tool":
            search_keywords = self._normalize_github_search_keywords(
                raw_arguments.get("search_keywords", raw_arguments.get("keywords", []))
            )
            return {
                **common_arguments,
                "search_keywords": search_keywords,
                "keywords": list(search_keywords),
                "top_k": self._clamp_github_top_k(raw_arguments.get("top_k", 2)),
            }
        if tool_name == "knowledge_evidence_tool":
            return {
                **common_arguments,
                "skill_id": str(raw_arguments.get("skill_id", skill_id) or skill_id).strip(),
                "include_global_skill": bool(raw_arguments.get("include_global_skill", True)),
                "top_k": max(1, min(5, int(raw_arguments.get("top_k", 3) or 3))),
            }
        return {
            **common_arguments,
            "top_k": max(1, int(raw_arguments.get("top_k", 3) or 3)),
        }

    def _build_knowledge_evidence_tool_arguments(
        self,
        *,
        current_question: dict[str, Any],
        blueprint: dict[str, Any],
        coverage: dict[str, Any],
        answer_text: str,
        skill_id: str,
        top_k: int,
    ) -> dict[str, Any]:
        """构建知识库追问证据工具参数。"""

        arguments = self._build_common_evidence_tool_arguments(
            current_question=current_question,
            blueprint=blueprint,
            coverage=coverage,
            answer_text=answer_text,
        )
        arguments["skill_id"] = skill_id
        arguments["include_global_skill"] = True
        arguments["top_k"] = max(1, min(5, int(top_k or 3)))
        return arguments

    def _limit_string_list(
        self,
        values: Any,
        *,
        limit: int,
        fallback: list[str] | None = None,
    ) -> list[str]:
        """把任意输入归一化为去重后的短字符串列表。"""

        if isinstance(values, str):
            iterable = [values]
        elif isinstance(values, (list, tuple, set)):
            iterable = list(values)
        else:
            iterable = list(fallback or [])

        normalized: list[str] = []
        seen: set[str] = set()
        for item in iterable:
            text = str(item).strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(text)
            if len(normalized) >= limit:
                break
        return normalized

    def _normalize_github_search_keywords(self, values: Any) -> list[str]:
        """仅保留 LLM 显式提供的 GitHub 检索词，不再从其他字段回退补词。"""

        return self._limit_string_list(values, limit=4)

    def _extract_observation_search_keywords(self, observation: dict[str, Any]) -> list[str]:
        """从观察结果中提取显式 search_keywords。"""

        if not isinstance(observation, dict):
            return []
        return self._normalize_github_search_keywords(
            observation.get("search_keywords", observation.get("keywords", []))
        )

    def _clamp_github_top_k(self, value: Any) -> int:
        """把 GitHub 代码片段返回条数收敛到 1..2。"""

        try:
            numeric_value = int(value)
        except (TypeError, ValueError):
            return 2
        return max(1, min(2, numeric_value))

    def _has_github_repo_link(self, resume_markdown: str) -> bool:
        """Return whether the resume contains at least one GitHub repository link."""

        return bool(_GITHUB_URL_PATTERN.search(resume_markdown or ""))

    def _should_prefer_github_tool(
        self,
        *,
        question_text: str,
        question_intent: str,
        focus_topics: list[str],
        missing_signals: list[str],
    ) -> bool:
        """Heuristically decide whether GitHub repo evidence is a better follow-up anchor."""

        raw_text = "\n".join([question_text, question_intent, *focus_topics, *missing_signals])
        combined_text = raw_text.lower()
        github_terms = (
            "github",
            "repo",
            "repository",
            "readme",
            "仓库",
            "项目",
            "工程",
            "实现",
            "维护",
            "目录",
            "结构",
            "模块",
            "技术栈",
            "design",
            "implement",
            "implementation",
            "maintain",
            "maintenance",
            "architecture",
            "structure",
        )
        if any(term in combined_text for term in github_terms):
            return True
        if any(term in combined_text for term in _GITHUB_CODE_HINT_TERMS if len(term) >= 4):
            return True
        if _CAMEL_CASE_TOKEN_PATTERN.search(raw_text) or _SNAKE_CASE_TOKEN_PATTERN.search(raw_text):
            return True
        return any(hint in raw_text for hint in _GITHUB_CHINESE_CODE_HINTS)

    def _should_prefer_knowledge_tool(
        self,
        *,
        question_text: str,
        question_intent: str,
        focus_topics: list[str],
        missing_signals: list[str],
        answer_text: str,
    ) -> bool:
        """判断当前追问是否更需要稳定的技术知识证据。"""

        raw_text = "\n".join([question_text, question_intent, *focus_topics, *missing_signals, answer_text])
        combined_text = raw_text.lower()
        knowledge_terms = (
            "原理",
            "机制",
            "边界",
            "权衡",
            "复杂度",
            "复杂性",
            "实现",
            "流程",
            "常见坑",
            "坑",
            "并发",
            "锁",
            "事务",
            "一致性",
            "幂等",
            "重试",
            "降级",
            "容错",
            "调优",
            "缓存",
            "索引",
            "消息队列",
            "线程",
            "协程",
            "网络",
            "协议",
            "算法",
            "架构",
            "principle",
            "mechanism",
            "boundary",
            "tradeoff",
            "complexity",
            "implementation",
            "concurrency",
            "consistency",
            "idempotent",
            "retry",
            "fallback",
            "cache",
            "index",
            "thread",
            "lock",
            "architecture",
        )
        return any(term in combined_text for term in knowledge_terms)

    def _build_follow_up_evidence_context(self, latest_tool_context: dict[str, Any] | None) -> str:
        """Convert the latest tool result into stable follow-up prompt context."""

        if not latest_tool_context:
            return "无可用追问证据。"

        tool_name = latest_tool_context.get("tool_name")
        result_payload = latest_tool_context.get("result", {})
        if not latest_tool_context.get("success") or not isinstance(result_payload, dict):
            reason = (
                latest_tool_context.get("error_message")
                or (result_payload.get("retrieval_reason") if isinstance(result_payload, dict) else None)
                or latest_tool_context.get("decision_reason")
                or "无命中"
            )
            return f"无可用追问证据。原因：{reason}"

        if tool_name == "resume_evidence_tool":
            return self._format_resume_evidence_context(latest_tool_context, result_payload)
        if tool_name == "github_repo_evidence_tool":
            return self._format_github_code_evidence_context(result_payload)
        if tool_name == "knowledge_evidence_tool":
            return self._format_knowledge_evidence_context(result_payload)
        return "无可用追问证据。"

    def _format_resume_evidence_context(
        self,
        latest_tool_context: dict[str, Any],
        result_payload: dict[str, Any],
    ) -> str:
        """Format resume evidence into a prompt-friendly context block."""

        try:
            result = ResumeEvidenceToolResult.model_validate(result_payload)
        except Exception:
            return "无可用追问证据。"
        if not result.found or not result.items:
            return f"无可用追问证据。原因：{result.retrieval_reason or '无命中'}"

        lines: list[str] = []
        if latest_tool_context.get("fallback_from_tool") == "github_repo_evidence_tool":
            fallback_reason = latest_tool_context.get("fallback_reason") or "github_tool_unavailable"
            lines.append(f"GitHub 补证失败，已回退到简历证据。原因：{fallback_reason}")
        lines.append(f"检索原因：{result.retrieval_reason or 'resume_evidence_matched'}")
        if result.matched_projects:
            lines.append("命中项目：" + "、".join(result.matched_projects))
        if result.matched_skills:
            lines.append("命中技能：" + "、".join(result.matched_skills))
        for index, item in enumerate(result.items, start=1):
            matched_terms = "、".join(item.matched_terms) if item.matched_terms else "无"
            lines.append(f"{index}. [{item.source_section}] 关键词：{matched_terms}；片段：{item.snippet}")
        return "\n".join(lines)

    def _format_github_evidence_context(self, result_payload: dict[str, Any]) -> str:
        """Format GitHub repo evidence into a prompt-friendly context block."""

        try:
            result = GitHubRepoEvidenceToolResult.model_validate(result_payload)
        except Exception:
            return "无可用追问证据。"
        if not result.found or not result.items:
            return f"无可用 GitHub 仓库证据。原因：{result.retrieval_reason or '无命中'}"

        prompt_items = self._select_github_items_for_prompt(result.items)
        lines = [
            f"检索原因：{result.retrieval_reason or 'github_repo_evidence_matched'}",
            f"仓库：{result.repo_name or result.repo_url}",
        ]
        binding_project = result.matched_project_title or self._summarize_project_binding(result.matched_project_excerpt)
        if binding_project:
            lines.append(f"简历绑定项目：{binding_project}")
        readme_headings = self._collect_readme_headings(prompt_items)
        if readme_headings:
            lines.append("README 命中章节：" + "、".join(readme_headings[:4]))
        matched_files = self._collect_github_matched_files(prompt_items)
        if matched_files:
            lines.append("命中文件：" + "、".join(matched_files[:4]))
        for index, item in enumerate(prompt_items, start=1):
            matched_terms = "、".join(item.matched_terms) if item.matched_terms else "无"
            label = item.heading or item.source_path
            lines.append(f"{index}. [{item.evidence_type} | {label}] 来源：{item.source_path}；关键词：{matched_terms}")
            lines.append(f"原文摘录：\n{item.raw_excerpt}")
        return "\n".join(lines)

    def _select_github_items_for_prompt(
        self,
        items: list[GitHubRepoEvidenceItem],
        *,
        max_items: int = 3,
        max_excerpt_chars: int = 1400,
    ) -> list[GitHubRepoEvidenceItem]:
        """Pick the strongest GitHub evidence cards without rewriting their excerpts."""

        selected: list[GitHubRepoEvidenceItem] = []
        total_excerpt_chars = 0
        for item in items:
            excerpt_length = len(item.raw_excerpt.strip())
            if len(selected) >= max_items:
                break
            if selected and total_excerpt_chars + excerpt_length > max_excerpt_chars:
                continue
            selected.append(item)
            total_excerpt_chars += excerpt_length

        if selected:
            return selected
        return items[:1]

    def _collect_github_matched_files(self, items: list[GitHubRepoEvidenceItem]) -> list[str]:
        """Collect unique file paths from the GitHub evidence items shown to the model."""

        files: list[str] = []
        seen: set[str] = set()
        for item in items:
            path = "README.md" if item.source_path.startswith("README.md") else item.source_path
            lowered = path.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            files.append(path)
        return files

    def _collect_readme_headings(self, items: list[GitHubRepoEvidenceItem]) -> list[str]:
        """Collect unique README headings from GitHub evidence items."""

        headings: list[str] = []
        seen: set[str] = set()
        for item in items:
            if not item.source_path.startswith("README.md"):
                continue
            heading = (item.heading or "").strip()
            if not heading or heading.lower() == "readme_intro":
                continue
            lowered = heading.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            headings.append(heading)
        return headings

    def _summarize_project_binding(self, excerpt: str) -> str:
        """Reduce a project excerpt to a short binding label."""

        normalized = " ".join(part.strip() for part in excerpt.splitlines() if part.strip())
        if not normalized:
            return ""
        return normalized[:80] + ("..." if len(normalized) > 80 else "")

    def _format_github_code_evidence_context(self, result_payload: dict[str, Any]) -> str:
        """按代码片段卡格式组织 GitHub 证据上下文。"""

        try:
            result = GitHubRepoEvidenceToolResult.model_validate(result_payload)
        except Exception:
            return "无可用追问证据。"
        if not result.found or not result.items:
            return f"无可用 GitHub 仓库证据。原因：{result.retrieval_reason or '无命中'}"

        prompt_items = self._select_github_code_items_for_prompt(result.items)
        lines: list[str] = []
        for index, item in enumerate(prompt_items, start=1):
            line_range = self._format_github_code_line_range(item)
            lines.append(f"{index}. 文件：{item.source_path}{line_range}")
            lines.append(f"代码片段：\n{self._truncate_github_code_excerpt(item.raw_excerpt)}")
        return "\n".join(lines)

    def _format_knowledge_evidence_context(self, result_payload: dict[str, Any]) -> str:
        """把知识库命中整理成追问可读的上下文块。"""

        try:
            result = KnowledgeEvidenceToolResult.model_validate(result_payload)
        except Exception:
            return "无可用追问证据。"
        if not result.found or not result.items:
            reason = result.retrieval_reason or "knowledge_search_empty"
            return f"无可用追问证据。原因：{reason}"

        prompt_items = self._select_knowledge_items_for_prompt(result.items)
        lines: list[str] = []
        if result.retrieval_reason:
            lines.append(f"检索原因：{result.retrieval_reason}")
        if result.matched_categories:
            lines.append("命中分类：" + "、".join(result.matched_categories[:4]))
        if result.matched_documents:
            lines.append(
                "命中文档：" + "、".join(item.file_name for item in result.matched_documents[:4])
            )
        for index, item in enumerate(prompt_items, start=1):
            skill_label = item.skill_id or "global"
            lines.append(
                f"{index}. [{item.category} | {item.file_name} | skill={skill_label} | score={item.score:.3f}]"
            )
            lines.append(f"摘录：{self._truncate_knowledge_excerpt(item.content)}")
        return "\n".join(lines)

    def _select_knowledge_items_for_prompt(
        self,
        items: list[KnowledgeEvidenceItem],
        *,
        max_items: int = 3,
        max_excerpt_chars: int = 900,
    ) -> list[KnowledgeEvidenceItem]:
        """保留少量知识命中用于 prompt。"""

        selected: list[KnowledgeEvidenceItem] = []
        total_excerpt_chars = 0
        for item in items:
            excerpt_length = len(item.content.strip())
            if len(selected) >= max_items:
                break
            if selected and total_excerpt_chars + excerpt_length > max_excerpt_chars:
                continue
            selected.append(item)
            total_excerpt_chars += excerpt_length
        if selected:
            return selected
        return items[:1]

    def _truncate_knowledge_excerpt(self, content: str, *, max_chars: int = 500) -> str:
        """裁剪知识库摘录，避免 prompt 过长。"""

        rendered = " ".join(part.strip() for part in content.splitlines() if part.strip()).strip()
        if len(rendered) > max_chars:
            rendered = rendered[:max_chars].rstrip()
        if len(content.strip()) > len(rendered):
            return f"{rendered}..."
        return rendered

    def _select_github_code_items_for_prompt(
        self,
        items: list[GitHubRepoEvidenceItem],
        *,
        max_items: int = 2,
    ) -> list[GitHubRepoEvidenceItem]:
        """固定保留前几张代码卡；具体片段长度在格式化阶段裁剪。"""

        return items[:max_items]

    def _truncate_github_code_excerpt(
        self,
        raw_excerpt: str,
        *,
        max_lines: int = 12,
        max_chars: int = 700,
    ) -> str:
        """裁剪展示给 follow-up prompt 的代码，不改写工具原始结果。"""

        lines = raw_excerpt.strip().splitlines()
        rendered = "\n".join(lines[:max_lines]).strip()
        if len(rendered) > max_chars:
            rendered = rendered[:max_chars].rstrip()
        if len(lines) > max_lines or len(raw_excerpt.strip()) > len(rendered):
            return f"{rendered}\n..." if rendered else "..."
        return rendered

    def _collect_github_code_matched_files(self, items: list[GitHubRepoEvidenceItem]) -> list[str]:
        """收集展示给模型的命中文件路径。"""

        files: list[str] = []
        seen: set[str] = set()
        for item in items:
            path = item.source_path.strip()
            lowered = path.lower()
            if not path or lowered in seen:
                continue
            seen.add(lowered)
            files.append(path)
        return files

    def _format_github_code_line_range(self, item: GitHubRepoEvidenceItem) -> str:
        """格式化代码片段的行号范围。"""

        if item.start_line is None and item.end_line is None:
            return ""
        if item.start_line is not None and item.end_line is not None:
            return f" ({item.start_line}-{item.end_line})"
        return f" ({item.start_line or item.end_line})"

    async def _generate_completion_payload(self, state: InterviewState) -> _CompletionOutput:
        """Prefer LLM closing generation and fall back to a stable template."""

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
        ) or "- no effective Q&A was formed in this session"
        covered_main_question_keys = list(state.get("plan_progress", {}).get("covered_main_question_keys", []))
        variables = {
            "skill_name": skill["display_name"],
            "answered_question_count": len(answered_questions),
            "question_summary": self._prompt_runner.wrap_untrusted_text(
                "question_summary",
                question_summary,
            ),
            "covered_topics": self._prompt_runner.wrap_untrusted_text(
                "covered_topics",
                "\n".join(f"- {question_key}" for question_key in covered_main_question_keys),
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
                "executor completion fallback applied=true, stage=executor_completion, error={}",
                exc,
            )

        return _CompletionOutput(
            closing_message=(
                f"The {skill['display_name']} interview ends here for now. "
                "Your answers have been saved, and a consolidated evaluation report will be generated next."
            ),
        )
