import { useQuery } from '@tanstack/react-query';

import { getSkillDetail, getSkillReferenceSection, listSkills } from '../../services/api';

export function useSkillsList() {
  return useQuery({
    queryKey: ['skills', 'list'],
    queryFn: listSkills
  });
}

export function useSkillDetail(skillId: string | null) {
  return useQuery({
    queryKey: ['skills', 'detail', skillId],
    queryFn: () => getSkillDetail(skillId as string),
    enabled: Boolean(skillId)
  });
}

export function useSkillReferenceSection(skillId: string | null) {
  return useQuery({
    queryKey: ['skills', 'reference', skillId],
    queryFn: () => getSkillReferenceSection(skillId as string),
    enabled: Boolean(skillId)
  });
}
