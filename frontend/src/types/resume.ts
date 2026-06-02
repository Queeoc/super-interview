export type ResumeSummaryDto = {
  resume_id: string;
  original_file_name: string;
  file_extension: string;
  mime_type: string;
  file_size: number;
  status: string;
  uploaded_at: string;
  updated_at: string;
};

export type ResumeDetailDto = ResumeSummaryDto & {
  markdown_content: string;
  source_metadata: Record<string, unknown>;
};

export type ResumeUploadResponse = {
  resume: ResumeDetailDto;
  reused_existing: boolean;
};
