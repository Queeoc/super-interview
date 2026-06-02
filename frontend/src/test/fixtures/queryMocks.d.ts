import type { UseMutationResult, UseQueryResult } from '@tanstack/react-query';
export declare function createQuerySuccessResult<T>(data: T): UseQueryResult<T, Error>;
export declare function createQueryLoadingResult<T>(): UseQueryResult<T, Error>;
export declare function createMutationResult<TData, TVariables>(overrides?: Partial<UseMutationResult<TData, Error, TVariables, unknown>>): UseMutationResult<TData, Error, TVariables, unknown>;
