你需要判断面试追问阶段是否应该先调用本地证据工具。

Return only a JSON object.
请只返回一个严格符合 schema 的 JSON 对象。
不要添加额外字段，不要输出解释文字。

当前阶段：
- follow-up evidence preparation
- 你的任务是在生成下一句追问前，判断是否需要调用一个可用证据工具。

核心规则：
- 只能从 Available tools 中选择工具。
- 不要选择 Available tools 以外的工具，即使它看起来适合。
- 如果 Available tools 为空，必须返回 `should_call_tool=false`。
- 如果需要证据但没有合适工具，也返回 `should_call_tool=false`，由后续追问 prompt 自然生成追问。
- 只有当候选人最新回答仍然过于笼统、缺少具体事实，或需要锚定外部证据时，才调用工具。

Available tools:
{available_tools}

Available tool guidance:
{available_tool_guidance}

工具用途参考：
- `resume_evidence_tool`：只在它出现在 Available tools 时可选，用于结合候选人简历中的真实项目、职责、权衡或结果。
- `github_repo_evidence_tool`：只在它出现在 Available tools 时可选，用于结合候选人 GitHub 仓库中的真实代码、README 或项目结构证据。
- `knowledge_evidence_tool`：只在它出现在 Available tools 时可选，用于技术原理、机制、边界条件、复杂度、实现细节、生产问题处理方法和常见坑。

不要调用工具的情况：
- 回答已经足够具体，可以直接生成追问。
- Available tools 中没有适合当前问题的工具。
- 不需要额外检索也能自然继续追问。

Current question:
{current_question}

Category key:
{category_key}

Question intent:
{question_intent}

Preferred follow-up focus:
{follow_up_focus}

Missing signals:
{missing_signals}

Latest observation:
{latest_observation}

User answer:
{user_answer}

Resume Context:
{resume_markdown}

Resume Metadata:
{resume_metadata}

Current skill id:
{skill_id}

GitHub context available:
{github_context_available}

输出要求：
- `should_call_tool` 必须是 true 或 false。
- 如果 `should_call_tool` 为 true：
  - `tool_name` 必须是 Available tools 中列出的一个工具。
  - 如果 `tool_name` 是 `resume_evidence_tool`，`arguments` 应包含 `category_key`、`question_text`、`answer_text`、`focus_topics`、`missing_signals`、`top_k`。
  - 如果 `tool_name` 是 `github_repo_evidence_tool`，`arguments` 必须包含 `category_key`、`question_text`、`answer_text`、`search_keywords`、`focus_topics`、`missing_signals`、`top_k`。
  - 如果 `tool_name` 是 `knowledge_evidence_tool`，`arguments` 必须包含 `skill_id`、`category_key`、`question_text`、`answer_text`、`focus_topics`、`missing_signals`、`top_k`。
  - 使用 `knowledge_evidence_tool` 时，`top_k` 通常为 `2` 或 `3`。
  - 使用 `github_repo_evidence_tool` 时，`search_keywords` 必须是 2-4 个可能在仓库中真实出现的词，`top_k` 必须是 `1` 或 `2`。
  - `search_keywords` 应混合宽泛技术短语和代码形态词：
    - 至少包含一个宽泛技术短语，例如 `Redis Stream`、`Spring AI`、`PgVectorStore`、`WebSocket`
    - 至少包含一个代码形态词，例如 `StreamProducer`、`StreamConsumer`、`VectorService`、`followUps`、`InterviewQuestionService`
  - 避免只使用猜测出来的精确类名，例如只写 `RAGService`、`FollowUpService` 或 `RedisStream`。
  - 示例：如果要搜索 Redis Stream 异步处理、RAG 检索和追问生成，优先使用 `["Redis Stream", "RedisService", "StreamConsumer", "InterviewQuestionService"]`。
- 如果 `should_call_tool` 为 false：
  - `tool_name` 可以是 null。
  - `arguments` 应为空对象。
- `reason` 应简要说明决策原因。
- 字段名、工具名、枚举值、JSON 结构不要翻译。
