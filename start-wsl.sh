#!/usr/bin/env bash
set -e

CONDA_ENV_NAME="biz_agent"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WSL_IP=$(ip addr show eth0 2>/dev/null | grep "inet\b" | awk '{print $2}' | cut -d/ -f1)

echo "===================================="
echo "  super-interview 服务启动 (WSL)"
echo "===================================="
echo ""

FRONTEND_PORT=5173

# ── Step 1: Activate conda ──────────────────────────────────
echo "[1/6] 激活 Conda 环境..."

# Locate conda
if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/anaconda3/etc/profile.d/conda.sh"
elif [ -f "/opt/miniconda3/etc/profile.d/conda.sh" ]; then
    source "/opt/miniconda3/etc/profile.d/conda.sh"
else
    echo "[错误] 未找到 conda，请先安装 Miniconda"
    exit 1
fi

conda activate "$CONDA_ENV_NAME" 2>/dev/null || {
    echo "[错误] conda 环境 '$CONDA_ENV_NAME' 不存在"
    echo "        conda create -n $CONDA_ENV_NAME python=3.11 -y"
    echo "        conda activate $CONDA_ENV_NAME"
    echo "        pip install -e ."
    exit 1
}
echo "[成功] Conda 环境已激活: $CONDA_ENV_NAME"
echo ""

# ── Step 2: Start Docker ────────────────────────────────────
echo "[2/6] 检查 Docker..."

if ! docker info >/dev/null 2>&1; then
    echo "[信息] Docker 未运行，尝试启动..."
    sudo service docker start 2>/dev/null || {
        echo "[错误] 无法启动 Docker，请确认 Docker 已安装"
        echo "        sudo apt install docker.io docker-compose-v2"
        exit 1
    }
    sleep 2
fi
echo "[成功] Docker 运行中"
echo ""

# ── Step 3: Start infrastructure stack ──────────────────────
echo "[3/6] 启动开发基础设施 (PostgreSQL / Redis / Milvus)..."

cd "$SCRIPT_DIR"

# 将 Docker volumes 存放在 WSL 原生文件系统中，避免 Windows NTFS 挂载的权限问题
# 默认使用 WSL 原生文件系统（$HOME 在 WSL 内为 /home/xxx），避免 NTFS 挂载的 chmod 权限问题
export DOCKER_VOLUME_DIRECTORY="${HOME}/.biz-agent-docker"

if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "milvus-standalone" \
    && docker ps --format '{{.Names}}' 2>/dev/null | grep -q "biz-postgres" \
    && docker ps --format '{{.Names}}' 2>/dev/null | grep -q "biz-redis"; then
    echo "[信息] 开发基础设施已在运行"
else
    docker compose -f docker-compose.dev.yml up -d
    echo "[信息] 等待基础设施启动 (12秒)..."
    sleep 12
fi
echo "[成功] 开发基础设施就绪"
echo ""

# ── Step 4: Start MCP services ──────────────────────────────
echo "[4/6] 启动 MCP 服务..."

# CLS MCP
if pgrep -f "mcp_servers/cls_server.py" >/dev/null 2>&1; then
    echo "[信息] CLS MCP 已在运行"
else
    nohup python mcp_servers/cls_server.py > mcp_cls.log 2>&1 &
    echo "[成功] CLS MCP 已启动 (PID: $!)"
fi

# Monitor MCP
if pgrep -f "mcp_servers/monitor_server.py" >/dev/null 2>&1; then
    echo "[信息] Monitor MCP 已在运行"
else
    nohup python mcp_servers/monitor_server.py > mcp_monitor.log 2>&1 &
    echo "[成功] Monitor MCP 已启动 (PID: $!)"
fi
echo ""

# ── Step 5: Start FastAPI ───────────────────────────────────
echo "[5/6] 启动 FastAPI 服务..."

# Kill old uvicorn if running
pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 1

nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 9900 > server.log 2>&1 &
FASTAPI_PID=$!
echo "[成功] FastAPI 已启动 (PID: $FASTAPI_PID)"
echo "[信息] 等待服务就绪..."

# Wait for health check
for i in $(seq 1 30); do
    if curl -s http://localhost:9900/health >/dev/null 2>&1; then
        echo "[成功] FastAPI 服务运行正常"
        break
    fi
    sleep 1
done
echo ""

# ── Step 6: Start Frontend ──────────────────────────────────
echo "[6/6] 启动前端 Vite 服务..."

if [ ! -f "$SCRIPT_DIR/frontend/package.json" ]; then
    echo "[错误] 未找到 frontend/package.json，无法启动前端"
    exit 1
fi

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo "[错误] 未找到 node/npm，请先在 WSL 中安装前端运行环境"
    exit 1
fi

if [ ! -d "$SCRIPT_DIR/frontend/node_modules" ]; then
    echo "[错误] 未找到 frontend/node_modules，请先执行:"
    echo "        cd frontend && npm install"
    exit 1
fi

if pgrep -f "vite --host 0.0.0.0 --port ${FRONTEND_PORT}" >/dev/null 2>&1; then
    echo "[信息] 前端 Vite 服务已在运行"
else
    rm -f "$SCRIPT_DIR/frontend.pid"
    (
        cd "$SCRIPT_DIR/frontend"
        nohup npm run dev -- --host 0.0.0.0 --port "${FRONTEND_PORT}" > "$SCRIPT_DIR/frontend.log" 2>&1 &
        echo $! > "$SCRIPT_DIR/frontend.pid"
    )
    echo "[成功] 前端 Vite 已启动 (PID: $(cat "$SCRIPT_DIR/frontend.pid"))"
    echo "[信息] 等待前端服务就绪..."

    for i in $(seq 1 30); do
        if curl -s "http://localhost:${FRONTEND_PORT}" >/dev/null 2>&1; then
            echo "[成功] 前端服务运行正常"
            break
        fi
        sleep 1
    done
fi
echo ""

# ── Upload docs ─────────────────────────────────────────────
# echo "[上传] 上传文档到向量数据库..."
# for f in aiops-docs/*.md; do
#     if [ -f "$f" ]; then
#         printf "  上传: %s\n" "$(basename "$f")"
#         curl -s -X POST http://localhost:9900/api/upload -F "file=@$f" >/dev/null
#         sleep 1
#     fi
# done
# echo "[成功] 文档上传完成"
# echo ""

# ── Done ────────────────────────────────────────────────────
echo "===================================="
echo "  服务启动完成！"
echo "===================================="
echo ""
echo "  Windows 访问地址:"
echo "    Frontend: http://${WSL_IP}:${FRONTEND_PORT}"
echo "    Web:     http://${WSL_IP}:9900"
echo "    API文档: http://${WSL_IP}:9900/docs"
echo "    Milvus:  http://${WSL_IP}:8000"
echo "    Redis:   ${WSL_IP}:6379"
echo "    Postgres:${WSL_IP}:5432"
echo ""
echo "  WSL 内前端: http://localhost:${FRONTEND_PORT}"
echo "  WSL 内访问: http://localhost:9900"
echo "  Conda env:  conda activate $CONDA_ENV_NAME"
echo ""
echo "  日志:"
echo "    Frontend: tail -f frontend.log"
echo "    FastAPI : tail -f server.log"
echo "    CLS MCP : tail -f mcp_cls.log"
echo "    Monitor : tail -f mcp_monitor.log"
echo ""
echo "  停止服务: ./stop-wsl.sh"
echo "===================================="
