import { ApiEnvelope, AppError } from '../../types';

const BUSINESS_SUCCESS_CODE = 200;

type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

type RequestOptions = {
  method?: HttpMethod;
  body?: BodyInit | null;
  headers?: HeadersInit;
  signal?: AbortSignal;
};

const DIRECT_API_BASE_URL = import.meta.env.VITE_API_BASE_URL?.trim() ?? '';

function buildUrl(path: string): string {
  if (/^https?:\/\//.test(path)) {
    return path;
  }

  if (!DIRECT_API_BASE_URL) {
    return path;
  }

  const base = DIRECT_API_BASE_URL.endsWith('/')
    ? DIRECT_API_BASE_URL.slice(0, -1)
    : DIRECT_API_BASE_URL;
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${base}${normalizedPath}`;
}

async function parseEnvelope<T>(response: Response): Promise<T> {
  let payload: ApiEnvelope<T> | null = null;

  try {
    payload = (await response.json()) as ApiEnvelope<T>;
  } catch (error) {
    throw new AppError({
      code: response.status,
      message: '响应不是合法 JSON',
      status: response.status,
      data: { cause: error instanceof Error ? error.message : 'unknown' }
    });
  }

  if (!response.ok) {
    throw new AppError({
      code: payload.code ?? response.status,
      message: payload.message ?? '请求失败',
      status: response.status,
      data: (payload.data as Record<string, unknown>) ?? {}
    });
  }

  if (payload.code !== BUSINESS_SUCCESS_CODE) {
    throw new AppError({
      code: payload.code,
      message: payload.message,
      status: response.status,
      data: (payload.data as Record<string, unknown>) ?? {}
    });
  }

  return payload.data;
}

function buildHeaders(body: BodyInit | null | undefined, headers?: HeadersInit): Headers {
  const resolvedHeaders = new Headers(headers);

  if (body instanceof FormData) {
    return resolvedHeaders;
  }

  if (!resolvedHeaders.has('Content-Type')) {
    resolvedHeaders.set('Content-Type', 'application/json');
  }

  return resolvedHeaders;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body = null, headers, signal } = options;
  const response = await fetch(buildUrl(path), {
    method,
    body,
    headers: buildHeaders(body, headers),
    signal,
    credentials: 'include'
  });

  return parseEnvelope<T>(response);
}

function get<T>(path: string, options: Omit<RequestOptions, 'method' | 'body'> = {}) {
  return request<T>(path, { ...options, method: 'GET' });
}

function post<TResponse, TRequest = unknown>(
  path: string,
  body?: TRequest,
  options: Omit<RequestOptions, 'method' | 'body'> = {}
) {
  return request<TResponse>(path, {
    ...options,
    method: 'POST',
    body: body === undefined ? null : JSON.stringify(body)
  });
}

function upload<TResponse>(
  path: string,
  formData: FormData,
  options: Omit<RequestOptions, 'method' | 'body'> = {}
) {
  return request<TResponse>(path, {
    ...options,
    method: 'POST',
    body: formData
  });
}

export const apiClient = {
  request,
  get,
  post,
  upload,
  buildUrl
};
