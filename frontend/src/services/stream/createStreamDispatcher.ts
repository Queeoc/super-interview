import { AppError } from '../../types';

import { parseJsonEvent } from './parseSseChunk';
import type { ParsedSseMessage, StreamDispatcher } from './streamTypes';

export function createStreamDispatcher<TEvent extends { type?: string }>() : StreamDispatcher<TEvent> {
  return {
    handleRawMessage(message: ParsedSseMessage) {
      if (!message.data.trim()) {
        return null;
      }

      const event = parseJsonEvent<TEvent>(message.data);

      if (!event || typeof event !== 'object' || typeof event.type !== 'string') {
        throw new AppError({
          code: 1005,
          message: '流式事件缺少合法 type 字段',
          data: { rawData: message.data }
        });
      }

      return event;
    }
  };
}
