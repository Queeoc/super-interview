import { AppError } from '../../types';

export type ParsedSseMessage = {
  event: string;
  data: string;
};

export type StreamDispatcher<TEvent> = {
  handleRawMessage: (message: ParsedSseMessage) => TEvent | null;
};

export type StreamConsumerCallbacks<TEvent> = {
  onEvent?: (event: TEvent) => void;
  onError?: (error: AppError) => void;
  onDone?: () => void;
};
