import { render, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

import { AppRoot } from './AppRoot';
import { createQuerySuccessResult } from '../test/fixtures/queryMocks';
import * as resumeHooks from '../features/resume/useResume';
import * as skillsHooks from '../features/skills/useSkills';
import * as storageUtils from '../utils/storage';

describe('AppRoot', () => {
  test('mounts the routed app and renders the home workspace', () => {
    vi.spyOn(skillsHooks, 'useSkillsList').mockReturnValue(createQuerySuccessResult([]));
    vi.spyOn(resumeHooks, 'useResumeList').mockReturnValue(createQuerySuccessResult([]));
    vi.spyOn(storageUtils, 'getRecentInterviewSessionId').mockReturnValue(null);

    render(<AppRoot />);

    expect(screen.getByRole('heading', { name: 'super-interview 工作台' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '文字面试' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /开始文字面试/ })).toBeInTheDocument();
  });
});
