import type { UseMutationResult, UseQueryResult } from '@tanstack/react-query';
import { vi } from 'vitest';

export function createQuerySuccessResult<T>(data: T): UseQueryResult<T, Error> {
  return {
    data,
    error: null,
    isLoading: false,
    isError: false,
    isPending: false,
    isSuccess: true,
    isLoadingError: false,
    isRefetchError: false,
    isFetched: true,
    isFetchedAfterMount: true,
    isFetching: false,
    isInitialLoading: false,
    isPaused: false,
    isPlaceholderData: false,
    isRefetching: false,
    isStale: false,
    refetch: vi.fn(),
    status: 'success',
    fetchStatus: 'idle',
    dataUpdatedAt: Date.now(),
    errorUpdatedAt: 0,
    failureCount: 0,
    failureReason: null,
    errorUpdateCount: 0,
    isEnabled: true,
    isInitialData: false,
    promise: Promise.resolve(data),
    remove: vi.fn()
  } as unknown as UseQueryResult<T, Error>;
}

export function createQueryLoadingResult<T>(): UseQueryResult<T, Error> {
  return {
    data: undefined,
    error: null,
    isLoading: true,
    isError: false,
    isPending: true,
    isSuccess: false,
    isLoadingError: false,
    isRefetchError: false,
    isFetched: false,
    isFetchedAfterMount: false,
    isFetching: true,
    isInitialLoading: true,
    isPaused: false,
    isPlaceholderData: false,
    isRefetching: false,
    isStale: true,
    refetch: vi.fn(),
    status: 'pending',
    fetchStatus: 'fetching',
    dataUpdatedAt: 0,
    errorUpdatedAt: 0,
    failureCount: 0,
    failureReason: null,
    errorUpdateCount: 0,
    isEnabled: true,
    isInitialData: false,
    promise: Promise.resolve(undefined),
    remove: vi.fn()
  } as unknown as UseQueryResult<T, Error>;
}

export function createMutationResult<TData, TVariables>(
  overrides: Partial<UseMutationResult<TData, Error, TVariables, unknown>> = {}
): UseMutationResult<TData, Error, TVariables, unknown> {
  return {
    data: undefined,
    error: null,
    variables: undefined,
    status: 'idle',
    isError: false,
    isIdle: true,
    isPending: false,
    isPaused: false,
    isSuccess: false,
    failureCount: 0,
    failureReason: null,
    submittedAt: 0,
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    context: undefined,
    ...overrides
  } as unknown as UseMutationResult<TData, Error, TVariables, unknown>;
}
