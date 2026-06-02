export type SkillCategoryDto = {
  key: string;
  label: string;
  priority: string;
  ref: string | null;
  shared: boolean;
};

export type SkillReferenceDto = {
  file_name: string;
  title: string;
  shared: boolean;
  resolved_path: string;
  category_keys: string[];
};

export type SkillSummaryDto = {
  skill_id: string;
  display_name: string;
  description: string;
  display: Record<string, string>;
  categories: SkillCategoryDto[];
};

export type SkillDetailDto = SkillSummaryDto & {
  content_markdown: string;
  references: SkillReferenceDto[];
  enabled_tools: string[];
  question_preferences: Record<string, unknown>;
};

export type SkillReferenceSectionResponse = {
  skill_id: string;
  reference_markdown: string;
  resolved_reference_files: string[];
};
