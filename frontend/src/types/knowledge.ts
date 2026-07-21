export type KnowledgeUploadResponse = {
  knowledge_base_id: string;
  document_id: string;
  name: string;
  category: string;
  source_type: string;
  skill_id: string | null;
  file_name: string;
  file_size: number;
  index_status: string;
  chunk_count: number;
};

export type AdminKnowledgeBatchUploadItem = {
  file_name: string;
  success: boolean;
  document_id: string;
  index_status: string;
  file_size: number;
  chunk_count: number;
  error_message: string;
};

export type AdminKnowledgeBatchUploadResponse = {
  knowledge_base_id: string;
  total_files: number;
  success_count: number;
  failed_count: number;
  items: AdminKnowledgeBatchUploadItem[];
};

export type KnowledgeSearchRequest = {
  query: string;
  knowledge_base_id?: string | null;
  knowledge_base_ids?: string[];
  category?: string | null;
  top_k?: number | null;
  score_threshold?: number | null;
  rewrite_enabled?: boolean | null;
};

export type RagSearchHit = {
  knowledge_base_id: string;
  document_id: string;
  category: string;
  source_type: string;
  skill_id: string | null;
  file_name: string;
  source: string;
  score: number;
  content: string;
};

export type KnowledgeSearchResponse = {
  query: string;
  rewritten_query: string;
  used_rewrite: boolean;
  top_k: number;
  score_threshold: number | null;
  hits: RagSearchHit[];
  message: string;
};

export type KnowledgeBaseSummaryDto = {
  id: string;
  name: string;
  description: string | null;
  category: string;
  skill_id: string | null;
  document_count: number;
  created_at: string | null;
  updated_at: string | null;
};

export type KnowledgeDocumentSummaryDto = {
  id: string;
  knowledge_base_id: string;
  original_file_name: string;
  file_extension: string;
  mime_type: string;
  file_size: number;
  source_type: string;
  category: string;
  skill_id: string | null;
  index_status: string;
  uploaded_at: string | null;
  indexed_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type KnowledgeBaseListResponse = {
  items: KnowledgeBaseSummaryDto[];
};

export type KnowledgeBaseDetailResponse = {
  knowledge_base: KnowledgeBaseSummaryDto;
  documents: KnowledgeDocumentSummaryDto[];
};

export type AdminKnowledgeBaseDto = {
  id: string;
  name: string;
  description: string | null;
  category: string;
  skill_id: string | null;
  source_type: string;
  status: string;
  is_enabled: boolean;
  document_count: number;
  metadata_json: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
};

export type AdminKnowledgeDocumentDto = {
  id: string;
  knowledge_base_id: string;
  original_file_name: string;
  storage_path: string;
  file_extension: string;
  mime_type: string;
  file_size: number;
  source_type: string;
  category: string;
  skill_id: string | null;
  index_status: string;
  is_enabled: boolean;
  error_message: string | null;
  metadata_json: Record<string, unknown>;
  uploaded_at: string | null;
  indexed_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type AdminKnowledgeBaseListResponse = {
  items: AdminKnowledgeBaseDto[];
};

export type AdminKnowledgeBaseDetailResponse = {
  knowledge_base: AdminKnowledgeBaseDto;
  documents: AdminKnowledgeDocumentDto[];
};

export type AdminKnowledgeBaseCreateRequest = {
  name: string;
  category: string;
  description?: string | null;
  skill_id?: string | null;
};

export type AdminKnowledgeBaseUpdateRequest = Partial<{
  name: string;
  description: string | null;
  category: string;
  skill_id: string | null;
  is_enabled: boolean;
  status: string;
}>;

export type AdminKnowledgeDocumentUpdateRequest = {
  is_enabled: boolean;
};

export type AdminKnowledgeReindexItem = {
  document_id: string;
  file_name: string;
  success: boolean;
  chunk_count: number;
  error_message: string;
};

export type AdminKnowledgeReindexResponse = {
  knowledge_base_id: string;
  total_documents: number;
  success_count: number;
  failed_count: number;
  items: AdminKnowledgeReindexItem[];
};
