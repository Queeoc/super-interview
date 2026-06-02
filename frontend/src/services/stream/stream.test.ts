import { describe, expect, test, vi } from 'vitest';

import { AppError } from '../../types';
import { consumePostStream } from './consumePostStream';
import { createStreamDispatcher } from './createStreamDispatcher';
import { parseSseChunk } from './parseSseChunk';

type DemoEvent = {
  type: string;
  message?: string;
  content?: string;
};

function createStreamResponse(chunks: string[]) {
  const encoder = new TextEncoder();

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    }
  });

  return new Response(stream, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream'
    }
  });
}

describe('stream helpers', () => {
  test('parseSseChunk parses multiple message frames', () => {
    const result = parseSseChunk(
      'event: message\ndata: {"type":"status","message":"ready"}\n\nevent: message\ndata: {"type":"done"}\n\n'
    );

    expect(result.messages).toHaveLength(2);
    expect(result.messages[0].data).toContain('"status"');
    expect(result.remainder).toBe('');
  });

  test('parseSseChunk handles CRLF-separated SSE frames from backend', () => {
    const result = parseSseChunk(
      'event: message\r\ndata: {"type":"status","message":"ready"}\r\n\r\nevent: message\r\ndata: {"type":"done"}\r\n\r\n'
    );

    expect(result.messages).toHaveLength(2);
    expect(result.messages[0].data).toContain('"status"');
    expect(result.messages[1].data).toContain('"done"');
    expect(result.remainder).toBe('');
  });

  test('consumePostStream dispatches parsed events', async () => {
    const events: DemoEvent[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        createStreamResponse([
          'event: message\ndata: {"type":"status","message":"开始"}\n\n',
          'event: message\ndata: {"type":"content","content":"你好"}\n\n',
          'event: message\ndata: {"type":"done"}\n\n'
        ])
      )
    );

    await consumePostStream({
      path: '/api/interview/sessions/demo/answers',
      body: { answer_text: 'test' },
      dispatcher: createStreamDispatcher<DemoEvent>(),
      onEvent(event) {
        events.push(event);
      }
    });

    expect(events.map((item) => item.type)).toEqual(['status', 'content', 'done']);
  });

  test('consumePostStream ignores heartbeat and empty frames', async () => {
    const events: DemoEvent[] = [];

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        createStreamResponse([
          ': ping\n\n',
          'event: message\n\n',
          'event: message\ndata: {"type":"status","message":"ready"}\n\n',
          ': keep-alive\n\n',
          'event: message\ndata: {"type":"done"}\n\n'
        ])
      )
    );

    await consumePostStream({
      path: '/api/interview/sessions/demo/answers',
      body: { answer_text: 'test' },
      dispatcher: createStreamDispatcher<DemoEvent>(),
      onEvent(event) {
        events.push(event);
      }
    });

    expect(events.map((item) => item.type)).toEqual(['status', 'done']);
  });

  test('consumePostStream surfaces invalid JSON as AppError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        createStreamResponse(['event: message\ndata: not-json\n\n'])
      )
    );

    await expect(
      consumePostStream({
        path: '/api/interview/sessions/demo/answers',
        body: { answer_text: 'test' },
        dispatcher: createStreamDispatcher<DemoEvent>()
      })
    ).rejects.toBeInstanceOf(AppError);
  });

  test('consumePostStream stops cleanly when aborted', async () => {
    const controller = new AbortController();
    controller.abort();

    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new DOMException('The user aborted a request.', 'AbortError'))
    );

    await expect(
      consumePostStream({
        path: '/api/interview/sessions/demo/answers',
        body: { answer_text: 'test' },
        signal: controller.signal,
        dispatcher: createStreamDispatcher<DemoEvent>()
      })
    ).resolves.toBeUndefined();
  });
});
