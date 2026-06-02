import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';

import { ResumeWorkspace } from './ResumeWorkspace';
import { createMutationResult, createQuerySuccessResult } from '../../test/fixtures/queryMocks';
import { renderWithProviders } from '../../test/testUtils';
import * as resumeHooks from './useResume';

describe('ResumeWorkspace', () => {
  test('uploads and focuses latest resume detail', async () => {
    const mutateAsync = vi.fn().mockResolvedValue({
      resume: {
        resume_id: 'resume-2',
        original_file_name: 'new.pdf',
        file_extension: '.pdf',
        mime_type: 'application/pdf',
        file_size: 1024,
        status: 'completed',
        uploaded_at: '2026-06-01T10:00:00Z',
        updated_at: '2026-06-01T10:00:00Z',
        markdown_content: '# Resume',
        source_metadata: { source: 'upload' }
      },
      reused_existing: false
    });

    vi.spyOn(resumeHooks, 'useResumeList').mockReturnValue(
      createQuerySuccessResult([
        {
          resume_id: 'resume-1',
          original_file_name: 'old.pdf',
          file_extension: '.pdf',
          mime_type: 'application/pdf',
          file_size: 2048,
          status: 'completed',
          uploaded_at: '2026-05-31T10:00:00Z',
          updated_at: '2026-05-31T10:00:00Z'
        }
      ])
    );

    vi.spyOn(resumeHooks, 'useResumeDetail').mockImplementation((resumeId) =>
      createQuerySuccessResult({
          resume_id: resumeId ?? 'resume-1',
          original_file_name: resumeId === 'resume-2' ? 'new.pdf' : 'old.pdf',
          file_extension: '.pdf',
          mime_type: 'application/pdf',
          file_size: 1024,
          status: 'completed',
          uploaded_at: '2026-06-01T10:00:00Z',
          updated_at: '2026-06-01T10:00:00Z',
          markdown_content: resumeId === 'resume-2' ? '# Resume' : '# Old Resume',
          source_metadata: { source: 'upload' }
        })
    );

    vi.spyOn(resumeHooks, 'useUploadResume').mockReturnValue(
      createMutationResult({
        mutateAsync
      })
    );

    renderWithProviders(<ResumeWorkspace />);

    const input = screen.getByLabelText(/选择文件上传/i) as HTMLInputElement;
    const file = new File(['resume'], 'new.pdf', { type: 'application/pdf' });

    await userEvent.upload(input, file);

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalled();
      expect(screen.getByText('简历上传成功，已同步刷新列表。')).toBeInTheDocument();
    });
  });
});
