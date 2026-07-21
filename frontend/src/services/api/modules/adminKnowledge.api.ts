import type {
  AdminKnowledgeBatchUploadResponse,
  AdminKnowledgeBaseCreateRequest,
  AdminKnowledgeBaseDetailResponse,
  AdminKnowledgeBaseDto,
  AdminKnowledgeBaseListResponse,
  AdminKnowledgeBaseUpdateRequest,
  AdminKnowledgeDocumentDto,
  AdminKnowledgeDocumentUpdateRequest,
  AdminKnowledgeReindexResponse,
  KnowledgeSearchRequest,
  KnowledgeSearchResponse,
  KnowledgeUploadResponse
} from '../../../types';
import { apiClient } from '../apiClient';

type AdminRequestOptions = {
  adminToken: string;
};

function buildAdminHeaders(adminToken: string): HeadersInit {
  return {
    'X-Admin-Token': adminToken
  };
}

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

export function listAdminKnowledgeBases(
  options: AdminRequestOptions & {
    category?: string | null;
    skillId?: string | null;
    enabledOnly?: boolean;
    limit?: number;
  }
) {
  const query = buildQuery({
    category: options.category,
    skill_id: options.skillId,
    enabled_only: options.enabledOnly,
    limit: options.limit
  });
  return apiClient.get<AdminKnowledgeBaseListResponse>(`/api/admin/knowledge/bases${query}`, {
    headers: buildAdminHeaders(options.adminToken)
  });
}

export function createAdminKnowledgeBase(
  payload: AdminKnowledgeBaseCreateRequest,
  options: AdminRequestOptions
) {
  return apiClient.post<AdminKnowledgeBaseDto, AdminKnowledgeBaseCreateRequest>(
    '/api/admin/knowledge/bases',
    payload,
    {
      headers: buildAdminHeaders(options.adminToken)
    }
  );
}

export function getAdminKnowledgeBaseDetail(
  knowledgeBaseId: string,
  options: AdminRequestOptions
) {
  return apiClient.get<AdminKnowledgeBaseDetailResponse>(
    `/api/admin/knowledge/bases/${knowledgeBaseId}`,
    {
      headers: buildAdminHeaders(options.adminToken)
    }
  );
}

export function updateAdminKnowledgeBase(
  knowledgeBaseId: string,
  payload: AdminKnowledgeBaseUpdateRequest,
  options: AdminRequestOptions
) {
  return apiClient.patch<AdminKnowledgeBaseDto, AdminKnowledgeBaseUpdateRequest>(
    `/api/admin/knowledge/bases/${knowledgeBaseId}`,
    payload,
    {
      headers: buildAdminHeaders(options.adminToken)
    }
  );
}

export function uploadAdminKnowledgeDocument(
  knowledgeBaseId: string,
  file: File,
  options: AdminRequestOptions
) {
  const formData = new FormData();
  formData.append('file', file);
  return apiClient.upload<KnowledgeUploadResponse>(
    `/api/admin/knowledge/bases/${knowledgeBaseId}/documents`,
    formData,
    {
      headers: buildAdminHeaders(options.adminToken)
    }
  );
}

export function uploadAdminKnowledgeDocuments(
  knowledgeBaseId: string,
  files: File[],
  options: AdminRequestOptions
) {
  const formData = new FormData();
  files.forEach((file) => {
    formData.append('files', file);
  });
  return apiClient.upload<AdminKnowledgeBatchUploadResponse>(
    `/api/admin/knowledge/bases/${knowledgeBaseId}/documents/batch`,
    formData,
    {
      headers: buildAdminHeaders(options.adminToken)
    }
  );
}

export function updateAdminKnowledgeDocument(
  documentId: string,
  payload: AdminKnowledgeDocumentUpdateRequest,
  options: AdminRequestOptions
) {
  return apiClient.patch<AdminKnowledgeDocumentDto, AdminKnowledgeDocumentUpdateRequest>(
    `/api/admin/knowledge/documents/${documentId}`,
    payload,
    {
      headers: buildAdminHeaders(options.adminToken)
    }
  );
}

export function deleteAdminKnowledgeDocument(documentId: string, options: AdminRequestOptions) {
  return apiClient.delete<AdminKnowledgeDocumentDto>(
    `/api/admin/knowledge/documents/${documentId}`,
    {
      headers: buildAdminHeaders(options.adminToken)
    }
  );
}

export function deleteAdminKnowledgeBase(knowledgeBaseId: string, options: AdminRequestOptions) {
  return apiClient.delete<AdminKnowledgeBaseDto>(
    `/api/admin/knowledge/bases/${knowledgeBaseId}`,
    {
      headers: buildAdminHeaders(options.adminToken)
    }
  );
}

export function reindexAdminKnowledgeBase(knowledgeBaseId: string, options: AdminRequestOptions) {
  return apiClient.post<AdminKnowledgeReindexResponse>(
    `/api/admin/knowledge/bases/${knowledgeBaseId}/reindex`,
    undefined,
    {
      headers: buildAdminHeaders(options.adminToken)
    }
  );
}

export function searchAdminKnowledge(payload: KnowledgeSearchRequest, options: AdminRequestOptions) {
  return apiClient.post<KnowledgeSearchResponse, KnowledgeSearchRequest>(
    '/api/admin/knowledge/search',
    payload,
    {
      headers: buildAdminHeaders(options.adminToken)
    }
  );
}
