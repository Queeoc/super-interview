"""Configuration management for the refactored super-interview project.

Phase 0 introduces grouped settings that match the target interview-platform
architecture while preserving legacy flat accessors used by the current code.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _coerce_boolish(value: Any) -> Any:
    """Convert common environment string variants into booleans."""

    if isinstance(value, bool) or value is None:
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "debug", "dev", "development"}:
            return True
        if normalized in {"0", "false", "no", "off", "release", "prod", "production"}:
            return False

    return value


class SectionSettings(BaseSettings):
    """Shared base class for environment-backed settings sections."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


class AppSettings(SectionSettings):
    """Application runtime settings."""

    name: str = Field(
        default="super-interview",
        validation_alias=AliasChoices("APP__NAME", "APP_NAME"),
    )
    version: str = Field(
        default="1.0.0",
        validation_alias=AliasChoices("APP__VERSION", "APP_VERSION"),
    )
    debug: bool = Field(
        default=False,
        validation_alias=AliasChoices("APP__DEBUG", "DEBUG"),
    )
    host: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("APP__HOST", "HOST"),
    )
    port: int = Field(
        default=9900,
        validation_alias=AliasChoices("APP__PORT", "PORT"),
    )

    @field_validator("debug", mode="before")
    @classmethod
    def normalize_debug(cls, value: Any) -> Any:
        """Accept both standard booleans and common runtime labels."""

        return _coerce_boolish(value)


class LlmSettings(SectionSettings):
    """LLM provider settings."""

    default_provider: str = Field(
        default="dashscope",
        validation_alias=AliasChoices("LLM__DEFAULT_PROVIDER", "LLM_DEFAULT_PROVIDER"),
    )
    api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LLM__API_KEY", "DASHSCOPE_API_KEY"),
    )
    api_base: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias=AliasChoices("LLM__API_BASE", "DASHSCOPE_API_BASE"),
    )
    chat_model: str = Field(
        default="qwen-max",
        validation_alias=AliasChoices("LLM__CHAT_MODEL", "DASHSCOPE_MODEL"),
    )
    embedding_model: str = Field(
        default="text-embedding-v4",
        validation_alias=AliasChoices(
            "LLM__EMBEDDING_MODEL",
            "DASHSCOPE_EMBEDDING_MODEL",
        ),
    )
    request_timeout_seconds: int = Field(
        default=60,
        validation_alias=AliasChoices(
            "LLM__REQUEST_TIMEOUT_SECONDS",
            "LLM_REQUEST_TIMEOUT_SECONDS",
        ),
    )


class MilvusSettings(SectionSettings):
    """Milvus and vector store settings."""

    host: str = Field(
        default="localhost",
        validation_alias=AliasChoices("MILVUS__HOST", "MILVUS_HOST"),
    )
    port: int = Field(
        default=19530,
        validation_alias=AliasChoices("MILVUS__PORT", "MILVUS_PORT"),
    )
    timeout_ms: int = Field(
        default=10000,
        validation_alias=AliasChoices("MILVUS__TIMEOUT_MS", "MILVUS_TIMEOUT"),
    )
    collection_name: str = Field(
        default="biz",
        validation_alias=AliasChoices(
            "MILVUS__COLLECTION_NAME",
            "MILVUS_COLLECTION_NAME",
        ),
    )
    vector_dim: int = Field(
        default=1024,
        validation_alias=AliasChoices("MILVUS__VECTOR_DIM", "MILVUS_VECTOR_DIM"),
    )


class PostgresSettings(SectionSettings):
    """Placeholder PostgreSQL settings for later phases."""

    enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("POSTGRES__ENABLED", "POSTGRES_ENABLED"),
    )
    host: str = Field(
        default="localhost",
        validation_alias=AliasChoices("POSTGRES__HOST", "POSTGRES_HOST"),
    )
    port: int = Field(
        default=5432,
        validation_alias=AliasChoices("POSTGRES__PORT", "POSTGRES_PORT"),
    )
    database: str = Field(
        default="super_interview",
        validation_alias=AliasChoices(
            "POSTGRES__DATABASE",
            "POSTGRES_DB",
            "POSTGRES_DATABASE",
        ),
    )
    user: str = Field(
        default="postgres",
        validation_alias=AliasChoices("POSTGRES__USER", "POSTGRES_USER"),
    )
    password: str = Field(
        default="",
        validation_alias=AliasChoices("POSTGRES__PASSWORD", "POSTGRES_PASSWORD"),
    )
    echo: bool = Field(
        default=False,
        validation_alias=AliasChoices("POSTGRES__ECHO", "POSTGRES_ECHO"),
    )
    pool_size: int = Field(
        default=10,
        validation_alias=AliasChoices("POSTGRES__POOL_SIZE", "POSTGRES_POOL_SIZE"),
    )
    max_overflow: int = Field(
        default=20,
        validation_alias=AliasChoices("POSTGRES__MAX_OVERFLOW", "POSTGRES_MAX_OVERFLOW"),
    )
    pool_timeout_seconds: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "POSTGRES__POOL_TIMEOUT_SECONDS",
            "POSTGRES_POOL_TIMEOUT_SECONDS",
        ),
    )

    @field_validator("enabled", "echo", mode="before")
    @classmethod
    def normalize_bools(cls, value: Any) -> Any:
        return _coerce_boolish(value)

    @property
    def async_url(self) -> str:
        """Build the async SQLAlchemy connection URL."""

        user = quote_plus(self.user)
        password = quote_plus(self.password)
        host = self.host
        port = self.port
        database = self.database
        return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"


class RedisSettings(SectionSettings):
    """Placeholder Redis settings for later phases."""

    enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("REDIS__ENABLED", "REDIS_ENABLED"),
    )
    host: str = Field(
        default="localhost",
        validation_alias=AliasChoices("REDIS__HOST", "REDIS_HOST"),
    )
    port: int = Field(
        default=6379,
        validation_alias=AliasChoices("REDIS__PORT", "REDIS_PORT"),
    )
    db: int = Field(
        default=0,
        validation_alias=AliasChoices("REDIS__DB", "REDIS_DB"),
    )
    password: str = Field(
        default="",
        validation_alias=AliasChoices("REDIS__PASSWORD", "REDIS_PASSWORD"),
    )
    key_prefix: str = Field(
        default="super_interview",
        validation_alias=AliasChoices("REDIS__KEY_PREFIX", "REDIS_KEY_PREFIX"),
    )
    default_ttl_seconds: int = Field(
        default=3600,
        validation_alias=AliasChoices(
            "REDIS__DEFAULT_TTL_SECONDS",
            "REDIS_DEFAULT_TTL_SECONDS",
        ),
    )
    decode_responses: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "REDIS__DECODE_RESPONSES",
            "REDIS_DECODE_RESPONSES",
        ),
    )
    socket_timeout_seconds: int = Field(
        default=5,
        validation_alias=AliasChoices(
            "REDIS__SOCKET_TIMEOUT_SECONDS",
            "REDIS_SOCKET_TIMEOUT_SECONDS",
        ),
    )

    @field_validator("enabled", "decode_responses", mode="before")
    @classmethod
    def normalize_enabled(cls, value: Any) -> Any:
        return _coerce_boolish(value)

    @property
    def url(self) -> str:
        """Build the Redis connection URL."""

        if self.password:
            password = quote_plus(self.password)
            return f"redis://:{password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"


class StorageSettings(SectionSettings):
    """Placeholder object storage settings for later phases."""

    provider: str = Field(
        default="local",
        validation_alias=AliasChoices("STORAGE__PROVIDER", "STORAGE_PROVIDER"),
    )
    endpoint: str = Field(
        default="",
        validation_alias=AliasChoices("STORAGE__ENDPOINT", "STORAGE_ENDPOINT"),
    )
    bucket: str = Field(
        default="super-interview",
        validation_alias=AliasChoices("STORAGE__BUCKET", "STORAGE_BUCKET"),
    )
    access_key: str = Field(
        default="",
        validation_alias=AliasChoices("STORAGE__ACCESS_KEY", "STORAGE_ACCESS_KEY"),
    )
    secret_key: str = Field(
        default="",
        validation_alias=AliasChoices("STORAGE__SECRET_KEY", "STORAGE_SECRET_KEY"),
    )
    base_dir: str = Field(
        default="uploads",
        validation_alias=AliasChoices("STORAGE__BASE_DIR", "STORAGE_BASE_DIR"),
    )
    temp_dir: str = Field(
        default="uploads/tmp",
        validation_alias=AliasChoices("STORAGE__TEMP_DIR", "STORAGE_TEMP_DIR"),
    )
    public_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("STORAGE__PUBLIC_BASE_URL", "STORAGE_PUBLIC_BASE_URL"),
    )

    @property
    def resolved_base_dir(self) -> Path:
        """Return the absolute base directory for local storage."""

        return Path(self.base_dir).resolve()

    @property
    def resolved_temp_dir(self) -> Path:
        """Return the absolute temp directory for local storage."""

        return Path(self.temp_dir).resolve()


class InterviewSettings(SectionSettings):
    """Placeholder interview-domain settings for later phases."""

    default_language: str = Field(
        default="zh-CN",
        validation_alias=AliasChoices(
            "INTERVIEW__DEFAULT_LANGUAGE",
            "INTERVIEW_DEFAULT_LANGUAGE",
        ),
    )
    default_mode: str = Field(
        default="text",
        validation_alias=AliasChoices(
            "INTERVIEW__DEFAULT_MODE",
            "INTERVIEW_DEFAULT_MODE",
        ),
    )
    session_ttl_minutes: int = Field(
        default=120,
        validation_alias=AliasChoices(
            "INTERVIEW__SESSION_TTL_MINUTES",
            "INTERVIEW_SESSION_TTL_MINUTES",
        ),
    )
    max_rounds: int = Field(
        default=10,
        validation_alias=AliasChoices("INTERVIEW__MAX_ROUNDS", "INTERVIEW_MAX_ROUNDS"),
    )
    max_follow_up_questions: int = Field(
        default=2,
        validation_alias=AliasChoices(
            "INTERVIEW__MAX_FOLLOW_UP_QUESTIONS",
            "INTERVIEW_MAX_FOLLOW_UP_QUESTIONS",
        ),
    )
    evaluation_batch_size: int = Field(
        default=3,
        validation_alias=AliasChoices(
            "INTERVIEW__EVALUATION_BATCH_SIZE",
            "INTERVIEW_EVALUATION_BATCH_SIZE",
        ),
    )
    structured_output_max_retries: int = Field(
        default=1,
        validation_alias=AliasChoices(
            "INTERVIEW__STRUCTURED_OUTPUT_MAX_RETRIES",
            "INTERVIEW_STRUCTURED_OUTPUT_MAX_RETRIES",
        ),
    )
    rubric_root_dir: str = Field(
        default=str(Path(__file__).resolve().parents[1] / "knowledge_base" / "rubrics"),
        validation_alias=AliasChoices(
            "INTERVIEW__RUBRIC_ROOT_DIR",
            "INTERVIEW_RUBRIC_ROOT_DIR",
        ),
    )
    report_export_format: str = Field(
        default="markdown",
        validation_alias=AliasChoices(
            "INTERVIEW__REPORT_EXPORT_FORMAT",
            "INTERVIEW_REPORT_EXPORT_FORMAT",
        ),
    )


class SkillSettings(SectionSettings):
    """Preset skill system settings."""

    root_dir: str = Field(
        default=str(Path(__file__).resolve().parents[1] / "skills"),
        validation_alias=AliasChoices("SKILL__ROOT_DIR", "SKILL_ROOT_DIR"),
    )

    @property
    def resolved_root_dir(self) -> Path:
        """Return the absolute root directory for preset skill resources."""

        return Path(self.root_dir).resolve()


class RagSettings(SectionSettings):
    """RAG runtime settings."""

    top_k: int = Field(
        default=3,
        validation_alias=AliasChoices("RAG__TOP_K", "RAG_TOP_K"),
    )
    model: str = Field(
        default="qwen-max",
        validation_alias=AliasChoices("RAG__MODEL", "RAG_MODEL"),
    )
    chunk_max_size: int = Field(
        default=800,
        validation_alias=AliasChoices("RAG__CHUNK_MAX_SIZE", "CHUNK_MAX_SIZE"),
    )
    chunk_overlap: int = Field(
        default=100,
        validation_alias=AliasChoices("RAG__CHUNK_OVERLAP", "CHUNK_OVERLAP"),
    )
    score_threshold: float = Field(
        default=1.2,
        validation_alias=AliasChoices("RAG__SCORE_THRESHOLD", "RAG_SCORE_THRESHOLD"),
    )
    rewrite_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("RAG__REWRITE_ENABLED", "RAG_REWRITE_ENABLED"),
    )
    empty_result_message: str = Field(
        default="没有检索到相关知识，请补充更具体的问题或调整知识库范围。",
        validation_alias=AliasChoices(
            "RAG__EMPTY_RESULT_MESSAGE",
            "RAG_EMPTY_RESULT_MESSAGE",
        ),
    )

    @field_validator("rewrite_enabled", mode="before")
    @classmethod
    def normalize_rewrite_enabled(cls, value: Any) -> Any:
        return _coerce_boolish(value)


class McpSettings(SectionSettings):
    """MCP server settings kept for current runtime compatibility."""

    cls_transport: str = Field(
        default="streamable-http",
        validation_alias=AliasChoices("MCP__CLS__TRANSPORT", "MCP_CLS_TRANSPORT"),
    )
    cls_url: str = Field(
        default="http://localhost:8003/mcp",
        validation_alias=AliasChoices("MCP__CLS__URL", "MCP_CLS_URL"),
    )
    monitor_transport: str = Field(
        default="streamable-http",
        validation_alias=AliasChoices("MCP__MONITOR__TRANSPORT", "MCP_MONITOR_TRANSPORT"),
    )
    monitor_url: str = Field(
        default="http://localhost:8004/mcp",
        validation_alias=AliasChoices("MCP__MONITOR__URL", "MCP_MONITOR_URL"),
    )

    @property
    def servers(self) -> dict[str, dict[str, str]]:
        """Return the MCP server mapping expected by the current client."""

        return {
            "cls": {
                "transport": self.cls_transport,
                "url": self.cls_url,
            },
            "monitor": {
                "transport": self.monitor_transport,
                "url": self.monitor_url,
            },
        }


class Settings(BaseModel):
    """Root project settings grouped by target architecture domains."""

    app: AppSettings = Field(default_factory=AppSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    milvus: MilvusSettings = Field(default_factory=MilvusSettings)
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    interview: InterviewSettings = Field(default_factory=InterviewSettings)
    skill: SkillSettings = Field(default_factory=SkillSettings)
    rag: RagSettings = Field(default_factory=RagSettings)
    mcp: McpSettings = Field(default_factory=McpSettings)

    @property
    def app_name(self) -> str:
        return self.app.name

    @property
    def app_version(self) -> str:
        return self.app.version

    @property
    def debug(self) -> bool:
        return self.app.debug

    @property
    def host(self) -> str:
        return self.app.host

    @property
    def port(self) -> int:
        return self.app.port

    @property
    def skill_root_dir(self) -> Path:
        return self.skill.resolved_root_dir

    @property
    def dashscope_api_key(self) -> str:
        return self.llm.api_key

    @property
    def dashscope_api_base(self) -> str:
        return self.llm.api_base

    @property
    def dashscope_model(self) -> str:
        return self.llm.chat_model

    @property
    def dashscope_embedding_model(self) -> str:
        return self.llm.embedding_model

    @property
    def milvus_host(self) -> str:
        return self.milvus.host

    @property
    def milvus_port(self) -> int:
        return self.milvus.port

    @property
    def milvus_timeout(self) -> int:
        return self.milvus.timeout_ms

    @property
    def rag_top_k(self) -> int:
        return self.rag.top_k

    @property
    def rag_model(self) -> str:
        return self.rag.model

    @property
    def chunk_max_size(self) -> int:
        return self.rag.chunk_max_size

    @property
    def chunk_overlap(self) -> int:
        return self.rag.chunk_overlap

    @property
    def rag_score_threshold(self) -> float:
        return self.rag.score_threshold

    @property
    def rag_rewrite_enabled(self) -> bool:
        return self.rag.rewrite_enabled

    @property
    def rag_empty_result_message(self) -> str:
        return self.rag.empty_result_message

    @property
    def mcp_cls_transport(self) -> str:
        return self.mcp.cls_transport

    @property
    def mcp_cls_url(self) -> str:
        return self.mcp.cls_url

    @property
    def mcp_monitor_transport(self) -> str:
        return self.mcp.monitor_transport

    @property
    def mcp_monitor_url(self) -> str:
        return self.mcp.monitor_url

    @property
    def mcp_servers(self) -> dict[str, dict[str, Any]]:
        return self.mcp.servers


config = Settings()


def _sync_runtime_environment() -> None:
    """将配置同步到运行时环境变量，兼容依赖 os.environ 的第三方 SDK。"""

    # ChatQwen 等 SDK 会直接从环境变量读取 DashScope 配置。
    # pydantic-settings 虽然能从 .env 读取值，但不会自动回写到 os.environ，
    # 因此这里需要显式同步，避免出现 embedding 可用但 ChatQwen 401 的情况。
    if config.dashscope_api_key:
        os.environ["DASHSCOPE_API_KEY"] = config.dashscope_api_key

    if config.dashscope_api_base:
        os.environ["DASHSCOPE_API_BASE"] = config.dashscope_api_base

    if config.dashscope_model:
        os.environ["DASHSCOPE_MODEL"] = config.dashscope_model


_sync_runtime_environment()
