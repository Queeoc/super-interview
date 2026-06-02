import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  completeInterviewSession,
  createInterviewSession,
  getInterviewReport,
  getInterviewReportExport,
  getInterviewSession,
  listInterviewSessions,
  saveInterviewDraft
} from '../../services/api';
import type { CreateInterviewRequest, SubmitAnswerRequest } from '../../types';

export function useCreateInterviewSession() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: CreateInterviewRequest) => createInterviewSession(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['interview', 'sessions'] });
    }
  });
}

export function useInterviewSessionsList() {
  return useQuery({
    queryKey: ['interview', 'sessions'],
    queryFn: listInterviewSessions,
    refetchOnWindowFocus: false
  });
}

export function useInterviewSession(sessionId: string | null) {
  return useQuery({
    queryKey: ['interview', 'session', sessionId],
    queryFn: () => getInterviewSession(sessionId as string),
    enabled: Boolean(sessionId),
    refetchOnWindowFocus: false
  });
}

export function useInterviewReport(sessionId: string | null) {
  return useQuery({
    queryKey: ['interview', 'report', sessionId],
    queryFn: () => getInterviewReport(sessionId as string),
    enabled: Boolean(sessionId),
    refetchOnWindowFocus: false
  });
}

export function useInterviewReportExport(sessionId: string | null) {
  return useQuery({
    queryKey: ['interview', 'report-export', sessionId],
    queryFn: () => getInterviewReportExport(sessionId as string),
    enabled: Boolean(sessionId),
    refetchOnWindowFocus: false
  });
}

export function useSaveInterviewDraft(sessionId: string | null) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: SubmitAnswerRequest) => saveInterviewDraft(sessionId as string, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['interview', 'session', sessionId] });
    }
  });
}

export function useCompleteInterviewSession(sessionId: string | null) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => completeInterviewSession(sessionId as string),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['interview', 'session', sessionId] }),
        queryClient.invalidateQueries({ queryKey: ['interview', 'report', sessionId] }),
        queryClient.invalidateQueries({ queryKey: ['interview', 'report-export', sessionId] })
      ]);
    }
  });
}
