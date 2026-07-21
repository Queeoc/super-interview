import { act, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

import { createQuerySuccessResult } from '../../test/fixtures/queryMocks';
import { renderWithProviders } from '../../test/testUtils';
import type { InterviewReportDto, InterviewSessionDto } from '../../types';
import { InterviewReportWorkspace } from './InterviewReportWorkspace';
import * as interviewHooks from './useInterview';

describe('InterviewReportWorkspace', () => {
  test('shows pending state while report is generating', () => {
    mockSession({ completed: true, reportStatus: 'processing' });
    mockReport({
      status: 'processing',
      summary_text: '统一评估进行中，请稍后刷新查看结果。'
    });

    renderWithProviders(<InterviewReportWorkspace sessionId="session-1" />);

    expect(screen.getByText('报告生成中')).toBeInTheDocument();
    expect(screen.getByText('收集本场问答')).toBeInTheDocument();
    expect(screen.getByText('逐题评估')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '返回面试会话' })).toHaveAttribute('href', '/interview/session-1');
  });

  test('polls pending report and shows slow-generation guidance after timeout', () => {
    vi.useFakeTimers();
    const refetch = vi.fn();
    mockSession({ completed: true, reportStatus: 'processing' });
    mockReport(
      {
        status: 'processing',
        summary_text: '统一评估进行中，请稍后刷新查看结果。'
      },
      { refetch }
    );

    renderWithProviders(<InterviewReportWorkspace sessionId="session-1" />);

    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(refetch).toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(18000);
    });
    expect(screen.getByText('报告还在生成，可能包含较多题目或模型调用较慢。你可以稍后回来查看。')).toBeInTheDocument();
    vi.useRealTimers();
  });

  test('renders generated summary, dimensions, and per-question details', () => {
    mockSession({ completed: true, reportStatus: 'generated' });
    mockReport({
      status: 'generated',
      summary_text: '候选人具备扎实的后端项目经验，但表达结构仍可加强。',
      overall_score: 88,
      overall_rating: 'good',
      strengths: ['项目经验扎实'],
      weaknesses: ['表达还可以更结构化'],
      suggestions: ['补充更多量化结果'],
      dimension_scores: {
        technical_depth: 86,
        implementation_clarity: 82,
        problem_solving: 84
      },
      question_evaluations: [
        {
          question_key: 'q-1',
          round_index: 1,
          question_text: '请介绍一个你主导的后端项目。',
          answer_text: '我负责核心链路设计、缓存策略和上线治理。',
          score: 88,
          rating: 'good',
          strengths: ['能说明项目职责'],
          weaknesses: ['边界场景还不够完整'],
          suggestions: ['补充压测和故障处理'],
          rationale: '回答体现了较好的工程落地能力。',
          source: 'llm'
        }
      ]
    });

    renderWithProviders(<InterviewReportWorkspace sessionId="session-1" />);

    expect(screen.getByText('候选人具备扎实的后端项目经验，但表达结构仍可加强。')).toBeInTheDocument();
    expect(screen.getByText('待提升领域')).toBeInTheDocument();
    expect(screen.getByText('表达还可以更结构化')).toBeInTheDocument();
    expect(screen.getAllByText('项目经验').length).toBeGreaterThan(0);
    expect(screen.getAllByText('技术深度').length).toBeGreaterThan(0);
    expect(screen.getAllByText('表达清晰度').length).toBeGreaterThan(0);
    expect(screen.queryByText('implementation_clarity')).not.toBeInTheDocument();
    expect(screen.getByText('请介绍一个你主导的后端项目。')).toBeInTheDocument();
    expect(screen.getByText('回答体现了较好的工程落地能力。')).toBeInTheDocument();
  });

  test('does not poll after report has been generated', () => {
    vi.useFakeTimers();
    const refetch = vi.fn();
    mockSession({ completed: true, reportStatus: 'generated' });
    mockReport(
      {
        status: 'generated',
        summary_text: '报告已生成。',
        overall_score: 88,
        overall_rating: 'good',
        strengths: [],
        weaknesses: [],
        suggestions: [],
        dimension_scores: {
          project_experience: 88,
          technical_depth: 88,
          skill_match: 88,
          content_completeness: 88,
          communication_clarity: 88
        }
      },
      { refetch }
    );

    renderWithProviders(<InterviewReportWorkspace sessionId="session-1" />);

    act(() => {
      vi.advanceTimersByTime(4000);
    });

    expect(refetch).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  test('does not poll after report generation has failed', () => {
    vi.useFakeTimers();
    const refetch = vi.fn();
    mockSession({ completed: true, reportStatus: 'failed' });
    mockReport(
      {
        status: 'failed',
        summary_text: '统一评估未能完成。',
        error_message: 'provider timeout'
      },
      { refetch }
    );

    renderWithProviders(<InterviewReportWorkspace sessionId="session-1" />);

    act(() => {
      vi.advanceTimersByTime(4000);
    });

    expect(refetch).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  test('shows failed state when report generation fails', () => {
    mockSession({ completed: true, reportStatus: 'failed' });
    mockReport({
      status: 'failed',
      summary_text: '统一评估未能完成。',
      error_message: 'provider timeout'
    });

    renderWithProviders(<InterviewReportWorkspace sessionId="session-1" />);

    expect(screen.getByText('报告生成失败')).toBeInTheDocument();
    expect(screen.getByText('provider timeout')).toBeInTheDocument();
  });

  test('blocks report details before interview completion', () => {
    mockSession({ completed: false, reportStatus: 'pending' });
    mockReport({
      status: 'pending',
      summary_text: '面试尚未完成。'
    });

    renderWithProviders(<InterviewReportWorkspace sessionId="session-1" />);

    expect(screen.getByText('面试未完成')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '返回面试会话' })).toHaveAttribute('href', '/interview/session-1');
  });
});

function mockSession({ completed, reportStatus }: { completed: boolean; reportStatus: string }) {
  vi.spyOn(interviewHooks, 'useInterviewSession').mockReturnValue(
    createQuerySuccessResult<InterviewSessionDto>({
      session_id: 'session-1',
      resume_id: null,
      skill_id: 'java-backend',
      skill_display_name: 'Java 后端开发',
      title: 'Java 模拟面试',
      language: 'zh-CN',
      status: completed ? 'completed' : 'active',
      current_round: completed ? 3 : 1,
      max_rounds: 3,
      current_question: completed
        ? null
        : {
            question_key: 'q-1',
            round_index: 1,
            category_key: 'JAVA_CORE',
            question_text: '请介绍一个项目。',
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
      completed,
      report_status: reportStatus,
      last_draft_answer: {},
      started_at: null,
      completed_at: completed ? '2026-06-01T12:30:00Z' : null
    })
  );
}

function mockReport(
  overrides: Partial<InterviewReportDto>,
  queryOverrides: Partial<ReturnType<typeof createQuerySuccessResult<InterviewReportDto>>> = {}
) {
  vi.spyOn(interviewHooks, 'useInterviewReport').mockReturnValue(
    {
      ...createQuerySuccessResult<InterviewReportDto>({
        session_id: 'session-1',
        skill_id: 'java-backend',
        status: 'pending',
        summary_text: null,
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
        generated_at: null,
        ...overrides
      }),
      ...queryOverrides
    }
  );
}
