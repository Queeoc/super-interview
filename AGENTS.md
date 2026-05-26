# AI Interview Platform 编码规范（Python 重构版）

面向 `biz_agent` 重构后的 AI 面试平台项目。默认技术栈为：

- Python 3.11+
- FastAPI
- LangChain + LangGraph
- SQLAlchemy 2.0 + PostgreSQL
- Redis
- Milvus
- Pydantic v2 / pydantic-settings
- React + TypeScript（独立前端）或 `static/` 过渡前端

写代码时必须遵守以下规则。

---

## 零、开发环境约定

本项目默认在 **WSL Ubuntu** 中开发和验证，Python 运行环境使用 WSL 内的 `conda biz_agent` 环境，Docker 相关服务也默认在 WSL 中启动和管理。

执行与验证时请遵守以下约定：

- 优先使用 WSL 侧的命令、解释器和依赖环境，不要混用 Windows 本机 Python / venv。
- 测试、依赖安装、Docker Compose、数据库初始化等操作都应以 WSL 环境为准。
- 如需运行项目命令，优先采用 `conda run -n biz_agent ...` 或先激活 `biz_agent` 环境后再执行。
- 如果出现 Windows 与 WSL 路径或依赖不一致，以 WSL 环境中的结果为准。

---

## 一、项目定位与结构

本仓库的目标不是继续堆叠“通用聊天页面”，而是基于现有 `biz_agent` 的 Agent / RAG / MCP 能力，重构为一个面向面试场景的 AI 原生应用。

当前仓库中已有的：

- `app/services/rag_agent_service.py`：通用对话 Agent
- `app/services/aiops_service.py`：Plan-Execute-Replan 工作流示例
- `app/services/vector_*`：Milvus 向量化与检索链路
- `app/agent/mcp_client.py`：MCP 工具接入

这些是**底层能力样板**，不是最终的面试业务分层。重构后建议采用如下目录：

```text
biz_agent/
├── app/
│   ├── main.py                        # FastAPI 入口 + lifespan
│   ├── config.py                      # Pydantic Settings
│   │
│   ├── api/                           # API 路由层
│   │   ├── interview.py               # 模拟面试
│   │   ├── knowledge.py               # 知识库管理与问答
│   │   ├── resume.py                  # 简历上传/分析
│   │   ├── skill.py                   # Skill 管理 / JD 解析
│   │   ├── provider.py                # 多模型 Provider 管理
│   │   └── health.py                  # 健康检查
│   │
│   ├── middleware/                    # 中间件
│   │   ├── rate_limit.py              # Redis Lua 滑动窗口限流
│   │   └── error_handler.py           # 全局异常处理
│   │
│   ├── services/                      # 业务编排层
│   │   ├── interview_service.py       # 面试会话编排
│   │   ├── question_service.py        # 出题服务
│   │   ├── evaluation_service.py      # 统一评估引擎
│   │   ├── skill_service.py           # Skill 加载与 JD 解析
│   │   ├── resume_service.py          # 简历解析与分析
│   │   ├── rag_service.py             # RAG 检索与问答
│   │   ├── provider_service.py        # Provider 运行时管理
│   │   ├── vector_store_manager.py    # 向量存储
│   │   ├── vector_index_service.py    # 上传入库
│   │   └── document_splitter_service.py
│   │
│   ├── agent/                         # Agent / Workflow 层
│   │   ├── interview/
│   │   │   ├── state.py               # InterviewState
│   │   │   ├── planner.py             # 出题计划
│   │   │   ├── executor.py            # 提问 / 追问 / 评估执行
│   │   │   ├── replanner.py           # 下一题 / 追问 / 结束决策
│   │   │   └── prompts.py
│   │   ├── evaluator/
│   │   │   ├── batch_evaluator.py     # 分批评估
│   │   │   └── summarizer.py          # 汇总
│   │   └── mcp_client.py              # MCP 客户端
│   │
│   ├── tools/                         # Agent Tool 层
│   │   ├── knowledge_tool.py          # 知识检索
│   │   ├── question_bank_tool.py      # 题库检索
│   │   ├── resume_tool.py             # 简历分析
│   │   ├── evaluation_tool.py         # 标准化评分
│   │   └── code_exec_tool.py          # 代码执行（可选）
│   │
│   ├── repositories/                  # 持久化访问层（推荐新增）
│   │   ├── interview_repository.py
│   │   ├── resume_repository.py
│   │   ├── knowledge_repository.py
│   │   └── provider_repository.py
│   │
│   ├── core/                          # 基础设施与技术组件
│   │   ├── llm_factory.py
│   │   ├── database.py
│   │   ├── redis_client.py
│   │   ├── milvus_client.py
│   │   ├── storage_client.py
│   │   └── structured_output.py
│   │
│   ├── models/                        # Pydantic / SQLAlchemy 模型
│   │   ├── interview.py
│   │   ├── resume.py
│   │   ├── knowledge.py
│   │   └── provider.py
│   │
│   └── utils/                         # 通用工具
│       ├── logger.py
│       ├── exceptions.py
│       └── prompt_security.py
│
├── skills/                            # 面试 Skill 定义（纯配置）
├── knowledge_base/                    # 题库 / 参考知识 / rubrics
├── prompts/                           # Prompt 模板
├── scripts/                           # Lua / 初始化脚本
├── tests/                             # pytest
├── static/                            # 过渡期静态前端
└── frontend/                          # 独立 React 前端（如引入）
```

### 重构约束

- 面试业务逻辑必须放到新领域模块，不要继续堆在 `aiops_service.py` 或 `agent/aiops/` 中。
- `agent/aiops/` 可以作为 LangGraph 工作流样例保留，但**禁止**把面试业务继续写进 AIOps 节点。
- 新增面试功能时，优先新增 `app/agent/interview/`、`app/services/interview_service.py`、`app/api/interview.py`。

---

## 二、分层架构与依赖方向

推荐的依赖方向：

```text
API Router
  ↓
Service
  ↓
Agent / Workflow
  ↓
Tools / Repositories / Core
  ↓
Redis / PostgreSQL / Milvus / S3 / External LLM / MCP
```

允许的横向协作：

- `Service` 可调用 `Repository`、`Core`、`Agent`
- `Agent` 可调用 `Tool`
- `Tool` 可调用 `Service` 或 `Core`，但必须保持粒度小、职责单一

禁止的依赖方向：

- `api/` 直接访问数据库、Milvus、Redis
- `agent/` 直接写 SQLAlchemy Session
- `tools/` 包含复杂业务编排
- `models/` 反向依赖 `services/`

### API 层

- 仅负责路由、鉴权、参数校验、返回格式与 SSE 输出
- 禁止在 Router 中写业务逻辑、Prompt 拼接、数据库访问
- 所有长耗时 AI 任务都必须通过 Service 封装
- SSE 事件格式必须稳定，不能在前端未适配时随意变更字段名

### Service 层

- 负责业务编排，是领域逻辑的主入口
- 一个 Service 只负责一个清晰的业务职责
- 大服务要拆分，例如：
  - `resume_service.py`
  - `resume_parse_service.py`
  - `resume_analysis_service.py`
  - `resume_persistence_service.py`
- 所有跨组件操作必须优先在 Service 层协调，不要散落到 Router / Tool / Model

### Agent / Workflow 层

- 负责 LangGraph 状态机、节点编排和 Agent 决策
- 只处理“推理、计划、决策、状态流转”
- 不负责 HTTP、数据库事务、文件上传
- 一个状态图只做一类业务：面试流程、评估流程、异步复盘流程各自独立

### Repository / Persistence 层

- 负责 PostgreSQL 持久化访问
- 推荐引入 `repositories/`，集中封装 SQLAlchemy 查询
- 禁止在 `api/`、`agent/`、`tools/` 中直接操作 ORM Session
- 复杂查询写成 Repository 方法，不要把查询条件散落在多个 Service 中

### Core / Infrastructure 层

- 负责数据库连接、Redis 客户端、Milvus 客户端、对象存储、LLM 工厂、结构化输出组件
- 所有第三方 SDK 的接入点必须集中在 `core/`
- 禁止在业务代码中散落 `os.getenv()`、SDK 初始化和连接细节

---

## 三、命名与模型后缀规则

| 后缀 | 用途 | 示例 |
|------|------|------|
| `XxxEntity` | SQLAlchemy 持久化模型 | `InterviewSessionEntity` |
| `XxxDTO` | 跨层传输对象 | `InterviewReportDTO` |
| `XxxRequest` | API 请求体 | `CreateInterviewRequest` |
| `XxxResponse` | API 响应体 | `SubmitAnswerResponse` |
| `XxxState` | LangGraph 状态模型 | `InterviewState` |
| `XxxService` | 业务服务 | `InterviewService` |
| `XxxRepository` | 持久化访问 | `ResumeRepository` |
| `XxxTool` / `xxx_tool` | Agent 工具 | `EvaluationTool` / `evaluation_tool.py` |
| `XxxSettings` | 配置对象 | `LlmSettings` |

### Python 代码风格

- 类名使用 `UpperCamelCase`
- 函数、方法、变量使用 `snake_case`
- 常量使用 `UPPER_SNAKE_CASE`
- 模块名使用 `snake_case.py`
- 所有新代码优先补充类型注解
- 注释、Docstring、必要的代码说明尽可能使用中文编写；仅在第三方协议、标准字段名、专业术语不宜翻译时保留英文
- 非必要不要使用缩写，除非是通用术语：`llm`, `rag`, `mcp`, `dto`

### 模型约束

- Pydantic 负责请求响应模型与结构化输出模型
- SQLAlchemy 负责持久化实体
- **禁止直接把 SQLAlchemy Entity 返回给前端**
- Entity 与 Response/DTO 之间必须显式转换

---

## 四、异常、错误码与响应规范

### 异常规则

- 所有业务异常统一继承自自定义业务异常，例如 `BusinessException`
- 禁止在业务层直接抛裸 `Exception` 或 `RuntimeError` 作为业务信号
- `HTTPException` 只允许出现在 API 层，且主要用于协议层语义错误
- 业务层只抛业务异常，由全局异常处理中间件统一映射

### 错误码分域建议

| 域 | 范围 | 示例 |
|----|------|------|
| 通用 | 1xxx | BAD_REQUEST |
| 简历 | 2xxx | RESUME_NOT_FOUND |
| 面试 | 3xxx | INTERVIEW_SESSION_NOT_FOUND |
| 存储 | 4xxx | STORAGE_UPLOAD_FAILED |
| 导出 | 5xxx | EXPORT_PDF_FAILED |
| 知识库 | 6xxx | KNOWLEDGE_BASE_NOT_FOUND |
| AI 服务 | 7xxx | AI_SERVICE_TIMEOUT |
| 限流 | 8xxx | RATE_LIMIT_EXCEEDED |
| 日程 | 9xxx | INTERVIEW_SCHEDULE_NOT_FOUND |
| 语音面试 | 10xxx | VOICE_SESSION_NOT_FOUND |

### 响应规范

- 普通 JSON 接口应统一返回固定响应结构
- SSE 接口必须统一事件类型，例如：
  - `status`
  - `content`
  - `tool_call`
  - `plan`
  - `step_complete`
  - `report`
  - `done`
  - `error`
- 事件字段一旦公开给前端，不可随意重命名

---

## 五、Skill、Prompt 与 Agent 规则

### Skill 规则

- Skill 必须以配置驱动，目录格式：

```text
skills/{skill_id}/
├── SKILL.md
└── skill.meta.yml
```

- `SKILL.md` 负责定义面试官 Persona、提问风格、追问原则
- `skill.meta.yml` 负责定义展示信息、分类、参考知识、工具声明
- 新增面试方向时，优先通过新增 Skill 完成，**不要**修改核心业务代码

### Prompt 规则

- 所有 Prompt 必须放在 `prompts/` 下，禁止在 Service 中写大段硬编码 Prompt
- Prompt 文件与业务职责对应，例如：
  - `prompts/interview/planner_system.st`
  - `prompts/interview/executor_system.st`
  - `prompts/interview/replanner_system.st`
  - `prompts/evaluation/batch_evaluator.st`
- Prompt 变量必须明确、可测试，不要拼接未清洗的用户输入

### Agent 规则

- 面试主流程使用 `Plan-Execute-Replan` 或等价状态图
- RAG 是 Agent 的知识底座，不是单独外挂的流程分支
- Tool 是 Agent 的唯一外部能力入口，不要在节点中绕过 Tool 直接写临时逻辑
- 单个节点必须保持“单决策职责”，例如：
  - `planner` 只做出题计划
  - `executor` 只做提问/追问/评估执行
  - `replanner` 只决定下一步

---

## 六、RAG 与知识库规则

RAG 必须拆成清晰链路：

```text
文件校验 → 文本解析 → 文档清洗 → 分块 → 向量化 → 入库 → 检索 → 组装上下文 → 回答
```

### 文档处理

- 文档解析、切分、向量化必须拆分为独立服务
- Markdown / TXT / PDF / DOCX 解析逻辑不能揉成一个大函数
- 所有文档入库都必须带来源元数据，例如：
  - `_source`
  - `_file_name`
  - `_extension`
  - `knowledge_base_id`
  - `category`

### 检索规则

- 检索逻辑优先封装为 Tool 或 RAG Service
- 检索参数可以动态调整，例如 TopK、最小相似度阈值
- 如果引入 query rewrite，必须放在 `rag_service.py` 或独立 `query_rewrite` 组件中
- 没有命中结果时要返回稳定兜底文案，不能让模型自由编造

### 向量存储规则

- Milvus 只负责向量检索，不负责结构化业务数据
- 会话、简历、报告、Provider 配置等必须落 PostgreSQL
- 禁止把大段业务 JSON 当作“唯一事实源”只存到向量库里

---

## 七、数据库、事务与持久化规则

### 数据职责划分

- PostgreSQL：结构化业务数据
- Redis：缓存、限流、会话状态、异步任务队列
- Milvus：向量索引与检索
- S3/MinIO：原始文件、导出文件

### SQLAlchemy 规则

- 推荐使用 SQLAlchemy 2.0 async session
- 所有数据库访问通过 Repository 或 Persistence Service 封装
- 禁止在 Router 中直接 `session.execute(...)`
- 禁止在 LangGraph 节点里直接操作 DB Session

### 事务规则

- 事务边界放在 Service 层
- 事务内禁止执行外部长耗时调用：
  - LLM 调用
  - S3 上传
  - MCP 远程工具调用
  - 大文件解析
- 保持事务范围尽可能小
- 生产环境禁止依赖 `create_all()` 自动建表；需要迁移脚本工具时，优先引入 Alembic

---

## 八、Redis、异步任务与限流规则

### Redis 使用场景

- 面试会话缓存
- 简历分析任务状态
- 面试评估异步任务
- API 限流
- 短期对话态 / 幂等状态

### 异步任务规则

- 异步任务统一通过 Redis Stream、`arq`、Celery 等机制实现
- 同一类任务必须有统一的 Producer / Consumer 模板
- 建议场景：
  - 简历分析
  - 知识库向量化
  - 面试评估
  - PDF 导出
- 失败任务必须有：
  - 最大重试次数
  - 状态持久化
  - 最终失败标记
  - 可读错误信息

### 限流规则

- 限流统一放在 `middleware/rate_limit.py`
- 使用 Redis Lua 实现滑动窗口或令牌桶时，脚本文件放 `scripts/`
- 维度建议支持：
  - `GLOBAL`
  - `IP`
  - `USER`
- 限流失败必须返回稳定错误码和文案，不能抛未处理异常

---

## 九、LLM Provider 与结构化输出规则

### Provider 规则

- 所有模型初始化必须走统一工厂或注册表，例如 `llm_factory.py`
- 禁止在 Service 中散落创建 `ChatOpenAI(...)`
- 必须支持运行时切换默认 Provider / 模型
- Provider 配置应支持持久化与安全存储

### 结构化输出规则

- 所有依赖 LLM 返回 JSON 的场景都必须经过统一包装器
- 包装器至少应支持：
  - 输出解析
  - 失败重试
  - 严格 JSON 提示注入
  - 本地 JSON 修复
  - 指标埋点
- 禁止每个 Service 各自手写一套 JSON 重试逻辑

### Prompt 安全

- 用户输入、JD、简历、知识库上下文都必须经过显式边界包裹或清洗
- 不得将用户原始输入直接拼入 system prompt 而无边界控制
- 对外部文档内容默认按“不可信输入”处理

---

## 十、文件、简历与导出规则

### 文件上传

- 文件类型白名单必须显式定义
- 文件大小上限必须显式定义
- 文件名要做清洗，禁止直接信任客户端文件名
- 上传成功不代表分析成功，文件存储与异步分析状态要分开表达

### 简历处理

- 简历上传、文本解析、结构化分析、持久化、去重必须拆层
- 支持重复简历检测时，应优先用内容哈希或稳定指纹
- 简历分析建议异步执行，返回 `PENDING / PROCESSING / COMPLETED / FAILED`

### 导出规则

- PDF / 报告导出必须通过独立服务封装
- 导出逻辑禁止散落在 Controller 或 Service 主流程中
- 中文字体、模板、页眉页脚等资源集中管理

---

## 十一、日志与可观测性规范

- 使用 `loguru`，禁止 `print()`
- 日志必须带业务上下文，例如：
  - `session_id`
  - `resume_id`
  - `knowledge_base_id`
  - `skill_id`
  - `provider`
- 结构化记录优先，不要只打印自然语言长句
- 记录异常时必须保留堆栈，不要只记 `str(e)`

推荐格式：

```python
logger.info("interview session created", session_id=session_id, skill_id=skill_id)
logger.exception("evaluation failed", session_id=session_id)
```

必须记录的关键链路：

- 上传文件
- 文档解析
- 向量化入库
- 面试会话创建
- 问题生成
- 评估执行
- Provider 切换
- Redis Stream 消费失败

---

## 十二、配置管理规则

- 所有配置统一放在 `app/config.py` 或拆分后的 settings 模块中
- 使用 `pydantic-settings` 统一管理
- 禁止在业务代码里散落 `os.getenv()` / `dotenv.load_dotenv()`
- 敏感信息只允许放 `.env` 或外部安全配置中
- 不要在代码里硬编码：
  - API Key
  - Base URL
  - Bucket 名
  - 数据库密码
  - Redis 密码

配置应分层：

- App 基础配置
- LLM 配置
- Milvus 配置
- PostgreSQL 配置
- Redis 配置
- 存储配置
- Interview 配置
- RAG / Query Rewrite 配置

---

## 十三、测试规则

- 使用 `pytest` + `pytest-asyncio`
- 新增 Service、Tool、核心工作流必须补单测
- API 层至少补关键 happy path 和异常 path
- LangGraph 工作流至少验证：
  - 起始状态
  - 节点跳转
  - 结束条件
  - 重规划路径
- 结构化输出组件必须覆盖解析失败和修复重试测试
- RAG 检索组件必须覆盖空命中、正常命中、参数调整逻辑

### 测试分类建议

- 单元测试：纯函数、Service、Tool、Prompt 变量装配
- 集成测试：API、Repository、Redis/Milvus 适配层
- 工作流测试：LangGraph 状态流转

---

## 十四、前端与接口协作规则

- 当前若保留 `static/` 过渡前端，后端仍要按正式 API 契约设计
- 若引入独立 `frontend/` React 工程：
  - 不在后端模板里写业务 UI
  - API 契约先于页面实现
  - SSE 事件结构必须文档化
- 前端展示逻辑不要反向驱动后端模型命名
- 后端禁止为了单一页面临时加不一致字段

---

## 十五、通用软件开发原则

- 单一职责优先：一个模块只做一件事
- 明确边界优先：业务编排、模型推理、存储访问分开
- 配置优于硬编码
- 组合优于复制粘贴
- 显式优于隐式
- 可测试优于“一次跑通”
- 兼容演进优于一次性大爆改

### 重构期特别要求

- 在迁移完成前，允许旧模块与新模块并存，但边界必须清楚
- 旧实现只允许复用底层能力，不允许继续往旧业务入口堆新逻辑
- 新功能默认写到目标架构目录，不要把“临时方案”做成长期债务

---

## 速查：禁止清单

| 禁止项 | 原因 |
|--------|------|
| 在 `api/` 里写业务逻辑 | 破坏分层 |
| 在 `agent/` 节点中直接操作数据库 | 推理层与持久化耦合 |
| 在 Service 中散落创建 LLM 客户端 | Provider 管理失控 |
| 直接返回 SQLAlchemy Entity 给前端 | 暴露内部结构 |
| 用裸 `Exception` 表达业务错误 | 错误语义不清 |
| 在事务内调用 LLM / S3 / MCP | 长事务占用资源 |
| 把 Prompt 大段硬编码在 Python 逻辑里 | 难维护、难测试 |
| 为了赶进度把新面试逻辑继续写进 `aiops` 模块 | 技术债继续扩散 |
| 在生产链路只依赖 `MemorySaver` 保存会话 | 无法持久化、不可扩展 |
| 使用 `localStorage` 作为唯一历史事实源 | 数据不可靠 |
| `print()` 调试生产逻辑 | 不可观测 |
| 硬编码密钥 / URL / 模型名 | 安全与可维护性差 |
| 未做文件白名单与大小校验直接上传 | 安全风险 |
| 每个业务点都手写一套结构化输出重试 | 重复造轮子 |
