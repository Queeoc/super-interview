import { AppError } from '../../types';
import { apiClient } from '../api';

import { parseSseChunk } from './parseSseChunk';
import type { StreamConsumerCallbacks, StreamDispatcher } from './streamTypes';

type ConsumePostStreamOptions<TRequest, TEvent> = {
  path: string;
  body: TRequest;
  signal?: AbortSignal;
  dispatcher: StreamDispatcher<TEvent>;
} & StreamConsumerCallbacks<TEvent>;

export async function consumePostStream<TRequest, TEvent>({
  path,
  body,
  signal,
  dispatcher,
  onEvent,
  onError,
  onDone
}: ConsumePostStreamOptions<TRequest, TEvent>): Promise<void> {
  let response: Response;

  try {
    response = await fetch(apiClient.buildUrl(path), {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(body),
      signal
    });
  } catch (error) {
    if (signal?.aborted) {
      return;
    }

    const requestError =
      error instanceof AppError
        ? error
        : new AppError({
            code: 1005,
            message: '流式请求失败',
            data: {
              cause: error instanceof Error ? error.message : 'unknown'
            }
          });

    onError?.(requestError);
    throw requestError;
  }

  if (!response.ok) {
    let errorPayload: Record<string, unknown> = {};

    try {
      errorPayload = (await response.json()) as Record<string, unknown>;
    } catch {
      errorPayload = {};
    }

    const error = new AppError({
      code: Number(errorPayload.code ?? response.status),
      message: String(errorPayload.message ?? '流式请求失败'),
      data: (errorPayload.data as Record<string, unknown>) ?? {},
      status: response.status
    });
    onError?.(error);
    throw error;
  }

  if (!response.body) {
    const error = new AppError({
      code: 1005,
      message: '流式响应缺少可读数据流',
      status: response.status
    });
    onError?.(error);
    throw error;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let remainder = '';

  try {
    while (true) {
      const { done, value } = await reader.read();

      if (done) {
        break;
      }

      const chunk = decoder.decode(value, { stream: true });
      const parsed = parseSseChunk(chunk, remainder);
      remainder = parsed.remainder;

      for (const message of parsed.messages) {
        const event = dispatcher.handleRawMessage(message);
        if (event) {
          onEvent?.(event);
        }
      }
    }

    if (remainder.trim()) {
      const parsed = parseSseChunk('\n\n', remainder);
      for (const message of parsed.messages) {
        const event = dispatcher.handleRawMessage(message);
        if (event) {
          onEvent?.(event);
        }
      }
    }

    onDone?.();
  } catch (error) {
    if (signal?.aborted) {
      return;
    }

    const streamError =
      error instanceof AppError
        ? error
        : new AppError({
            code: 1005,
            message: '流式消费失败',
            data: {
              cause: error instanceof Error ? error.message : 'unknown'
            }
          });

    onError?.(streamError);
    throw streamError;
  } finally {
    reader.releaseLock();
  }
}
