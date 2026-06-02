import { AppError } from '../../types';

import type { ParsedSseMessage } from './streamTypes';

export type ParsedSseChunkResult = {
  messages: ParsedSseMessage[];
  remainder: string;
};

export function parseSseChunk(chunk: string, remainder = ''): ParsedSseChunkResult {
  // EventSourceResponse defaults to CRLF separators, so normalize before framing.
  const combined = `${remainder}${chunk}`.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const frames = combined.split('\n\n');
  const nextRemainder = frames.pop() ?? '';

  const messages = frames
    .map((frame) => frame.split('\n').filter((line) => !line.startsWith(':')).join('\n').trim())
    .filter(Boolean)
    .map((frame) => {
      const lines = frame.split('\n');
      const eventLine = lines.find((line) => line.startsWith('event:'));
      const dataLines = lines
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart());

      return {
        event: eventLine ? eventLine.slice(6).trim() : 'message',
        data: dataLines.join('\n')
      };
    });

  return {
    messages,
    remainder: nextRemainder
  };
}

export function parseJsonEvent<TEvent>(rawData: string): TEvent {
  try {
    return JSON.parse(rawData) as TEvent;
  } catch (error) {
    throw new AppError({
      code: 1005,
      message: '流式事件 JSON 解析失败',
      data: {
        rawData,
        cause: error instanceof Error ? error.message : 'unknown'
      }
    });
  }
}
