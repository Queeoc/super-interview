import type {
  CreateInterviewRequest,
  InterviewReportDto,
  InterviewReportExportDto,
  InterviewSessionSummaryDto,
  InterviewSessionDto,
  InterviewStreamEvent,
  SubmitAnswerRequest,
  SubmitAnswerResponse
} from '../../../types';
import { createStreamDispatcher, consumePostStream } from '../../stream';
import { apiClient } from '../apiClient';

export function createInterviewSession(payload: CreateInterviewRequest) {
  return apiClient.post<InterviewSessionDto, CreateInterviewRequest>('/api/interview/sessions', payload);
}

export function listInterviewSessions() {
  return apiClient.get<InterviewSessionSummaryDto[]>('/api/interview/sessions');
}

export function getInterviewSession(sessionId: string) {
  return apiClient.get<InterviewSessionDto>(`/api/interview/sessions/${sessionId}`);
}

export function saveInterviewDraft(sessionId: string, payload: SubmitAnswerRequest) {
  return apiClient.post<SubmitAnswerResponse, SubmitAnswerRequest>(
    `/api/interview/sessions/${sessionId}/answers/draft`,
    payload
  );
}

export function submitInterviewAnswerStream(
  sessionId: string,
  payload: SubmitAnswerRequest,
  options: {
    signal?: AbortSignal;
    onEvent?: (event: InterviewStreamEvent) => void;
    onError?: (error: Error) => void;
    onDone?: () => void;
  }
) {
  return consumePostStream<SubmitAnswerRequest, InterviewStreamEvent>({
    path: `/api/interview/sessions/${sessionId}/answers`,
    body: payload,
    signal: options.signal,
    dispatcher: createStreamDispatcher<InterviewStreamEvent>(),
    onEvent: options.onEvent,
    onError: options.onError,
    onDone: options.onDone
  });
}

export function completeInterviewSession(sessionId: string) {
  return apiClient.post<InterviewSessionDto>(`/api/interview/sessions/${sessionId}/complete`);
}

export function getInterviewReport(sessionId: string) {
  return apiClient.get<InterviewReportDto>(`/api/interview/sessions/${sessionId}/report`);
}

export function getInterviewReportExport(sessionId: string) {
  return apiClient.get<InterviewReportExportDto>(`/api/interview/sessions/${sessionId}/report/export`);
}
