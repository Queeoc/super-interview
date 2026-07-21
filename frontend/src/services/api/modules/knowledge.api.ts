import type {
  KnowledgeBaseDetailResponse,
  KnowledgeBaseListResponse,
  KnowledgeSearchRequest,
  KnowledgeSearchResponse
} from '../../../types';
import { apiClient } from '../apiClient';

function buildQuery(params: Record<string, string | number | boolean | null | undefined>) {
  const searchParams = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === null || value === undefined || value === '') {
      return;
    }
    searchParams.set(key, String(value));
  });
  const query = searchParams.toString();
  return query ? `?${query}` : '';
}

export function listPublicKnowledgeBases(options?: {
  category?: string | null;
  skillId?: string | null;
  limit?: number;
}) {
  const query = buildQuery({
    category: options?.category,
    skill_id: options?.skillId,
    limit: options?.limit
  });
  return apiClient.get<KnowledgeBaseListResponse>(`/api/knowledge/bases${query}`);
}

export function getPublicKnowledgeBaseDetail(knowledgeBaseId: string) {
  return apiClient.get<KnowledgeBaseDetailResponse>(`/api/knowledge/bases/${knowledgeBaseId}`);
}

export function searchPublicKnowledge(payload: KnowledgeSearchRequest) {
  return apiClient.post<KnowledgeSearchResponse, KnowledgeSearchRequest>('/api/knowledge/search', payload);
}
