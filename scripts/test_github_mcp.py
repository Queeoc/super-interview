"""GitHub MCP Server 冒烟测试脚本。

用途：
1. 检查项目当前 MCP 配置是否已注册 GitHub MCP Server
2. 列出当前可见的 MCP tools，并可选打印参数 schema
3. 默认尝试读取 `github/github-mcp-server` 仓库的 `README.md`

示例：
    conda run -n biz_agent python scripts/test_github_mcp.py --list-only
    conda run -n biz_agent python scripts/test_github_mcp.py --show-schemas
    conda run -n biz_agent python scripts/test_github_mcp.py --owner github --repo github-mcp-server --path README.md
    conda run -n biz_agent python scripts/test_github_mcp.py --tool get_file_contents --tool-args '{"owner":"github","repo":"github-mcp-server","path":"README.md"}'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger

from app.agent.mcp_client import get_mcp_client_with_retry
from app.config import config
from app.utils.logger import setup_logger

_DEFAULT_TOOL_CANDIDATES = (
    "get_file_contents",
    "get_file_content",
    "get_readme",
    "get_repository",
)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GitHub MCP Server 冒烟测试")
    parser.add_argument("--server-name", default="github", help="目标 MCP server 名称，默认 github")
    parser.add_argument("--list-only", action="store_true", help="只列出工具，不执行读取测试")
    parser.add_argument("--show-schemas", action="store_true", help="打印工具参数 schema")
    parser.add_argument("--print-config", action="store_true", help="打印当前 MCP 配置（会脱敏 token）")
    parser.add_argument("--tool", default="", help="显式指定要调用的 tool 名称")
    parser.add_argument(
        "--tool-args",
        default="",
        help="显式传入 JSON 格式 tool 参数；若不传则根据 schema 自动拼装",
    )
    parser.add_argument("--owner", default="github", help="默认测试仓库 owner")
    parser.add_argument("--repo", default="github-mcp-server", help="默认测试仓库 repo")
    parser.add_argument("--path", default="README.md", help="默认测试文件路径")
    parser.add_argument("--ref", default="", help="可选分支/提交引用，如 main")
    parser.add_argument(
        "--max-output-chars",
        type=int,
        default=4000,
        help="打印结果时的最大字符数，默认 4000",
    )
    parser.add_argument(
        "--fail-on-missing-tool",
        action="store_true",
        help="若找不到默认探测 tool，则直接返回失败码",
    )
    return parser


def _mask_sensitive(value: str) -> str:
    """对敏感 token 做日志脱敏。"""

    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-4:]}"


def _sanitize_mcp_servers(servers: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """输出 MCP 配置时隐藏敏感凭据。"""

    sanitized: dict[str, dict[str, Any]] = {}
    for server_name, server_config in servers.items():
        normalized = dict(server_config)
        env = dict(normalized.get("env", {}))
        if "GITHUB_PERSONAL_ACCESS_TOKEN" in env:
            env["GITHUB_PERSONAL_ACCESS_TOKEN"] = _mask_sensitive(env["GITHUB_PERSONAL_ACCESS_TOKEN"])
        if env:
            normalized["env"] = env
        sanitized[server_name] = normalized
    return sanitized


def _tool_name(tool: Any) -> str:
    """获取 tool 名称。"""

    return str(getattr(tool, "name", "") or "")


def _tool_schema(tool: Any) -> dict[str, Any] | None:
    """提取 tool 的参数 schema。"""

    schema = getattr(tool, "args_schema", None)
    if schema is not None:
        if hasattr(schema, "model_json_schema"):
            try:
                return schema.model_json_schema()
            except Exception:
                pass
        if hasattr(schema, "schema"):
            try:
                return schema.schema()  # type: ignore[no-any-return]
            except Exception:
                pass

    if hasattr(tool, "get_input_schema"):
        try:
            input_schema = tool.get_input_schema()
        except Exception:
            input_schema = None
        if input_schema is not None:
            if hasattr(input_schema, "model_json_schema"):
                try:
                    return input_schema.model_json_schema()
                except Exception:
                    pass
            if hasattr(input_schema, "schema"):
                try:
                    return input_schema.schema()  # type: ignore[no-any-return]
                except Exception:
                    pass

    args_payload = getattr(tool, "args", None)
    if isinstance(args_payload, dict) and args_payload:
        if "properties" in args_payload or "required" in args_payload:
            return args_payload
        return {
            "type": "object",
            "properties": args_payload,
        }
    return None


def _pick_target_tool(tools: list[Any], explicit_tool_name: str) -> Any | None:
    """优先按显式名称，其次按默认候选列表选择测试用 tool。"""

    if explicit_tool_name.strip():
        for tool in tools:
            if _tool_name(tool) == explicit_tool_name.strip():
                return tool
        return None

    normalized_by_name = {_tool_name(tool): tool for tool in tools}
    for candidate in _DEFAULT_TOOL_CANDIDATES:
        if candidate in normalized_by_name:
            return normalized_by_name[candidate]
    return None


def _build_default_tool_args(
    schema: dict[str, Any] | None,
    *,
    owner: str,
    repo: str,
    path: str,
    ref: str,
) -> tuple[dict[str, Any], list[str]]:
    """按常见 GitHub 工具参数命名规则自动构造调用参数。"""

    if not schema:
        return {}, []

    properties = schema.get("properties", {})
    if not properties and all(isinstance(key, str) for key in schema.keys()):
        properties = {key: value for key, value in schema.items() if key not in {"required", "type", "title"}}
    required_fields = list(schema.get("required", []))
    payload: dict[str, Any] = {}

    def _set_first(aliases: tuple[str, ...], value: str) -> None:
        for alias in aliases:
            if alias in properties:
                payload[alias] = value
                return

    _set_first(("owner", "repo_owner", "repository_owner"), owner)
    _set_first(("repo", "repository", "repo_name", "repository_name"), repo)
    _set_first(("path", "file_path", "filepath"), path)
    if ref.strip():
        _set_first(("ref", "branch", "sha"), ref.strip())

    missing_required = [field for field in required_fields if field not in payload]
    return payload, missing_required


def _build_tool_name_fallback_args(
    tool_name: str,
    *,
    owner: str,
    repo: str,
    path: str,
    ref: str,
) -> dict[str, Any]:
    """当 schema 不可用时，按已知 GitHub tool 名称补齐常见参数。"""

    normalized_name = tool_name.strip()
    if normalized_name in {"get_file_contents", "get_file_content"}:
        payload = {
            "owner": owner,
            "repo": repo,
            "path": path,
        }
        if ref.strip():
            payload["ref"] = ref.strip()
        return payload

    if normalized_name in {
        "get_repository",
        "get_commit",
        "list_branches",
        "list_commits",
        "list_releases",
        "list_tags",
        "get_latest_release",
        "get_release_by_tag",
        "get_tag",
        "list_repository_collaborators",
    }:
        payload = {
            "owner": owner,
            "repo": repo,
        }
        if ref.strip():
            payload["ref"] = ref.strip()
        return payload

    return {}


def _preview_output(value: Any, max_output_chars: int) -> str:
    """把任意返回值压缩为易读文本。"""

    if isinstance(value, str):
        rendered = value
    else:
        try:
            rendered = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        except TypeError:
            rendered = str(value)
    if len(rendered) <= max_output_chars:
        return rendered
    return rendered[:max_output_chars] + "\n...<truncated>..."


async def _invoke_tool(tool: Any, tool_args: dict[str, Any]) -> Any:
    """兼容常见 LangChain tool 运行接口。"""

    if hasattr(tool, "ainvoke"):
        return await tool.ainvoke(tool_args)
    if hasattr(tool, "arun"):
        return await tool.arun(tool_args)
    raise RuntimeError(f"tool {_tool_name(tool) or '<unknown>'} does not support async invocation")


async def _async_main(args: argparse.Namespace) -> int:
    """异步主入口。"""

    setup_logger(force=True)

    if args.print_config:
        logger.info(
            "当前 MCP servers 配置:\n{}",
            json.dumps(_sanitize_mcp_servers(config.mcp_servers), ensure_ascii=False, indent=2),
        )

    if args.server_name not in config.mcp_servers:
        logger.error(
            "未在 config.mcp_servers 中找到目标 server: {}。当前可用 server={}",
            args.server_name,
            sorted(config.mcp_servers.keys()),
        )
        return 2

    client = await get_mcp_client_with_retry(force_new=True)
    tools = await client.get_tools()
    tool_names = [_tool_name(tool) for tool in tools]

    logger.info("MCP tool 总数: {}", len(tool_names))
    for name in tool_names:
        logger.info("tool: {}", name)

    if args.show_schemas:
        for tool in tools:
            schema = _tool_schema(tool)
            if schema is None:
                continue
            logger.info(
                "tool schema: {}\n{}",
                _tool_name(tool),
                json.dumps(schema, ensure_ascii=False, indent=2),
            )

    if args.list_only:
        logger.success("已完成 MCP tools 枚举，未执行数据访问测试")
        return 0

    target_tool = _pick_target_tool(tools, args.tool)
    if target_tool is None:
        logger.error(
            "未找到目标 tool。显式参数 tool={}，默认候选={}",
            args.tool or "<auto>",
            list(_DEFAULT_TOOL_CANDIDATES),
        )
        if args.fail_on_missing_tool:
            return 3
        logger.warning("你可以先加 --show-schemas 看看官方 server 当前暴露的 tool 名称")
        return 0

    tool_name = _tool_name(target_tool)
    schema = _tool_schema(target_tool)
    if schema is not None:
        logger.info("选中的测试 tool: {}", tool_name)

    auto_args, missing_required = _build_default_tool_args(
        schema,
        owner=args.owner,
        repo=args.repo,
        path=args.path,
        ref=args.ref,
    )

    explicit_args: dict[str, Any] = {}
    if args.tool_args.strip():
        explicit_args = json.loads(args.tool_args)
        if not isinstance(explicit_args, dict):
            raise RuntimeError("--tool-args 必须是 JSON object")

    fallback_args = _build_tool_name_fallback_args(
        tool_name,
        owner=args.owner,
        repo=args.repo,
        path=args.path,
        ref=args.ref,
    )
    final_args = {**fallback_args, **auto_args, **explicit_args}
    unresolved_required = []
    if schema is not None:
        unresolved_required = [
            field for field in schema.get("required", []) if field not in final_args
        ]

    if unresolved_required:
        logger.error(
            "无法自动补齐必填参数。tool={}, missing_required={}, current_args={}",
            tool_name,
            unresolved_required,
            final_args,
        )
        logger.info("建议改用 --tool-args 显式传参")
        return 4

    logger.info("开始调用 tool={}，args={}", tool_name, final_args)
    result = await _invoke_tool(target_tool, final_args)
    logger.success("tool 调用成功: {}", tool_name)
    print(_preview_output(result, args.max_output_chars))

    if missing_required:
        logger.warning("schema 中存在未自动映射字段，但当前调用仍然成功: {}", missing_required)

    return 0


def main() -> int:
    """同步主入口。"""

    args = _build_arg_parser().parse_args()
    try:
        return asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        logger.warning("用户中断 GitHub MCP 测试")
        return 130
    except Exception as exc:
        logger.exception("GitHub MCP 测试失败: {}", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
