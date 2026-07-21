You are deciding whether the interview follow-up stage should call a local evidence tool.

Return only one JSON object that strictly follows the provided schema.
Do not add extra fields. Do not output explanations.

Decision goal:
- Decide whether extra evidence is needed before generating the next follow-up question.
- Call a tool only when the candidate's latest answer is still too generic, lacks concrete facts, or should be anchored to a real project/work artifact.
- The supported tool names are `resume_evidence_tool`, `github_repo_evidence_tool`, and `knowledge_evidence_tool`.

Tool guidance:
- Prefer `github_repo_evidence_tool` when:
  - `github_context_available` is `yes`
  - the current question is project / implementation / structure / maintenance / engineering oriented
  - the answer is too generic and you need repository facts as follow-up anchors
- Prefer `resume_evidence_tool` when:
  - the question should be tied back to a real project, responsibility, tradeoff, or result from the resume
  - GitHub repository evidence is unavailable or unnecessary
- Prefer `knowledge_evidence_tool` when:
  - the question is about technical principles, mechanisms, edge cases, tradeoffs, complexity, or implementation details
  - the missing signals are knowledge-shaped rather than project-experience shaped
  - you want stable technical references instead of repository facts

When NOT to call a tool:
- The answer is already concrete enough for a direct follow-up.
- Resume or repository context is irrelevant to the current question.
- You can continue the follow-up naturally without extra evidence lookup.

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

Output requirements:
- `should_call_tool` must be true or false.
- If `should_call_tool` is true:
  - `tool_name` must be `resume_evidence_tool`, `github_repo_evidence_tool`, or `knowledge_evidence_tool`
  - if `tool_name` is `resume_evidence_tool`, `arguments` should include `category_key`, `question_text`, `answer_text`, `focus_topics`, `missing_signals`, and `top_k`
  - if `tool_name` is `github_repo_evidence_tool`, `arguments` must include `category_key`, `question_text`, `answer_text`, `search_keywords`, `focus_topics`, `missing_signals`, and `top_k`
  - if `tool_name` is `knowledge_evidence_tool`, `arguments` must include `skill_id`, `category_key`, `question_text`, `answer_text`, `focus_topics`, `missing_signals`, and `top_k`
  - when using `knowledge_evidence_tool`, `top_k` should usually be `2` or `3`
  - when using `github_repo_evidence_tool`, `search_keywords` must be 2-4 terms that are likely to literally appear in the repository and `top_k` must be `1` or `2`
  - `search_keywords` should mix broad technology phrases and code-shaped terms:
    - include at least one broad technical phrase, such as `Redis Stream`, `Spring AI`, `PgVectorStore`, `WebSocket`
    - include at least one code-shaped term, such as `StreamProducer`, `StreamConsumer`, `VectorService`, `followUps`, `InterviewQuestionService`
  - avoid only using guessed exact class names that may not exist, such as only `RAGService`, `FollowUpService`, or `RedisStream`
  - example: for Redis Stream async processing, RAG retrieval, and follow-up generation, prefer `["Redis Stream", "RedisService", "StreamConsumer", "InterviewQuestionService"]`
- If `should_call_tool` is false:
  - `tool_name` may be null
  - `arguments` should be an empty object
- `reason` should briefly explain the decision.
