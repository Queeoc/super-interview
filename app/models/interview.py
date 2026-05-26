"""面试领域持久化实体。

本模块同时定义：
- 面试相关的 SQLAlchemy 持久化实体
- Phase 5 使用的 API DTO 与工作流结构对象
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _generate_uuid() -> str:
    """生成字符串形式的 UUID 主键。"""

    return str(uuid4())


def _utc_now() -> datetime:
    """返回带时区的当前 UTC 时间。"""

    return datetime.now(timezone.utc)


class InterviewSessionStatus(str, Enum):
    """面试会话状态。"""

    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class InterviewAnswerStatus(str, Enum):
    """面试答案状态。"""

    DRAFT = "draft"
    SUBMITTED = "submitted"
    REVIEWED = "reviewed"


class InterviewReportStatus(str, Enum):
    """面试报告状态。"""

    PENDING = "pending"
    GENERATED = "generated"
    FAILED = "failed"


class InterviewQuestionSource(str, Enum):
    """题目来源类型。"""

    PLANNED = "planned"
    FOLLOW_UP = "follow_up"


class InterviewQuestionStatus(str, Enum):
    """题目状态。"""

    PLANNED = "planned"
    ASKED = "asked"
    ANSWERED = "answered"


class InterviewWorkflowAction(str, Enum):
    """面试工作流动作。"""

    INITIAL_ASK = "initial_ask"
    FOLLOW_UP = "follow_up"
    NEXT_QUESTION = "next_question"
    COMPLETE = "complete"
    DRAFT_SAVED = "draft_saved"


class InterviewQuestionSnapshot(BaseModel):
    """工作流与 API 共享的题目快照。"""

    model_config = ConfigDict(extra="forbid")

    question_key: str = Field(..., description="题目标识，如 q-1 或 q-1-f-1")
    round_index: int = Field(..., ge=1, description="所属主问题轮次，从 1 开始")
    category_key: str = Field(..., description="Skill 分类标识")
    question_text: str = Field(..., description="题目正文")
    parent_question_key: str | None = Field(default=None, description="所属主问题标识")
    source: str = Field(
        default=InterviewQuestionSource.PLANNED.value,
        description="题目来源：planned 或 follow_up",
    )
    status: str = Field(
        default=InterviewQuestionStatus.PLANNED.value,
        description="题目状态：planned / asked / answered",
    )
    is_follow_up: bool = Field(default=False, description="是否为追问题")
    asked_at: datetime | None = Field(default=None, description="提问时间")
    answered_at: datetime | None = Field(default=None, description="回答完成时间")

    @field_validator("question_key", "category_key", "question_text")
    @classmethod
    def strip_required_strings(cls, value: str) -> str:
        """清理必填字符串字段。"""

        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("字段不能为空")
        return normalized_value


class CreateInterviewRequest(BaseModel):
    """创建文字面试会话请求。"""

    skill_id: str = Field(..., min_length=1, description="预设 Skill 标识")
    user_id: str | None = Field(default=None, description="用户标识")
    resume_id: str | None = Field(default=None, description="关联简历标识")
    title: str | None = Field(default=None, description="会话标题")
    language: str | None = Field(default=None, description="面试语言")
    max_rounds: int | None = Field(default=None, ge=1, le=20, description="最大主问题轮次")

    @field_validator("skill_id", mode="before")
    @classmethod
    def validate_skill_id(cls, value: str) -> str:
        """在进入标准校验前先清理 Skill 标识。"""

        return value.strip() if isinstance(value, str) else value


class SubmitAnswerRequest(BaseModel):
    """提交或暂存答案请求。"""

    answer_text: str = Field(..., min_length=1, description="候选人回答内容")
    question_key: str | None = Field(default=None, description="当前回答对应的题目标识")
    answer_metadata: dict[str, Any] = Field(default_factory=dict, description="附加回答元数据")

    @field_validator("answer_text", mode="before")
    @classmethod
    def validate_answer_text(cls, value: str) -> str:
        """在进入标准校验前先清理答案内容。"""

        return value.strip() if isinstance(value, str) else value


class InterviewSessionDTO(BaseModel):
    """对外暴露的面试会话快照。"""

    session_id: str = Field(..., description="会话标识")
    user_id: str | None = Field(default=None, description="用户标识")
    resume_id: str | None = Field(default=None, description="简历标识")
    skill_id: str = Field(..., description="Skill 标识")
    skill_display_name: str = Field(..., description="Skill 展示名称")
    title: str | None = Field(default=None, description="会话标题")
    language: str = Field(..., description="面试语言")
    status: str = Field(..., description="会话状态")
    current_round: int = Field(..., ge=0, description="当前主问题轮次")
    max_rounds: int = Field(..., ge=1, description="最大主问题轮次")
    current_question: InterviewQuestionSnapshot | None = Field(
        default=None,
        description="当前待回答题目",
    )
    questions: list[InterviewQuestionSnapshot] = Field(
        default_factory=list,
        description="当前会话题目快照列表",
    )
    answer_count: int = Field(default=0, ge=0, description="已提交答案数")
    follow_up_count: int = Field(default=0, ge=0, description="当前主问题追问次数")
    completed: bool = Field(default=False, description="是否已完成")
    report_status: str | None = Field(default=None, description="报告状态")
    last_draft_answer: dict[str, Any] = Field(
        default_factory=dict,
        description="最近一次暂存答案快照",
    )
    started_at: datetime | None = Field(default=None, description="开始时间")
    completed_at: datetime | None = Field(default=None, description="完成时间")


class SubmitAnswerResponse(BaseModel):
    """提交答案或暂存答案后的统一响应。"""

    session_id: str = Field(..., description="会话标识")
    action: str = Field(..., description="本次动作，如 follow_up / next_question / complete")
    status: str = Field(..., description="会话状态")
    completed: bool = Field(default=False, description="是否已完成")
    draft_saved: bool = Field(default=False, description="是否为草稿暂存结果")
    message: str = Field(..., description="响应说明")
    current_question: InterviewQuestionSnapshot | None = Field(
        default=None,
        description="下一题或当前待答题",
    )
    feedback: dict[str, Any] = Field(default_factory=dict, description="占位反馈")
    report_status: str | None = Field(default=None, description="报告状态")


class InterviewSessionEntity(Base):
    """面试会话主表。

    说明：
    - 题目以 ``questions_json`` 方式保存，避免额外引入题目表。
    - 后续如需拆分题目表，可在不影响主流程的前提下演进。
    """

    __tablename__ = "interview_sessions"
    __table_args__ = (
        Index("ix_interview_sessions_user_status", "user_id", "status"),
        Index("ix_interview_sessions_skill_status", "skill_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_generate_uuid)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    skill_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    resume_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    language: Mapped[str] = mapped_column(String(32), nullable=False, default="zh-CN")
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=InterviewSessionStatus.DRAFT.value,
        index=True,
    )
    current_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    questions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    session_context_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )


class InterviewAnswerEntity(Base):
    """面试答案明细表。"""

    __tablename__ = "interview_answers"
    __table_args__ = (
        Index("ix_interview_answers_session_round", "session_id", "round_index"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_generate_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    round_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    question_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=InterviewAnswerStatus.SUBMITTED.value,
        index=True,
    )
    score_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    feedback_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    answer_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )


class InterviewReportEntity(Base):
    """面试报告表。"""

    __tablename__ = "interview_reports"
    __table_args__ = (Index("ix_interview_reports_session_status", "session_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_generate_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=InterviewReportStatus.PENDING.value,
        index=True,
    )
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    score_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )


__all__ = [
    "CreateInterviewRequest",
    "InterviewAnswerEntity",
    "InterviewAnswerStatus",
    "InterviewQuestionSnapshot",
    "InterviewQuestionSource",
    "InterviewQuestionStatus",
    "InterviewReportEntity",
    "InterviewReportStatus",
    "InterviewSessionDTO",
    "InterviewSessionEntity",
    "InterviewSessionStatus",
    "InterviewWorkflowAction",
    "SubmitAnswerRequest",
    "SubmitAnswerResponse",
]
