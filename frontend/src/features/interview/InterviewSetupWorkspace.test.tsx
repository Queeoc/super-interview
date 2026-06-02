import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';

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
  test('creates session and navigates to session page', async () => {
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

    await userEvent.click(screen.getByRole('button', { name: '开始面试' }));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalled();
      expect(mockNavigate).toHaveBeenCalledWith('/interview/session-1');
    });
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
});
