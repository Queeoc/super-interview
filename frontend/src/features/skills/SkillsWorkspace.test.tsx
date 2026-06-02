import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';

import { SkillsWorkspace } from './SkillsWorkspace';
import { createQuerySuccessResult } from '../../test/fixtures/queryMocks';
import { renderWithProviders } from '../../test/testUtils';
import * as skillsHooks from './useSkills';

describe('SkillsWorkspace', () => {
  test('loads list and switches detail content', async () => {
    vi.spyOn(skillsHooks, 'useSkillsList').mockReturnValue(
      createQuerySuccessResult([
        {
          skill_id: 'python-backend',
          display_name: 'Python 后端开发',
          description: 'Python 面试',
          display: {},
          categories: [{ key: 'PYTHON_BASIC', label: 'Python 基础', priority: 'CORE', ref: null, shared: false }]
        },
        {
          skill_id: 'algorithm',
          display_name: '算法',
          description: '算法面试',
          display: {},
          categories: [{ key: 'ALGO', label: '算法', priority: 'CORE', ref: null, shared: false }]
        }
      ])
    );

    vi.spyOn(skillsHooks, 'useSkillDetail').mockImplementation((skillId) =>
      createQuerySuccessResult(
        skillId === 'algorithm'
          ? {
              skill_id: 'algorithm',
              display_name: '算法',
              description: '算法面试',
              display: {},
              categories: [{ key: 'ALGO', label: '算法', priority: 'CORE', ref: null, shared: false }],
              content_markdown: '# 算法',
              references: [],
              enabled_tools: [],
              question_preferences: { difficulty: 'high' }
            }
          : {
              skill_id: 'python-backend',
              display_name: 'Python 后端开发',
              description: 'Python 面试',
              display: {},
              categories: [{ key: 'PYTHON_BASIC', label: 'Python 基础', priority: 'CORE', ref: null, shared: false }],
              content_markdown: '# Python',
              references: [],
              enabled_tools: ['knowledge_tool'],
              question_preferences: { difficulty: 'medium' }
            }
      )
    );

    vi.spyOn(skillsHooks, 'useSkillReferenceSection').mockImplementation((skillId) =>
      createQuerySuccessResult({
        skill_id: skillId ?? '',
        reference_markdown: skillId === 'algorithm' ? '# 算法参考' : '# Python 参考',
        resolved_reference_files: []
      })
    );

    renderWithProviders(<SkillsWorkspace />);

    expect(screen.getByRole('heading', { name: 'Python 后端开发' })).toBeInTheDocument();
    expect(screen.getByText('Python 参考')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /算法/ }));

    await waitFor(() => {
      expect(screen.getByText('算法参考')).toBeInTheDocument();
      expect(screen.getByText(/"difficulty":\s*"high"/)).toBeInTheDocument();
    });
  });
});
