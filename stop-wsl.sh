#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "===================================="
echo "  super-interview 停止服务 (WSL)"
echo "===================================="
echo ""

# ── Stop FastAPI ────────────────────────────────────────────
echo "[1/4] 停止 FastAPI 服务..."
if pkill -f "uvicorn app.main:app" 2>/dev/null; then
    echo "[成功] FastAPI 已停止"
else
    echo "[信息] FastAPI 未在运行"
fi
echo ""

# ── Stop MCP services ───────────────────────────────────────
echo "[2/4] 停止 MCP 服务..."
STOPPED=0
pkill -f "mcp_servers/cls_server.py" 2>/dev/null && STOPPED=1
pkill -f "mcp_servers/monitor_server.py" 2>/dev/null && STOPPED=1
if [ $STOPPED -eq 1 ]; then
    echo "[成功] MCP 服务已停止"
else
    echo "[信息] MCP 服务未在运行"
fi
echo ""

# ── Stop infrastructure containers ──────────────────────────
echo "[3/4] 停止开发基础设施容器..."
cd "$SCRIPT_DIR"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -Eq "milvus|biz-postgres|biz-redis"; then
    docker compose -f docker-compose.dev.yml down
    echo "[成功] 开发基础设施容器已停止"
else
    echo "[信息] 开发基础设施容器未运行"
fi
echo ""

# ── Clean PID/log files ─────────────────────────────────────
echo "[4/4] 清理临时文件..."
rm -f "$SCRIPT_DIR/mcp_cls.pid" "$SCRIPT_DIR/mcp_monitor.pid" "$SCRIPT_DIR/server.pid"
echo "[成功] 清理完成"
echo ""

echo "===================================="
echo "  所有服务已停止"
echo "===================================="
echo ""
echo "  如需完全清理 Docker 数据卷:"
echo "    docker compose -f docker-compose.dev.yml down -v"
echo ""
