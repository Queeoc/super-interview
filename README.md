# super-interview

面向面试场景的 AI Agent 平台，基于原 `biz_agent` 的通用 Agent / RAG / MCP 能力演进而来，当前聚焦于 AI 面试、简历分析、技能管理、知识库问答和 Provider 管理。

## 核心能力

- AI 面试：基于 LangGraph 的面试编排、追问、评估与总结
- 简历模块：上传、解析、结构化分析与持久化
- Skill 系统：面试方向配置化管理
- RAG 知识库：文档上传、切分、向量化、检索与问答
- MCP 集成：保留可复用的外部工具接入能力
- Provider 管理：统一模型配置、切换与运行时注册

## 技术栈

- Python 3.11+
- FastAPI
- LangChain + LangGraph
- SQLAlchemy 2.0 + PostgreSQL
- Redis
- Milvus
- Pydantic v2 / pydantic-settings
- React + TypeScript 前端

## 快速开始

项目默认在 WSL Ubuntu 中开发和验证，推荐使用 `conda biz_agent` 环境。

### 1. 安装依赖

```bash
conda activate biz_agent
pip install -e .
```

如需开发依赖：

```bash
conda activate biz_agent
pip install -e ".[dev]"
```

### 2. 准备配置

```bash
cp .env.example .env
```

按需填写：

- `DASHSCOPE_API_KEY`
- PostgreSQL / Redis / Milvus / 存储配置

### 3. 启动基础设施

```bash
docker compose -f docker-compose.dev.yml up -d
```

或仅启动向量相关依赖：

```bash
docker compose -f vector-database.yml up -d
```

### 4. 启动服务

WSL:

```bash
./start-wsl.sh
```

Windows:

```powershell
.\start-windows.bat
```

也可以直接运行后端：

```bash
conda run -n biz_agent python -m uvicorn app.main:app --host 0.0.0.0 --port 9900
```

## 主要接口

- `GET /health`
- `POST /api/interview/...`
- `POST /api/resume/...`
- `POST /api/knowledge/...`
- `POST /api/providers/...`
- `POST /api/chat`
- `POST /api/chat_stream`
- `POST /api/upload`

API 文档：

- `http://localhost:9900/docs`

## 文档与知识内容

默认可用于上传和初始化的知识内容位于：

- `knowledge_base/`
- `knowledge_base/rubrics/`

项目保留了通用 RAG、向量化、MCP 服务与对话能力，便于继续复用到底层面试业务中。

## 当前目录说明

- `app/`：后端核心代码
- `frontend/`：独立前端工程
- `skills/`：面试技能配置
- `knowledge_base/`：知识库与 rubric 内容
- `mcp_servers/`：MCP 服务
- `tests/`：pytest 测试

## 说明

仓库已移除旧的运维诊断业务代码与静态前端页面，仅保留面试相关业务代码和可复用通用能力。
