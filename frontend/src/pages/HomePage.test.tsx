import { screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

import { HomePage } from './HomePage';
import { createQuerySuccessResult } from '../test/fixtures/queryMocks';
import { renderWithProviders } from '../test/testUtils';
import * as resumeHooks from '../features/resume/useResume';
import * as skillsHooks from '../features/skills/useSkills';
import * as storageUtils from '../utils/storage';

describe('HomePage', () => {
  test('renders workspace entries and recent session status', () => {
    vi.spyOn(skillsHooks, 'useSkillsList').mockReturnValue(
      createQuerySuccessResult([{ skill_id: 'python', display_name: 'Python', description: '', display: {}, categories: [] }])
    );

    vi.spyOn(resumeHooks, 'useResumeList').mockReturnValue(createQuerySuccessResult([]));

    vi.spyOn(storageUtils, 'getRecentInterviewSessionId').mockReturnValue('session-1');

    renderWithProviders(<HomePage />);

    expect(screen.getByRole('heading', { name: 'super-interview 工作台' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /继续最近会话/ })).toBeInTheDocument();
    expect(screen.getByText('可继续')).toBeInTheDocument();
  });
});
