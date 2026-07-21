"""MCP 配置与客户端集成测试。"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from app.config import McpSettings, Settings


def test_mcp_settings_keeps_existing_http_servers() -> None:
    """默认 MCP 配置应保持现有 cls/monitor HTTP 服务不变。"""

    settings = Settings(mcp=McpSettings())

    assert settings.mcp_servers == {
        "cls": {
            "transport": "streamable-http",
            "url": "http://localhost:8003/mcp",
        },
        "monitor": {
            "transport": "streamable-http",
            "url": "http://localhost:8004/mcp",
        },
    }


def test_mcp_settings_enables_github_stdio_server_with_pat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置 GitHub PAT 后，应产出 stdio 版 GitHub MCP 服务描述。"""

    monkeypatch.setenv("MCP__GITHUB__ENABLED", "true")
    monkeypatch.setenv("MCP__GITHUB__COMMAND", "/usr/local/bin/github-mcp-server")
    monkeypatch.setenv("MCP__GITHUB__ARGS", "stdio")
    monkeypatch.setenv("MCP__GITHUB__PAT", "ghp_test_token")

    settings = Settings(mcp=McpSettings())
    github_server = settings.mcp_servers["github"]

    assert github_server == {
        "transport": "stdio",
        "command": "/usr/local/bin/github-mcp-server",
        "args": ["stdio"],
        "env": {
            "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_test_token",
            "GITHUB_TOOLSETS": "repos",
        },
    }


def test_mcp_settings_skips_github_server_without_pat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """即使显式启用，缺少 GitHub PAT 时也应跳过 GitHub MCP 服务。"""

    monkeypatch.setenv("MCP__GITHUB__ENABLED", "true")
    monkeypatch.delenv("MCP__GITHUB__PAT", raising=False)
    monkeypatch.delenv("MCP_GITHUB_PAT", raising=False)
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)

    settings = Settings(mcp=McpSettings())

    assert "github" not in settings.mcp_servers


def test_create_mcp_client_passes_generic_server_config_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """客户端创建入口应原样透传 stdio/http 混合 MCP 配置。"""

    mcp_client_module = importlib.import_module("app.agent.mcp_client")
    captured: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, servers: dict[str, dict[str, Any]], **kwargs: Any) -> None:
            captured["servers"] = servers
            captured["kwargs"] = kwargs

    monkeypatch.setattr(mcp_client_module, "MultiServerMCPClient", _FakeClient)

    servers = {
        "cls": {
            "transport": "streamable-http",
            "url": "http://localhost:8003/mcp",
        },
        "github": {
            "transport": "stdio",
            "command": "github-mcp-server",
            "args": ["stdio"],
            "env": {
                "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_test_token",
                "GITHUB_TOOLSETS": "repos",
            },
        },
    }
    tool_interceptors = [object()]

    client = mcp_client_module._create_mcp_client(
        servers=servers,
        tool_interceptors=tool_interceptors,
    )

    assert isinstance(client, _FakeClient)
    assert captured["servers"] == servers
    assert captured["kwargs"]["tool_interceptors"] == tool_interceptors
