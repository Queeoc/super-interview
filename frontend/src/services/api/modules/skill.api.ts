import type { SkillDetailDto, SkillReferenceSectionResponse, SkillSummaryDto } from '../../../types';
import { apiClient } from '../apiClient';

export function listSkills() {
  return apiClient.get<SkillSummaryDto[]>('/api/skills');
}

export function getSkillDetail(skillId: string) {
  return apiClient.get<SkillDetailDto>(`/api/skills/${skillId}`);
}

export function getSkillReferenceSection(skillId: string) {
  return apiClient.get<SkillReferenceSectionResponse>(`/api/skills/${skillId}/reference-section`);
}
