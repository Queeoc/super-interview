import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';

import { InterviewSessionWorkspace } from './InterviewSessionWorkspace';
import { createMutationResult, createQuerySuccessResult } from '../../test/fixtures/queryMocks';
import { renderWithProviders } from '../../test/testUtils';
import * as interviewHooks from './useInterview';
import * as interviewApi from '../../services/api/modules/interview.api';

describe('InterviewSessionWorkspace', () => {
  test('renders chat history, fills last draft answer, and hides report before completion', async () => {
    vi.spyOn(interviewHooks, 'useInterviewSession').mockReturnValue(
      createQuerySuccessResult({
        session_id: 'session-1',
        resume_id: null,
        skill_id: 'python-backend',
        skill_display_name: 'Python 后端开发',
        title: 'Python 模拟面试',
        language: 'zh-CN',
        status: 'active',
        current_round: 2,
        max_rounds: 5,
        current_question: {
          question_key: 'q-2',
          round_index: 2,
          category_key: 'PYTHON_BASIC',
          question_text: '请介绍一下 Python GIL。',
          parent_question_key: null,
          source: 'planned',
          status: 'asked',
          is_follow_up: false,
          asked_at: null,
          answered_at: null
        },
        questions: [],
        answers: [
          {
            answer_id: 'answer-1',
            round_index: 1,
            question_key: 'q-1',
            question_text: '请介绍一下你做过的缓存设计。',
            answer_text: '我会从热点 Key、失效策略和一致性角度展开。',
            answer_status: 'submitted',
            submitted_at: '2026-06-01T12:00:00Z',
            answer_metadata: {},
            feedback: {}
          }
        ],
        answer_count: 1,
        follow_up_count: 0,
        completed: false,
        report_status: 'pending',
        last_draft_answer: {
          answer_text: '这是我保存的草稿'
        },
        started_at: null,
        completed_at: null
      })
    );

    vi.spyOn(interviewHooks, 'useInterviewReport').mockReturnValue(
      createQuerySuccessResult({
        session_id: 'session-1',
        skill_id: 'python-backend',
        status: 'pending',
        summary_text: '面试尚未完成。',
        overall_score: null,
        overall_rating: null,
        strengths: [],
        weaknesses: [],
        suggestions: [],
        dimension_scores: {},
        question_evaluations: [],
        rubric_name: null,
        rubric_path: null,
        generation_mode: 'pending',
        markdown_content: '',
        error_message: null,
        generated_at: null
      })
    );

    vi.spyOn(interviewHooks, 'useInterviewReportExport').mockReturnValue(
      createQuerySuccessResult({
        session_id: 'session-1',
        report_status: 'pending',
        export_format: 'md',
        file_name: 'interview-report-session-1.md',
        content: '',
        message: '待生成'
      })
    );

    vi.spyOn(interviewHooks, 'useSaveInterviewDraft').mockReturnValue(
      createMutationResult({
        mutateAsync: vi.fn().mockResolvedValue({ message: '答案草稿已暂存' })
      })
    );

    vi.spyOn(interviewHooks, 'useCompleteInterviewSession').mockReturnValue(
      createMutationResult({
        mutateAsync: vi.fn().mockResolvedValue(undefined)
      })
    );

    renderWithProviders(<InterviewSessionWorkspace sessionId="session-1" />);

    expect(screen.getByText('请介绍一下你做过的缓存设计。')).toBeInTheDocument();
    expect(screen.getByText('我会从热点 Key、失效策略和一致性角度展开。')).toBeInTheDocument();
    expect(screen.getByText('请介绍一下 Python GIL。')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '查看统一评估报告' })).not.toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByDisplayValue('这是我保存的草稿')).toBeInTheDocument();
    });
  });

  test('restores persisted process bubble from answer metadata after refresh', async () => {
    vi.spyOn(interviewHooks, 'useInterviewSession').mockReturnValue(
      createQuerySuccessResult({
        session_id: 'session-process',
        resume_id: null,
        skill_id: 'java-backend',
        skill_display_name: 'Java 后端开发',
        title: 'Java 模拟面试',
        language: 'zh-CN',
        status: 'active',
        current_round: 2,
        max_rounds: 3,
        current_question: {
          question_key: 'q-2',
          round_index: 2,
          category_key: 'JAVA_CORE',
          question_text: '请继续说明线程池拒绝策略如何选择。',
          parent_question_key: null,
          source: 'planned',
          status: 'asked',
          is_follow_up: false,
          asked_at: null,
          answered_at: null
        },
        questions: [],
        answers: [
          {
            answer_id: 'answer-1',
            round_index: 1,
            question_key: 'q-1',
            question_text: '请说明线程池核心参数。',
            answer_text: '我会从核心线程数、最大线程数和队列容量解释。',
            answer_status: 'submitted',
            submitted_at: '2026-06-01T12:00:00Z',
            answer_metadata: {
              process_run: {
                id: 'q-1-1-persisted',
                question_key: 'q-1',
                round_index: 1,
                status: 'completed',
                steps: [
                  {
                    id: 'submit',
                    label: '提交答案',
                    detail: '答案已发送到服务端，正在进入本轮处理流程。',
                    status: 'completed',
                    timestamp: 1780000000000
                  },
                  {
                    id: 'tool_call',
                    label: '调用证据工具',
                    detail: '从 Snailclimb/interview-guide 中获取了相关代码片段。',
                    status: 'completed',
                    timestamp: 1780000000100,
                    tool_summary: {
                      display_name: 'GitHub 仓库证据工具',
                      source: 'Snailclimb/interview-guide',
                      matched_files: ['docs/java/concurrent/ThreadPoolExecutor.md'],
                      retrieval_reason: 'github_code_search_matched'
                    }
                  },
                  {
                    id: 'done',
                    label: '本轮完成',
                    detail: '当前轮流式流程已结束，可以继续作答或查看结果。',
                    status: 'completed',
                    timestamp: 1780000000200
                  }
                ]
              }
            },
            feedback: {}
          }
        ],
        answer_count: 1,
        follow_up_count: 0,
        completed: false,
        report_status: 'pending',
        last_draft_answer: {},
        started_at: null,
        completed_at: null
      })
    );

    vi.spyOn(interviewHooks, 'useSaveInterviewDraft').mockReturnValue(
      createMutationResult({
        mutateAsync: vi.fn().mockResolvedValue({ message: '答案草稿已暂存' })
      })
    );

    vi.spyOn(interviewHooks, 'useCompleteInterviewSession').mockReturnValue(
      createMutationResult({
        mutateAsync: vi.fn().mockResolvedValue(undefined)
      })
    );

    renderWithProviders(<InterviewSessionWorkspace sessionId="session-process" />);

    expect(screen.getByText('面试官处理完成')).toBeInTheDocument();
    expect(screen.getByText('已生成下方问题，可展开回顾处理细节。')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '展开运行过程' }));

    expect(screen.getByText('调用证据工具')).toBeInTheDocument();
    expect(screen.getAllByText(/GitHub 仓库证据工具/).length).toBeGreaterThan(0);
    expect(screen.getByText('docs/java/concurrent/ThreadPoolExecutor.md')).toBeInTheDocument();
  });

  test('shows report link after interview completion', () => {
    vi.spyOn(interviewHooks, 'useInterviewSession').mockReturnValue(
      createQuerySuccessResult({
        session_id: 'session-done',
        resume_id: null,
        skill_id: 'java-backend',
        skill_display_name: 'Java 后端开发',
        title: 'Java 模拟面试',
        language: 'zh-CN',
        status: 'completed',
        current_round: 3,
        max_rounds: 3,
        current_question: null,
        questions: [],
        answers: [
          {
            answer_id: 'answer-1',
            round_index: 1,
            question_key: 'q-1',
            question_text: '请介绍一个项目。',
            answer_text: '我负责核心链路设计。',
            answer_status: 'submitted',
            submitted_at: '2026-06-01T12:00:00Z',
            answer_metadata: {},
            feedback: {}
          }
        ],
        answer_count: 1,
        follow_up_count: 0,
        completed: true,
        report_status: 'generated',
        last_draft_answer: {},
        started_at: null,
        completed_at: '2026-06-01T12:30:00Z'
      })
    );

    vi.spyOn(interviewHooks, 'useSaveInterviewDraft').mockReturnValue(
      createMutationResult({
        mutateAsync: vi.fn().mockResolvedValue({ message: '答案草稿已暂存' })
      })
    );

    vi.spyOn(interviewHooks, 'useCompleteInterviewSession').mockReturnValue(
      createMutationResult({
        mutateAsync: vi.fn().mockResolvedValue(undefined)
      })
    );

    renderWithProviders(<InterviewSessionWorkspace sessionId="session-done" />);

    expect(screen.getByText('面试已完成')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看统一评估报告' })).toHaveAttribute(
      'href',
      '/interview/session-done/report'
    );
  });

  test('uses the displayed question key for answer submit', async () => {
    const saveDraftMock = vi.fn().mockResolvedValue({ message: '答案草稿已暂存' });
    const submitAnswerStreamMock = vi
      .spyOn(interviewApi, 'submitInterviewAnswerStream')
      .mockResolvedValue(undefined);

    vi.spyOn(interviewHooks, 'useInterviewSession').mockReturnValue(
      createQuerySuccessResult({
        session_id: 'session-2',
        resume_id: null,
        skill_id: 'java-backend',
        skill_display_name: 'Java 后端开发',
        title: 'Java 模拟面试',
        language: 'zh-CN',
        status: 'active',
        current_round: 1,
        max_rounds: 3,
        current_question: {
          question_key: 'q-1',
          round_index: 1,
          category_key: 'JAVA_CORE',
          question_text: '请解释一下 Java 中的 String 为什么不可变？',
          parent_question_key: null,
          source: 'planned',
          status: 'asked',
          is_follow_up: false,
          asked_at: null,
          answered_at: null
        },
        questions: [],
        answers: [],
        answer_count: 0,
        follow_up_count: 0,
        completed: false,
        report_status: 'pending',
        last_draft_answer: {},
        started_at: null,
        completed_at: null
      })
    );

    vi.spyOn(interviewHooks, 'useInterviewReport').mockReturnValue(
      createQuerySuccessResult({
        session_id: 'session-2',
        skill_id: 'java-backend',
        status: 'pending',
        summary_text: '面试尚未完成。',
        overall_score: null,
        overall_rating: null,
        strengths: [],
        weaknesses: [],
        suggestions: [],
        dimension_scores: {},
        question_evaluations: [],
        rubric_name: null,
        rubric_path: null,
        generation_mode: 'pending',
        markdown_content: '',
        error_message: null,
        generated_at: null
      })
    );

    vi.spyOn(interviewHooks, 'useInterviewReportExport').mockReturnValue(
      createQuerySuccessResult({
        session_id: 'session-2',
        report_status: 'pending',
        export_format: 'md',
        file_name: 'interview-report-session-2.md',
        content: '',
        message: '待生成'
      })
    );

    vi.spyOn(interviewHooks, 'useSaveInterviewDraft').mockReturnValue(
      createMutationResult({
        mutateAsync: saveDraftMock
      })
    );

    vi.spyOn(interviewHooks, 'useCompleteInterviewSession').mockReturnValue(
      createMutationResult({
        mutateAsync: vi.fn().mockResolvedValue(undefined)
      })
    );

    submitAnswerStreamMock.mockImplementationOnce(async (_sessionId, _payload, options) => {
      options.onEvent?.({
        type: 'step_complete',
        session_id: 'session-2',
        response: {
          session_id: 'session-2',
          action: 'next_question',
          status: 'active',
          completed: false,
          draft_saved: false,
          message: '已切换到下一题',
          current_question: {
            question_key: 'q-2',
            round_index: 2,
            category_key: 'JAVA_CORE',
            question_text: '请继续说明 String 不可变在多线程中的价值。',
            parent_question_key: null,
            source: 'planned',
            status: 'asked',
            is_follow_up: false,
            asked_at: null,
            answered_at: null
          },
          feedback: {},
          report_status: 'pending'
        }
      });
    });

    renderWithProviders(<InterviewSessionWorkspace sessionId="session-2" />);

    const textarea = screen.getByRole('textbox');
    await userEvent.type(textarea, '第一题回答');

    await userEvent.click(screen.getByRole('button', { name: '提交答案' }));

    await waitFor(() => {
      expect(screen.getByText('请继续说明 String 不可变在多线程中的价值。')).toBeInTheDocument();
    });

    await userEvent.clear(textarea);
    await userEvent.type(textarea, '第二题回答');

    await userEvent.click(screen.getByRole('button', { name: '提交答案' }));

    expect(submitAnswerStreamMock).toHaveBeenLastCalledWith(
      'session-2',
      expect.objectContaining({
        answer_text: '第二题回答',
        question_key: 'q-2'
      }),
      expect.any(Object)
    );
  });

  test('shows user-friendly stream progress as SSE events arrive', async () => {
    const submitAnswerStreamMock = vi
      .spyOn(interviewApi, 'submitInterviewAnswerStream')
      .mockResolvedValue(undefined);

    vi.spyOn(interviewHooks, 'useInterviewSession').mockReturnValue(
      createQuerySuccessResult({
        session_id: 'session-progress',
        resume_id: null,
        skill_id: 'java-backend',
        skill_display_name: 'Java 后端开发',
        title: 'Java 模拟面试',
        language: 'zh-CN',
        status: 'active',
        current_round: 1,
        max_rounds: 3,
        current_question: {
          question_key: 'q-1',
          round_index: 1,
          category_key: 'JAVA_CORE',
          question_text: '请说明线程池参数。',
          parent_question_key: null,
          source: 'planned',
          status: 'asked',
          is_follow_up: false,
          asked_at: null,
          answered_at: null
        },
        questions: [],
        answers: [],
        answer_count: 0,
        follow_up_count: 0,
        completed: false,
        report_status: 'pending',
        last_draft_answer: {},
        started_at: null,
        completed_at: null
      })
    );
    vi.spyOn(interviewHooks, 'useSaveInterviewDraft').mockReturnValue(
      createMutationResult({
        mutateAsync: vi.fn().mockResolvedValue({ message: '答案草稿已暂存' })
      })
    );
    vi.spyOn(interviewHooks, 'useCompleteInterviewSession').mockReturnValue(
      createMutationResult({
        mutateAsync: vi.fn().mockResolvedValue(undefined)
      })
    );

    submitAnswerStreamMock.mockImplementationOnce(async (_sessionId, _payload, options) => {
      options.onEvent?.({
        type: 'status',
        session_id: 'session-progress',
        message: '已加载当前面试会话',
        stage: 'session_loaded',
        label: '加载会话与答案上下文',
        detail: '已加载当前面试会话，准备分析本轮回答。'
      });
      options.onEvent?.({
        type: 'status',
        session_id: 'session-progress',
        message: '开始评估回答完整性',
        stage: 'answer_observation_start',
        label: '评估回答完整性',
        detail: '正在判断当前回答是否覆盖本题关键观察点。'
      });
      options.onEvent?.({
        type: 'status',
        session_id: 'session-progress',
        message: '开始判断下一步动作',
        stage: 'replan_start',
        label: '判断下一步动作',
        detail: '正在判断需要追问、切换主题还是结束面试。'
      });
      options.onEvent?.({
        type: 'plan',
        session_id: 'session-progress',
        action: 'next_question',
        reason: '继续考察线程池实践'
      });
      options.onEvent?.({
        type: 'status',
        session_id: 'session-progress',
        message: '开始准备工具上下文',
        stage: 'tool_prepare_start',
        label: '准备工具上下文',
        detail: '正在判断是否需要结合简历或 GitHub 代码证据继续追问。'
      });
      options.onEvent?.({
        type: 'status',
        session_id: 'session-progress',
        message: '证据工具调用完成',
        stage: 'tool_call_complete',
        label: '调用证据工具',
        detail: 'github_repo_evidence 已返回可用于追问的证据。',
        tool: {
          name: 'github_repo_evidence_tool',
          display_name: 'GitHub 仓库证据工具',
          source: 'Snailclimb/interview-guide',
          summary: '从 Snailclimb/interview-guide 中获取了相关代码片段，命中文件：ThreadPoolExecutor.md。',
          matched_files: ['docs/java/concurrent/ThreadPoolExecutor.md'],
          retrieval_reason: 'github_code_search_matched'
        }
      });
      options.onEvent?.({
        type: 'status',
        session_id: 'session-progress',
        message: '开始生成面试官回应',
        stage: 'llm_generation_start',
        label: 'LLM 生成下一题 / 结束语',
        detail: '正在生成面试官下一条提问或收尾说明。'
      });
      options.onEvent?.({
        type: 'content',
        session_id: 'session-progress',
        content: '请继续说明拒绝策略如何选择。'
      });
      options.onEvent?.({
        type: 'status',
        session_id: 'session-progress',
        message: '本轮结果保存完成',
        stage: 'persist_complete',
        label: '保存本轮结果',
        detail: '本轮答案和面试状态已保存。'
      });
      options.onEvent?.({
        type: 'step_complete',
        session_id: 'session-progress',
        response: {
          session_id: 'session-progress',
          action: 'next_question',
          status: 'active',
          completed: false,
          draft_saved: false,
          message: '已切换到下一题',
          current_question: {
            question_key: 'q-2',
            round_index: 2,
            category_key: 'JAVA_CORE',
            question_text: '请继续说明拒绝策略如何选择。',
            parent_question_key: null,
            source: 'planned',
            status: 'asked',
            is_follow_up: false,
            asked_at: null,
            answered_at: null
          },
          feedback: {},
          report_status: 'pending'
        }
      });
      options.onEvent?.({ type: 'done', session_id: 'session-progress', session: {} as never });
    });

    renderWithProviders(<InterviewSessionWorkspace sessionId="session-progress" />);

    const textarea = screen.getByRole('textbox');
    await userEvent.type(textarea, '我会结合核心线程数、最大线程数和队列容量说明。');
    await userEvent.click(screen.getByRole('button', { name: '提交答案' }));

    expect(screen.getByText('面试官处理完成')).toBeInTheDocument();
    expect(screen.getByText('运行过程')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '展开运行过程' }));
    expect(screen.getByText('评估回答完整性')).toBeInTheDocument();
    expect(screen.getByText('判断下一步动作')).toBeInTheDocument();
    expect(screen.getByText('准备证据工具')).toBeInTheDocument();
    expect(screen.getByText('调用证据工具')).toBeInTheDocument();
    expect(screen.getByText('生成下一题 / 结束语')).toBeInTheDocument();
    expect(screen.getAllByText(/GitHub 仓库证据工具/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Snailclimb\/interview-guide/).length).toBeGreaterThan(0);
    expect(screen.getByText('docs/java/concurrent/ThreadPoolExecutor.md')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('本轮完成')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '收起运行过程' })).toBeInTheDocument();
      expect(screen.getByText('查看技术日志')).toBeInTheDocument();
    });
  });

  test('marks stream timeline as failed when SSE reports an error', async () => {
    vi.spyOn(interviewApi, 'submitInterviewAnswerStream').mockImplementationOnce(async (_sessionId, _payload, options) => {
      options.onError?.(new Error('provider timeout'));
    });

    vi.spyOn(interviewHooks, 'useInterviewSession').mockReturnValue(
      createQuerySuccessResult({
        session_id: 'session-error',
        resume_id: null,
        skill_id: 'java-backend',
        skill_display_name: 'Java 后端开发',
        title: 'Java 模拟面试',
        language: 'zh-CN',
        status: 'active',
        current_round: 1,
        max_rounds: 3,
        current_question: {
          question_key: 'q-1',
          round_index: 1,
          category_key: 'JAVA_CORE',
          question_text: '请说明 HashMap 扩容。',
          parent_question_key: null,
          source: 'planned',
          status: 'asked',
          is_follow_up: false,
          asked_at: null,
          answered_at: null
        },
        questions: [],
        answers: [],
        answer_count: 0,
        follow_up_count: 0,
        completed: false,
        report_status: 'pending',
        last_draft_answer: {},
        started_at: null,
        completed_at: null
      })
    );
    vi.spyOn(interviewHooks, 'useSaveInterviewDraft').mockReturnValue(
      createMutationResult({
        mutateAsync: vi.fn().mockResolvedValue({ message: '答案草稿已暂存' })
      })
    );
    vi.spyOn(interviewHooks, 'useCompleteInterviewSession').mockReturnValue(
      createMutationResult({
        mutateAsync: vi.fn().mockResolvedValue(undefined)
      })
    );

    renderWithProviders(<InterviewSessionWorkspace sessionId="session-error" />);

    const textarea = screen.getByRole('textbox');
    await userEvent.type(textarea, '我会说明数组扩容和链表树化。');
    await userEvent.click(screen.getByRole('button', { name: '提交答案' }));

    expect(screen.getByText('面试官处理异常')).toBeInTheDocument();
    expect(screen.queryByText('本轮处理进度')).not.toBeInTheDocument();
    expect(screen.getByText('流式过程异常')).toBeInTheDocument();
    expect(screen.getByText('provider timeout')).toBeInTheDocument();
  });

  test('shows submitted answer immediately and only streams the next question', async () => {
    const submitAnswerStreamMock = vi
      .spyOn(interviewApi, 'submitInterviewAnswerStream')
      .mockResolvedValue(undefined);

    vi.spyOn(interviewHooks, 'useInterviewSession').mockReturnValue(
      createQuerySuccessResult({
        session_id: 'session-3',
        resume_id: null,
        skill_id: 'go-backend',
        skill_display_name: 'Go 后端开发',
        title: 'Go 模拟面试',
        language: 'zh-CN',
        status: 'active',
        current_round: 1,
        max_rounds: 3,
        current_question: {
          question_key: 'q-1',
          round_index: 1,
          category_key: 'GO_CORE',
          question_text: '请介绍一下 channel 的常见使用场景。',
          parent_question_key: null,
          source: 'planned',
          status: 'asked',
          is_follow_up: false,
          asked_at: null,
          answered_at: null
        },
        questions: [],
        answers: [],
        answer_count: 0,
        follow_up_count: 0,
        completed: false,
        report_status: 'pending',
        last_draft_answer: {},
        started_at: null,
        completed_at: null
      })
    );

    vi.spyOn(interviewHooks, 'useInterviewReport').mockReturnValue(
      createQuerySuccessResult({
        session_id: 'session-3',
        skill_id: 'go-backend',
        status: 'pending',
        summary_text: '面试尚未完成。',
        overall_score: null,
        overall_rating: null,
        strengths: [],
        weaknesses: [],
        suggestions: [],
        dimension_scores: {},
        question_evaluations: [],
        rubric_name: null,
        rubric_path: null,
        generation_mode: 'pending',
        markdown_content: '',
        error_message: null,
        generated_at: null
      })
    );

    vi.spyOn(interviewHooks, 'useInterviewReportExport').mockReturnValue(
      createQuerySuccessResult({
        session_id: 'session-3',
        report_status: 'pending',
        export_format: 'md',
        file_name: 'interview-report-session-3.md',
        content: '',
        message: '待生成'
      })
    );

    vi.spyOn(interviewHooks, 'useSaveInterviewDraft').mockReturnValue(
      createMutationResult({
        mutateAsync: vi.fn().mockResolvedValue({ message: '答案草稿已暂存' })
      })
    );

    vi.spyOn(interviewHooks, 'useCompleteInterviewSession').mockReturnValue(
      createMutationResult({
        mutateAsync: vi.fn().mockResolvedValue(undefined)
      })
    );

    submitAnswerStreamMock.mockImplementationOnce(async (_sessionId, _payload, options) => {
      options.onEvent?.({
        type: 'content',
        session_id: 'session-3',
        content: '请继续说明 channel 在取消控制和背压中的实践。'
      });

      options.onEvent?.({
        type: 'step_complete',
        session_id: 'session-3',
        response: {
          session_id: 'session-3',
          action: 'next_question',
          status: 'active',
          completed: false,
          draft_saved: false,
          message: '已切换到下一题',
          current_question: {
            question_key: 'q-2',
            round_index: 2,
            category_key: 'GO_CORE',
            question_text: '请继续说明 channel 在取消控制和背压中的实践。',
            parent_question_key: null,
            source: 'planned',
            status: 'asked',
            is_follow_up: false,
            asked_at: null,
            answered_at: null
          },
          feedback: {},
          report_status: 'pending'
        }
      });
    });

    renderWithProviders(<InterviewSessionWorkspace sessionId="session-3" />);

    const textarea = screen.getByRole('textbox');
    await userEvent.type(textarea, '我会用它协调 goroutine 之间的任务和退出。');
    await userEvent.click(screen.getByRole('button', { name: '提交答案' }));

    expect(screen.getByText('我会用它协调 goroutine 之间的任务和退出。')).toBeInTheDocument();
    expect(screen.getByText('请介绍一下 channel 的常见使用场景。')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('请继续说明 channel 在取消控制和背压中的实践。')).toBeInTheDocument();
    });

    expect(textarea).toHaveValue('');
  });
});
