"""Configuration management for the refactored super-interview project.

Phase 0 introduces grouped settings that match the target interview-platform
architecture while preserving legacy flat accessors used by the current code.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote_plus

from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _coerce_boolish(value: Any) -> Any:
    """将外部环境输入的各种字符串变体安全地转换为 Python 布尔值。"""

    if isinstance(value, bool) or value is None:
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "debug", "dev", "development"}:
            return True
        if normalized in {"0", "false", "no", "off", "release", "prod", "production"}:
            return False

    return value


def _coerce_string_listish(value: Any) -> Any:
    """将环境变量中的列表值安全转换为字符串列表。"""

    if value is None or isinstance(value, list):
        return value

    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return []
        if normalized.startswith("["):
            try:
                parsed = json.loads(normalized)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in normalized.split(",") if item.strip()]

    return value


class SectionSettings(BaseSettings):
    """
    配置板块基类（环境配置脚手架）
    
    作用:
        1. 统一封装所有配置子类的底层读取规则，避免代码冗余(DRY原则)。
        2. 自动从项目根目录的 .env 文件或系统环境变量中加载配置。
        3. 提供跨平台的 UTF-8 编码支持、大小写不敏感容错，并自动忽略无关的干扰环境变量。
    """

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


class AdminSettings(SectionSettings):
    """管理员入口的最小运行时配置。"""

    enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("ADMIN__ENABLED", "ADMIN_ENABLED"),
    )
    token: str = Field(
        default="",
        validation_alias=AliasChoices("ADMIN__TOKEN", "ADMIN_TOKEN"),
    )

    @field_validator("enabled", mode="before")
    @classmethod
    def normalize_enabled(cls, value: Any) -> Any:
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

    '''在读取配置时，把 enabled 和 echo 这两个字段的值，先统一转换成布尔值格式。'''
    @field_validator("enabled", "echo", mode="before")
    @classmethod
    def normalize_bools(cls, value: Any) -> Any:
        return _coerce_boolish(value)

    '''动态拼接 URL'''
    @property
    def async_url(self) -> str:
        """动态计算属性：安全拼接 SQLAlchemy 异步连接串"""
        # 对账号密码进行 URL 百分号转义，防止密码中的 '@' 破坏 URL 结构
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


class RateLimitSettings(SectionSettings):
    """限流配置。"""

    enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("RATE_LIMIT__ENABLED", "RATE_LIMIT_ENABLED"),
    )
    window_seconds: int = Field(
        default=60,
        validation_alias=AliasChoices("RATE_LIMIT__WINDOW_SECONDS", "RATE_LIMIT_WINDOW_SECONDS"),
    )
    read_global_limit: int = Field(
        default=1200,
        validation_alias=AliasChoices(
            "RATE_LIMIT__READ_GLOBAL_LIMIT",
            "RATE_LIMIT_READ_GLOBAL_LIMIT",
        ),
    )
    read_ip_limit: int = Field(
        default=240,
        validation_alias=AliasChoices("RATE_LIMIT__READ_IP_LIMIT", "RATE_LIMIT_READ_IP_LIMIT"),
    )
    read_user_limit: int = Field(
        default=120,
        validation_alias=AliasChoices("RATE_LIMIT__READ_USER_LIMIT", "RATE_LIMIT_READ_USER_LIMIT"),
    )
    write_global_limit: int = Field(
        default=600,
        validation_alias=AliasChoices(
            "RATE_LIMIT__WRITE_GLOBAL_LIMIT",
            "RATE_LIMIT_WRITE_GLOBAL_LIMIT",
        ),
    )
    write_ip_limit: int = Field(
        default=120,
        validation_alias=AliasChoices("RATE_LIMIT__WRITE_IP_LIMIT", "RATE_LIMIT_WRITE_IP_LIMIT"),
    )
    write_user_limit: int = Field(
        default=60,
        validation_alias=AliasChoices("RATE_LIMIT__WRITE_USER_LIMIT", "RATE_LIMIT_WRITE_USER_LIMIT"),
    )

    @field_validator("enabled", mode="before")
    @classmethod
    def normalize_enabled(cls, value: Any) -> Any:
        return _coerce_boolish(value)


class StreamTaskSettings(SectionSettings):
    """Redis Stream 异步任务配置。"""

    enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("STREAM_TASK__ENABLED", "STREAM_TASK_ENABLED"),
    )
    stream_name: str = Field(
        default="async_tasks",
        validation_alias=AliasChoices("STREAM_TASK__STREAM_NAME", "STREAM_TASK_STREAM_NAME"),
    )
    dead_letter_stream_name: str = Field(
        default="async_tasks_dead_letter",
        validation_alias=AliasChoices(
            "STREAM_TASK__DEAD_LETTER_STREAM_NAME",
            "STREAM_TASK_DEAD_LETTER_STREAM_NAME",
        ),
    )
    status_key_prefix: str = Field(
        default="async_task:status",
        validation_alias=AliasChoices(
            "STREAM_TASK__STATUS_KEY_PREFIX",
            "STREAM_TASK_STATUS_KEY_PREFIX",
        ),
    )
    consumer_group: str = Field(
        default="biz-agent",
        validation_alias=AliasChoices(
            "STREAM_TASK__CONSUMER_GROUP",
            "STREAM_TASK_CONSUMER_GROUP",
        ),
    )
    consumer_name_prefix: str = Field(
        default="worker",
        validation_alias=AliasChoices(
            "STREAM_TASK__CONSUMER_NAME_PREFIX",
            "STREAM_TASK_CONSUMER_NAME_PREFIX",
        ),
    )
    read_count: int = Field(
        default=10,
        validation_alias=AliasChoices("STREAM_TASK__READ_COUNT", "STREAM_TASK_READ_COUNT"),
    )
    block_ms: int = Field(
        default=1000,
        validation_alias=AliasChoices("STREAM_TASK__BLOCK_MS", "STREAM_TASK_BLOCK_MS"),
    )
    claim_idle_ms: int = Field(
        default=60000,
        validation_alias=AliasChoices("STREAM_TASK__CLAIM_IDLE_MS", "STREAM_TASK_CLAIM_IDLE_MS"),
    )
    max_retries: int = Field(
        default=3,
        validation_alias=AliasChoices("STREAM_TASK__MAX_RETRIES", "STREAM_TASK_MAX_RETRIES"),
    )
    maxlen: int = Field(
        default=5000,
        validation_alias=AliasChoices("STREAM_TASK__MAXLEN", "STREAM_TASK_MAXLEN"),
    )
    worker_poll_interval_seconds: float = Field(
        default=1.0,
        validation_alias=AliasChoices(
            "STREAM_TASK__WORKER_POLL_INTERVAL_SECONDS",
            "STREAM_TASK_WORKER_POLL_INTERVAL_SECONDS",
        ),
    )
    synchronous_fallback_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "STREAM_TASK__SYNCHRONOUS_FALLBACK_ENABLED",
            "STREAM_TASK_SYNCHRONOUS_FALLBACK_ENABLED",
        ),
    )

    @field_validator("enabled", "synchronous_fallback_enabled", mode="before")
    @classmethod
    def normalize_switches(cls, value: Any) -> Any:
        return _coerce_boolish(value)


class ProviderRuntimeSettings(SectionSettings):
    """Provider 运行时策略配置。"""

    prefer_database_default: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "PROVIDER_RUNTIME__PREFER_DATABASE_DEFAULT",
            "PROVIDER_RUNTIME_PREFER_DATABASE_DEFAULT",
        ),
    )
    env_fallback_provider_code: str = Field(
        default="env-default",
        validation_alias=AliasChoices(
            "PROVIDER_RUNTIME__ENV_FALLBACK_PROVIDER_CODE",
            "PROVIDER_RUNTIME_ENV_FALLBACK_PROVIDER_CODE",
        ),
    )
    env_fallback_provider_name: str = Field(
        default="Environment Default Provider",
        validation_alias=AliasChoices(
            "PROVIDER_RUNTIME__ENV_FALLBACK_PROVIDER_NAME",
            "PROVIDER_RUNTIME_ENV_FALLBACK_PROVIDER_NAME",
        ),
    )

    @field_validator("prefer_database_default", mode="before")
    @classmethod
    def normalize_prefer_database_default(cls, value: Any) -> Any:
        return _coerce_boolish(value)


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
        """动态计算属性：获取本地文件存储的【绝对路径】。"""

        return Path(self.base_dir).resolve()

    @property
    def resolved_temp_dir(self) -> Path:
        """动态计算属性：获取本地临时缓存文件存储的【绝对路径】"""

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
        default=5,
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


class McpServerConfig(BaseModel):
    """统一的 MCP 服务端运行时描述。"""

    transport: str = Field(..., description="MCP 传输方式")
    url: str | None = Field(default=None, description="HTTP MCP 服务地址")
    command: str | None = Field(default=None, description="stdio MCP 启动命令")
    args: list[str] = Field(default_factory=list, description="stdio MCP 启动参数")
    env: dict[str, str] = Field(default_factory=dict, description="stdio MCP 环境变量")

    def to_runtime_dict(self) -> dict[str, Any]:
        """转换为 MultiServerMCPClient 可消费的配置字典。"""

        payload: dict[str, Any] = {
            "transport": self.transport,
        }
        if self.url:
            payload["url"] = self.url
        if self.command:
            payload["command"] = self.command
        if self.args:
            payload["args"] = list(self.args)
        if self.env:
            payload["env"] = dict(self.env)
        return payload


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
    github_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("MCP__GITHUB__ENABLED", "MCP_GITHUB_ENABLED"),
    )
    github_transport: str = Field(
        default="stdio",
        validation_alias=AliasChoices("MCP__GITHUB__TRANSPORT", "MCP_GITHUB_TRANSPORT"),
    )
    github_command: str = Field(
        default="github-mcp-server",
        validation_alias=AliasChoices("MCP__GITHUB__COMMAND", "MCP_GITHUB_COMMAND"),
    )
    github_args: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["stdio"],
        validation_alias=AliasChoices("MCP__GITHUB__ARGS", "MCP_GITHUB_ARGS"),
    )
    github_pat: str = Field(
        default="",
        validation_alias=AliasChoices(
            "MCP__GITHUB__PAT",
            "MCP_GITHUB_PAT",
            "GITHUB_PERSONAL_ACCESS_TOKEN",
        ),
    )
    github_toolsets: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["repos"],
        validation_alias=AliasChoices("MCP__GITHUB__TOOLSETS", "MCP_GITHUB_TOOLSETS"),
    )

    @field_validator("github_enabled", mode="before")
    @classmethod
    def normalize_github_enabled(cls, value: Any) -> Any:
        return _coerce_boolish(value)

    @field_validator("github_args", "github_toolsets", mode="before")
    @classmethod
    def normalize_github_list_fields(cls, value: Any) -> Any:
        return _coerce_string_listish(value)

    def _build_http_server_config(self, *, transport: str, url: str) -> dict[str, Any]:
        """构建基于 HTTP 的 MCP 服务配置。"""

        return McpServerConfig(
            transport=transport,
            url=url,
        ).to_runtime_dict()

    def _build_github_server_config(self) -> dict[str, Any] | None:
        """构建 GitHub MCP Server 配置；若缺少必要凭据则返回空。"""

        if not self.github_enabled:
            return None

        github_pat = self.github_pat.strip()
        if not github_pat:
            return None

        github_env = {
            "GITHUB_PERSONAL_ACCESS_TOKEN": github_pat,
        }
        normalized_toolsets = [item.strip() for item in self.github_toolsets if item.strip()]
        if normalized_toolsets:
            github_env["GITHUB_TOOLSETS"] = ",".join(normalized_toolsets)

        return McpServerConfig(
            transport=self.github_transport,
            command=self.github_command.strip() or "github-mcp-server",
            args=[item.strip() for item in self.github_args if item.strip()],
            env=github_env,
        ).to_runtime_dict()

    @property
    def servers(self) -> dict[str, dict[str, Any]]:
        """Return the MCP server mapping expected by the current client."""

        server_map: dict[str, dict[str, Any]] = {
            "cls": self._build_http_server_config(
                transport=self.cls_transport,
                url=self.cls_url,
            ),
            "monitor": self._build_http_server_config(
                transport=self.monitor_transport,
                url=self.monitor_url,
            ),
        }
        github_server = self._build_github_server_config()
        if github_server is not None:
            server_map["github"] = github_server
        return server_map


class Settings(BaseModel):
    """Root project settings grouped by target architecture domains."""

    app: AppSettings = Field(default_factory=AppSettings)
    admin: AdminSettings = Field(default_factory=AdminSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    milvus: MilvusSettings = Field(default_factory=MilvusSettings)
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    stream_task: StreamTaskSettings = Field(default_factory=StreamTaskSettings)
    provider_runtime: ProviderRuntimeSettings = Field(default_factory=ProviderRuntimeSettings)
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

    @property
    def rate_limit_enabled(self) -> bool:
        return self.rate_limit.enabled

    @property
    def stream_task_enabled(self) -> bool:
        return self.stream_task.enabled

    @property
    def provider_runtime_prefer_database_default(self) -> bool:
        return self.provider_runtime.prefer_database_default


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
