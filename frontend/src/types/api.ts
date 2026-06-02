export type ApiEnvelope<T> = {
  code: number;
  message: string;
  data: T;
};

export type AppErrorData = Record<string, unknown>;

export class AppError extends Error {
  code: number;
  data: AppErrorData;
  status: number;

  constructor({
    code,
    message,
    data = {},
    status = 500
  }: {
    code: number;
    message: string;
    data?: AppErrorData;
    status?: number;
  }) {
    super(message);
    this.name = 'AppError';
    this.code = code;
    this.data = data;
    this.status = status;
  }
}
