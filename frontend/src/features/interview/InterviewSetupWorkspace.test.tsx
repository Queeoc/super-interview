import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, test, vi } from 'vitest';

import { InterviewSetupWorkspace } from './InterviewSetupWorkspace';
import { createMutationResult, createQuerySuccessResult } from '../../test/fixtures/queryMocks';
import { renderWithProviders } from '../../test/testUtils';
import * as interviewHooks from './useInterview';
import * as resumeHooks from '../resume/useResume';
import * as skillsHooks from '../skills/useSkills';

const mockNavigate = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate
  };
});

describe('InterviewSetupWorkspace', () => {
  beforeEach(() => {
    mockNavigate.mockReset();
  });

  test('shows creation timeline while initial question is being prepared', () => {
    vi.spyOn(skillsHooks, 'useSkillsList').mockReturnValue(
      createQuerySuccessResult([
        {
          skill_id: 'python-backend',
          display_name: 'Python 后端开发',
          description: 'Python 面试',
          display: {},
          categories: []
        }
      ])
    );

    vi.spyOn(resumeHooks, 'useResumeList').mockReturnValue(createQuerySuccessResult([]));
    vi.spyOn(interviewHooks, 'useInterviewSessionsList').mockReturnValue(createQuerySuccessResult([]));
    vi.spyOn(interviewHooks, 'useCreateInterviewSession').mockReturnValue(
      createMutationResult({
        isPending: true,
        status: 'pending',
        submittedAt: Date.now(),
        mutateAsync: vi.fn()
      })
    );

    renderWithProviders(<InterviewSetupWorkspace />);

    expect(screen.getByText('创建面试准备中')).toBeInTheDocument();
    expect(screen.getByText('生成面试官开场与首题')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成首题中...' })).toBeDisabled();
  });

  test('creates session and waits for staged creation before navigating', async () => {
    vi.useFakeTimers();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    vi.spyOn(skillsHooks, 'useSkillsList').mockReturnValue(
      createQuerySuccessResult([
        {
          skill_id: 'python-backend',
          display_name: 'Python 后端开发',
          description: 'Python 面试',
          display: {},
          categories: []
        }
      ])
    );

    vi.spyOn(resumeHooks, 'useResumeList').mockReturnValue(createQuerySuccessResult([]));
    vi.spyOn(interviewHooks, 'useInterviewSessionsList').mockReturnValue(createQuerySuccessResult([]));

    const mutateAsync = vi.fn().mockResolvedValue({
      session_id: 'session-1'
    });

    vi.spyOn(interviewHooks, 'useCreateInterviewSession').mockReturnValue(
      createMutationResult({
        mutateAsync
      })
    );

    renderWithProviders(<InterviewSetupWorkspace />);

    try {
      await user.click(screen.getByRole('button', { name: '开始面试' }));

      await waitFor(() => {
        expect(mutateAsync).toHaveBeenCalled();
      });
      expect(mockNavigate).not.toHaveBeenCalled();
      expect(screen.getByText('读取面试配置')).toBeInTheDocument();

      await vi.advanceTimersByTimeAsync(1000);
      expect(screen.getByText('加载 Skill / 简历上下文')).toBeInTheDocument();
      expect(mockNavigate).not.toHaveBeenCalled();

      await vi.advanceTimersByTimeAsync(2000);
      expect(screen.getByText('生成面试官开场与首题')).toBeInTheDocument();
      expect(mockNavigate).not.toHaveBeenCalled();

      await vi.advanceTimersByTimeAsync(650);
      await waitFor(() => {
        expect(mockNavigate).toHaveBeenCalledWith('/interview/session-1');
      });
    } finally {
      vi.useRealTimers();
    }
  });

  test('renders current visitor interview history below the create form', async () => {
    vi.spyOn(skillsHooks, 'useSkillsList').mockReturnValue(
      createQuerySuccessResult([
        {
          skill_id: 'python-backend',
          display_name: 'Python 后端开发',
          description: 'Python 面试',
          display: {},
          categories: []
        }
      ])
    );

    vi.spyOn(resumeHooks, 'useResumeList').mockReturnValue(createQuerySuccessResult([]));
    vi.spyOn(interviewHooks, 'useInterviewSessionsList').mockReturnValue(
      createQuerySuccessResult([
        {
          session_id: 'session-2',
          resume_id: null,
          skill_id: 'python-backend',
          skill_display_name: 'Python 后端开发',
          title: '缓存设计专项面试',
          language: 'zh-CN',
          status: 'active',
          current_round: 2,
          max_rounds: 5,
          answer_count: 1,
          completed: false,
          report_status: null,
          current_question: {
            question_key: 'q-2',
            round_index: 2,
            category_key: 'CACHE',
            question_text: '请继续说明缓存一致性方案。',
            parent_question_key: null,
            source: 'planned',
            status: 'asked',
            is_follow_up: false,
            asked_at: null,
            answered_at: null
          },
          started_at: '2026-06-02T08:00:00Z',
          updated_at: '2026-06-02T08:30:00Z',
          completed_at: null
        }
      ])
    );

    vi.spyOn(interviewHooks, 'useCreateInterviewSession').mockReturnValue(
      createMutationResult({
        mutateAsync: vi.fn()
      })
    );

    renderWithProviders(<InterviewSetupWorkspace />);

    expect(screen.getByText('历史面试记录')).toBeInTheDocument();
    expect(screen.getByText('缓存设计专项面试')).toBeInTheDocument();
    expect(screen.getByText('Skill：Python 后端开发')).toBeInTheDocument();
    expect(screen.getByText('当前题目：请继续说明缓存一致性方案。')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /缓存设计专项面试/ }));

    expect(mockNavigate).toHaveBeenCalledWith('/interview/session-2');
  });

  test('marks completed sessions with processing reports as generating report', () => {
    vi.spyOn(skillsHooks, 'useSkillsList').mockReturnValue(
      createQuerySuccessResult([
        {
          skill_id: 'python-backend',
          display_name: 'Python 后端开发',
          description: 'Python 面试',
          display: {},
          categories: []
        }
      ])
    );

    vi.spyOn(resumeHooks, 'useResumeList').mockReturnValue(createQuerySuccessResult([]));
    vi.spyOn(interviewHooks, 'useInterviewSessionsList').mockReturnValue(
      createQuerySuccessResult([
        {
          session_id: 'session-processing',
          resume_id: null,
          skill_id: 'python-backend',
          skill_display_name: 'Python 后端开发',
          title: '报告生成专项面试',
          language: 'zh-CN',
          status: 'completed',
          current_round: 3,
          max_rounds: 3,
          answer_count: 3,
          completed: true,
          report_status: 'processing',
          current_question: null,
          started_at: '2026-06-02T08:00:00Z',
          updated_at: '2026-06-02T08:30:00Z',
          completed_at: '2026-06-02T08:30:00Z'
        }
      ])
    );
    vi.spyOn(interviewHooks, 'useCreateInterviewSession').mockReturnValue(
      createMutationResult({
        mutateAsync: vi.fn()
      })
    );

    renderWithProviders(<InterviewSetupWorkspace />);

    expect(screen.getByText('报告生成中')).toBeInTheDocument();
  });
});
