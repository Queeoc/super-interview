# GitHub Repo Evidence Real Case Output

- Generated At: `2026-07-07T15:56:27`
- Repo URL: `https://github.com/Snailclimb/interview-guide`
- Question: `你刚才提到负责智能面试平台，请继续追问代码里是如何做异步编排、仓库检索和多轮追问生成的。`
- Answer: `我主要负责整体架构，把异步任务、知识库检索和面试追问链路串起来了。`
- Search Keywords: `Redis Stream, RedisService, StreamConsumer, InterviewQuestionService`
- Focus Topics: `Redis Stream, SpringAI, WebSocket, RAGService`
- Missing Signals: `具体代码入口, 异步消费链路, 真实工具调用过程, 关键类或方法`

## Resume Markdown

```md
# 项目经历
## InterviewGuide 智能 AI 面试平台
- 项目描述：负责智能简历分析、模拟面试、知识库检索增强和面试报告生成链路的架构设计与核心开发，重点实现 Redis Stream 异步处理、RAG 检索编排和 follow-up 追问生成。
- GitHub：https://github.com/Snailclimb/interview-guide
- 角色：后端 / Agent 基础设施负责人
- 亮点：MCP 工具集成、仓库检索、追问生成、异步任务编排
```

## Executor GitHub Arguments

```json
{
  "category_key": "PROJECT",
  "question_text": "你刚才提到负责智能面试平台，请继续追问代码里是如何做异步编排、仓库检索和多轮追问生成的。",
  "answer_text": "我主要负责整体架构，把异步任务、知识库检索和面试追问链路串起来了。",
  "focus_topics": [
    "Redis Stream",
    "SpringAI",
    "WebSocket",
    "RAGService"
  ],
  "missing_signals": [
    "具体代码入口",
    "异步消费链路",
    "真实工具调用过程",
    "关键类或方法"
  ],
  "search_keywords": [
    "Redis Stream",
    "RedisService",
    "StreamConsumer",
    "InterviewQuestionService"
  ],
  "keywords": [
    "Redis Stream",
    "RedisService",
    "StreamConsumer",
    "InterviewQuestionService"
  ],
  "top_k": 2
}
```

## latest_tool_context

```json
{
  "tool_name": "github_repo_evidence_tool",
  "tool_source": "local_tool",
  "decision_reason": "manual_script_test_formal_interview",
  "arguments": {
    "category_key": "PROJECT",
    "question_text": "你刚才提到负责智能面试平台，请继续追问代码里是如何做异步编排、仓库检索和多轮追问生成的。",
    "answer_text": "我主要负责整体架构，把异步任务、知识库检索和面试追问链路串起来了。",
    "focus_topics": [
      "Redis Stream",
      "SpringAI",
      "WebSocket",
      "RAGService"
    ],
    "missing_signals": [
      "具体代码入口",
      "异步消费链路",
      "真实工具调用过程",
      "关键类或方法"
    ],
    "search_keywords": [
      "Redis Stream",
      "RedisService",
      "StreamConsumer",
      "InterviewQuestionService"
    ],
    "keywords": [
      "Redis Stream",
      "RedisService",
      "StreamConsumer",
      "InterviewQuestionService"
    ],
    "top_k": 2,
    "resume_markdown": "# 项目经历\n## InterviewGuide 智能 AI 面试平台\n- 项目描述：负责智能简历分析、模拟面试、知识库检索增强和面试报告生成链路的架构设计与核心开发，重点实现 Redis Stream 异步处理、RAG 检索编排和 follow-up 追问生成。\n- GitHub：https://github.com/Snailclimb/interview-guide\n- 角色：后端 / Agent 基础设施负责人\n- 亮点：MCP 工具集成、仓库检索、追问生成、异步任务编排\n",
    "resume_metadata": {}
  },
  "success": true,
  "result": {
    "found": true,
    "repo_url": "https://github.com/Snailclimb/interview-guide",
    "repo_name": "Snailclimb/interview-guide",
    "matched_project_title": "InterviewGuide 智能 AI 面试平台",
    "matched_project_excerpt": "- 项目描述：负责智能简历分析、模拟面试、知识库检索增强和面试报告生成链路的架构设计与核心开发，重点实现 Redis Stream 异步处理、RAG 检索编排和 follow-up 追问生成。\n- GitHub：https://github.com/Snailclimb/interview-guide\n- 角色：后端 / Agent 基础设施负责人\n- 亮点：MCP 工具集成、仓库检索、追问生成、异步任务编排",
    "matched_files": [
      "app/src/main/java/interview/guide/modules/resume/listener/AnalyzeStreamConsumer.java",
      "app/src/main/java/interview/guide/common/async/AbstractStreamProducer.java"
    ],
    "items": [
      {
        "evidence_type": "code_snippet",
        "source_path": "app/src/main/java/interview/guide/modules/resume/listener/AnalyzeStreamConsumer.java",
        "heading": "redisService",
        "raw_excerpt": "            redisService().streamAdd(\n                AsyncTaskStreamConstants.RESUME_ANALYZE_STREAM_KEY,\n                message,\n                AsyncTaskStreamConstants.STREAM_MAX_LEN\n            );\n            log.info(\"简历分析任务已重新入队: resumeId={}, retryCount={}\", resumeId, retryCount);\n\n        } catch (Exception e) {\n            log.error(\"重试入队失败: resumeId={}, error={}\", resumeId, e.getMessage(), e);",
        "normalized_snippet": "redisService().streamAdd( AsyncTaskStreamConstants.RESUME_ANALYZE_STREAM_KEY, message, AsyncTaskStreamConstants.STREAM_MAX_LEN ); log.info(\"简历分析任务已重新入队: resumeId={}, retryCount={}\", resumeId, retryCount); } catch (Exception e) { log.error(\"重试入队失败: resumeId={},...",
        "matched_terms": [
          "RedisService",
          "StreamConsumer"
        ],
        "relevance_score": 2.85,
        "why_it_matched": "命中关键词：RedisService, StreamConsumer；符号：redisService；位置：app/src/main/java/interview/guide/modules/resume/listener/AnalyzeStreamConsumer.java:135-143",
        "language": "java",
        "start_line": 135,
        "end_line": 143
      },
      {
        "evidence_type": "code_snippet",
        "source_path": "app/src/main/java/interview/guide/common/async/AbstractStreamProducer.java",
        "heading": "sendTask",
        "raw_excerpt": "    protected void sendTask(T payload) {\n        try {\n            String messageId = redisService.streamAdd(\n                streamKey(),\n                buildMessage(payload),\n                AsyncTaskStreamConstants.STREAM_MAX_LEN\n            );\n            log.info(\"{}任务已发送到Stream: {}, messageId={}\",\n                taskDisplayName(), payloadIdentifier(payload), messageId);\n        } catch (Exception e) {\n            log.error(\"发送{}任务失败: {}, error={}\",\n                taskDisplayName(), payloadIdentifier(payload), e.getMessage(), e);\n            onSendFailed(payload, \"任务入队失败: \" + e.getMessage());\n        }\n    }",
        "normalized_snippet": "protected void sendTask(T payload) { try { String messageId = redisService.streamAdd( streamKey(), buildMessage(payload), AsyncTaskStreamConstants.STREAM_MAX_LEN ); log.info(\"{}任务已发送到Stream: {}, messageId={}\", taskDisplayName(), payloadIdentifier(payload), mes...",
        "matched_terms": [
          "Redis Stream",
          "RedisService"
        ],
        "relevance_score": 2.85,
        "why_it_matched": "命中关键词：Redis Stream, RedisService；符号：sendTask；位置：app/src/main/java/interview/guide/common/async/AbstractStreamProducer.java:22-37",
        "language": "java",
        "start_line": 22,
        "end_line": 37
      }
    ],
    "retrieval_reason": "github_code_search_matched"
  },
  "error_message": null
}
```

## Tool Summary

- Found: `True`
- Retrieval Reason: `github_code_search_matched`
- Repo Name: `Snailclimb/interview-guide`
- Matched Files: `app/src/main/java/interview/guide/modules/resume/listener/AnalyzeStreamConsumer.java, app/src/main/java/interview/guide/common/async/AbstractStreamProducer.java`
- Item Count: `2`

## Final follow_up_evidence_context Sent To LLM

```text
1. 文件：app/src/main/java/interview/guide/modules/resume/listener/AnalyzeStreamConsumer.java (135-143)
代码片段：
redisService().streamAdd(
                AsyncTaskStreamConstants.RESUME_ANALYZE_STREAM_KEY,
                message,
                AsyncTaskStreamConstants.STREAM_MAX_LEN
            );
            log.info("简历分析任务已重新入队: resumeId={}, retryCount={}", resumeId, retryCount);

        } catch (Exception e) {
            log.error("重试入队失败: resumeId={}, error={}", resumeId, e.getMessage(), e);
2. 文件：app/src/main/java/interview/guide/common/async/AbstractStreamProducer.java (22-37)
代码片段：
protected void sendTask(T payload) {
        try {
            String messageId = redisService.streamAdd(
                streamKey(),
                buildMessage(payload),
                AsyncTaskStreamConstants.STREAM_MAX_LEN
            );
            log.info("{}任务已发送到Stream: {}, messageId={}",
                taskDisplayName(), payloadIdentifier(payload), messageId);
        } catch (Exception e) {
            log.error("发送{}任务失败: {}, error={}",
                taskDisplayName(), payloadIdentifier(payload), e.getMessage(), e);
...
```
