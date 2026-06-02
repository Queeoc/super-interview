import type { ResumeDetailDto, ResumeSummaryDto, ResumeUploadResponse } from '../../../types';
import { apiClient } from '../apiClient';

export function uploadResume(file: File) {
  const formData = new FormData();
  formData.append('file', file);
  return apiClient.upload<ResumeUploadResponse>('/api/resumes/upload', formData);
}

export function listResumes() {
  return apiClient.get<ResumeSummaryDto[]>('/api/resumes');
}

export function getResumeDetail(resumeId: string) {
  return apiClient.get<ResumeDetailDto>(`/api/resumes/${resumeId}`);
}
