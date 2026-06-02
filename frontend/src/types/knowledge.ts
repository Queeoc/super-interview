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
