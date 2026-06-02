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
    expect(screen.getByText('报告暂未开放')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByDisplayValue('这是我保存的草稿')).toBeInTheDocument();
    });
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
