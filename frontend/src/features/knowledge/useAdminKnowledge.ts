import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  createAdminKnowledgeBase,
  deleteAdminKnowledgeBase,
  deleteAdminKnowledgeDocument,
  getAdminKnowledgeBaseDetail,
  listAdminKnowledgeBases,
  reindexAdminKnowledgeBase,
  searchAdminKnowledge,
  updateAdminKnowledgeBase,
  updateAdminKnowledgeDocument,
  uploadAdminKnowledgeDocument,
  uploadAdminKnowledgeDocuments
} from '../../services/api';
import type {
  AdminKnowledgeBaseCreateRequest,
  AdminKnowledgeBaseUpdateRequest,
  AdminKnowledgeDocumentUpdateRequest,
  KnowledgeSearchRequest
} from '../../types';

const adminKnowledgeKeys = {
  bases: (adminToken: string) => ['adminKnowledge', 'bases', Boolean(adminToken)] as const,
  detail: (adminToken: string, knowledgeBaseId: string | null) =>
    ['adminKnowledge', 'detail', Boolean(adminToken), knowledgeBaseId] as const
};

export function useAdminKnowledgeBases(adminToken: string) {
  return useQuery({
    queryKey: adminKnowledgeKeys.bases(adminToken),
    queryFn: () =>
      listAdminKnowledgeBases({
        adminToken,
        limit: 100
      }),
    enabled: Boolean(adminToken)
  });
}

export function useAdminKnowledgeBaseDetail(adminToken: string, knowledgeBaseId: string | null) {
  return useQuery({
    queryKey: adminKnowledgeKeys.detail(adminToken, knowledgeBaseId),
    queryFn: () => getAdminKnowledgeBaseDetail(knowledgeBaseId as string, { adminToken }),
    enabled: Boolean(adminToken && knowledgeBaseId)
  });
}

export function useCreateAdminKnowledgeBase(adminToken: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: AdminKnowledgeBaseCreateRequest) =>
      createAdminKnowledgeBase(payload, { adminToken }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['adminKnowledge', 'bases'] });
    }
  });
}

export function useUpdateAdminKnowledgeBase(adminToken: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      knowledgeBaseId,
      payload
    }: {
      knowledgeBaseId: string;
      payload: AdminKnowledgeBaseUpdateRequest;
    }) => updateAdminKnowledgeBase(knowledgeBaseId, payload, { adminToken }),
    onSuccess: async (knowledgeBase) => {
      await queryClient.invalidateQueries({ queryKey: ['adminKnowledge', 'bases'] });
      await queryClient.invalidateQueries({
        queryKey: ['adminKnowledge', 'detail', Boolean(adminToken), knowledgeBase.id]
      });
    }
  });
}

export function useUploadAdminKnowledgeDocument(adminToken: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ knowledgeBaseId, file }: { knowledgeBaseId: string; file: File }) =>
      uploadAdminKnowledgeDocument(knowledgeBaseId, file, { adminToken }),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['adminKnowledge', 'bases'] });
      await queryClient.invalidateQueries({
        queryKey: ['adminKnowledge', 'detail', Boolean(adminToken), result.knowledge_base_id]
      });
    }
  });
}

export function useUploadAdminKnowledgeDocuments(adminToken: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ knowledgeBaseId, files }: { knowledgeBaseId: string; files: File[] }) =>
      uploadAdminKnowledgeDocuments(knowledgeBaseId, files, { adminToken }),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['adminKnowledge', 'bases'] });
      await queryClient.invalidateQueries({
        queryKey: ['adminKnowledge', 'detail', Boolean(adminToken), result.knowledge_base_id]
      });
    }
  });
}

export function useUpdateAdminKnowledgeDocument(adminToken: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      knowledgeBaseId,
      documentId,
      payload
    }: {
      knowledgeBaseId: string;
      documentId: string;
      payload: AdminKnowledgeDocumentUpdateRequest;
    }) => updateAdminKnowledgeDocument(documentId, payload, { adminToken }).then((document) => ({
      knowledgeBaseId,
      document
    })),
    onSuccess: async ({ knowledgeBaseId }) => {
      await queryClient.invalidateQueries({
        queryKey: ['adminKnowledge', 'detail', Boolean(adminToken), knowledgeBaseId]
      });
    }
  });
}

export function useDeleteAdminKnowledgeDocument(adminToken: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ knowledgeBaseId, documentId }: { knowledgeBaseId: string; documentId: string }) =>
      deleteAdminKnowledgeDocument(documentId, { adminToken }).then((document) => ({
        knowledgeBaseId,
        document
      })),
    onSuccess: async ({ knowledgeBaseId }) => {
      await queryClient.invalidateQueries({
        queryKey: ['adminKnowledge', 'detail', Boolean(adminToken), knowledgeBaseId]
      });
    }
  });
}

export function useDeleteAdminKnowledgeBase(adminToken: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (knowledgeBaseId: string) =>
      deleteAdminKnowledgeBase(knowledgeBaseId, { adminToken }),
    onSuccess: async (knowledgeBase) => {
      await queryClient.invalidateQueries({ queryKey: ['adminKnowledge', 'bases'] });
      await queryClient.invalidateQueries({
        queryKey: ['adminKnowledge', 'detail', Boolean(adminToken), knowledgeBase.id]
      });
    }
  });
}

export function useReindexAdminKnowledgeBase(adminToken: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (knowledgeBaseId: string) => reindexAdminKnowledgeBase(knowledgeBaseId, { adminToken }),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({
        queryKey: ['adminKnowledge', 'detail', Boolean(adminToken), result.knowledge_base_id]
      });
    }
  });
}

export function useSearchAdminKnowledge(adminToken: string) {
  return useMutation({
    mutationFn: (payload: KnowledgeSearchRequest) => searchAdminKnowledge(payload, { adminToken })
  });
}
