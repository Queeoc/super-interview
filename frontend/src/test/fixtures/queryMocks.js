var __assign = (this && this.__assign) || function () {
    __assign = Object.assign || function(t) {
        for (var s, i = 1, n = arguments.length; i < n; i++) {
            s = arguments[i];
            for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p))
                t[p] = s[p];
        }
        return t;
    };
    return __assign.apply(this, arguments);
};
import { vi } from 'vitest';
export function createQuerySuccessResult(data) {
    return {
        data: data,
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
    };
}
export function createQueryLoadingResult() {
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
    };
}
export function createMutationResult(overrides) {
    if (overrides === void 0) { overrides = {}; }
    return __assign({ data: undefined, error: null, variables: undefined, status: 'idle', isError: false, isIdle: true, isPending: false, isPaused: false, isSuccess: false, failureCount: 0, failureReason: null, submittedAt: 0, mutate: vi.fn(), mutateAsync: vi.fn(), reset: vi.fn(), context: undefined }, overrides);
}
