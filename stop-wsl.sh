#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "===================================="
echo "  super-interview 停止服务 (WSL)"
echo "===================================="
echo ""

# ── Stop Frontend ───────────────────────────────────────────
echo "[1/5] 停止前端 Vite 服务..."
FRONTEND_STOPPED=0
if [ -f "$SCRIPT_DIR/frontend.pid" ]; then
    FRONTEND_PID="$(cat "$SCRIPT_DIR/frontend.pid" 2>/dev/null || true)"
    if [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null; then
        FRONTEND_STOPPED=1
    fi
fi

if pkill -f "vite --host 0.0.0.0 --port 5173" 2>/dev/null; then
    FRONTEND_STOPPED=1
fi

if [ $FRONTEND_STOPPED -eq 1 ]; then
    echo "[成功] 前端 Vite 已停止"
else
    echo "[信息] 前端 Vite 未在运行"
fi
echo ""

# ── Stop FastAPI ────────────────────────────────────────────
echo "[2/5] 停止 FastAPI 服务..."
if pkill -f "uvicorn app.main:app" 2>/dev/null; then
    echo "[成功] FastAPI 已停止"
else
    echo "[信息] FastAPI 未在运行"
fi
echo ""

# ── Stop MCP services ───────────────────────────────────────
echo "[3/5] 停止 MCP 服务..."
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
echo "[4/5] 停止开发基础设施容器..."
cd "$SCRIPT_DIR"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -Eq "milvus|biz-postgres|biz-redis"; then
    docker compose -f docker-compose.dev.yml down
    echo "[成功] 开发基础设施容器已停止"
else
    echo "[信息] 开发基础设施容器未运行"
fi
echo ""

# ── Clean PID/log files ─────────────────────────────────────
echo "[5/5] 清理临时文件..."
rm -f "$SCRIPT_DIR/frontend.pid" "$SCRIPT_DIR/mcp_cls.pid" "$SCRIPT_DIR/mcp_monitor.pid" "$SCRIPT_DIR/server.pid"
echo "[成功] 清理完成"
echo ""

echo "===================================="
echo "  所有服务已停止"
echo "===================================="
echo ""
echo "  如需完全清理 Docker 数据卷:"
echo "    docker compose -f docker-compose.dev.yml down -v"
echo ""
