import { useMutation, useQuery } from '@tanstack/react-query';

import { getPublicKnowledgeBaseDetail, listPublicKnowledgeBases, searchPublicKnowledge } from '../../services/api';
import type { KnowledgeSearchRequest } from '../../types';

const knowledgeKeys = {
  bases: ['knowledge', 'bases'] as const,
  detail: (knowledgeBaseId: string | null) => ['knowledge', 'detail', knowledgeBaseId] as const
};

export function usePublicKnowledgeBases() {
  return useQuery({
    queryKey: knowledgeKeys.bases,
    queryFn: () => listPublicKnowledgeBases({ limit: 100 })
  });
}

export function usePublicKnowledgeBaseDetail(knowledgeBaseId: string | null) {
  return useQuery({
    queryKey: knowledgeKeys.detail(knowledgeBaseId),
    queryFn: () => getPublicKnowledgeBaseDetail(knowledgeBaseId as string),
    enabled: Boolean(knowledgeBaseId)
  });
}

export function usePublicKnowledgeSearch() {
  return useMutation({
    mutationFn: (payload: KnowledgeSearchRequest) => searchPublicKnowledge(payload)
  });
}
