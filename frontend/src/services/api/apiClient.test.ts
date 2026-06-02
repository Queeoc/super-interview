import { describe, expect, test, vi } from 'vitest';

import { AppError } from '../../types';
import { apiClient } from './apiClient';

describe('apiClient', () => {
  test('unwraps business success response', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: 200,
          message: 'success',
          data: { value: 'ok' }
        }),
        {
          status: 200,
          headers: {
            'Content-Type': 'application/json'
          }
        }
      )
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiClient.get<{ value: string }>('/api/health')).resolves.toEqual({ value: 'ok' });
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/health',
      expect.objectContaining({
        credentials: 'include',
        method: 'GET'
      })
    );
  });

  test('throws AppError on business failure code', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            code: 3101,
            message: '会话不存在',
            data: {}
          }),
          {
            status: 200,
            headers: {
              'Content-Type': 'application/json'
            }
          }
        )
      )
    );

    await expect(apiClient.get('/api/interview/sessions/demo')).rejects.toBeInstanceOf(AppError);
  });

  test('does not force Content-Type for FormData upload', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: 200,
          message: 'success',
          data: { uploaded: true }
        }),
        {
          status: 200,
          headers: {
            'Content-Type': 'application/json'
          }
        }
      )
    );
    vi.stubGlobal('fetch', fetchMock);

    const formData = new FormData();
    formData.append('file', new Blob(['resume']), 'resume.txt');

    await apiClient.upload('/api/resumes/upload', formData);

    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = options.headers as Headers;
    expect(headers.has('Content-Type')).toBe(false);
  });
});
