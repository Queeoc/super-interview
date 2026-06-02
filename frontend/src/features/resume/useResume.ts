import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { getResumeDetail, listResumes, uploadResume } from '../../services/api';

export function useResumeList() {
  return useQuery({
    queryKey: ['resume', 'list'],
    queryFn: listResumes
  });
}

export function useResumeDetail(resumeId: string | null) {
  return useQuery({
    queryKey: ['resume', 'detail', resumeId],
    queryFn: () => getResumeDetail(resumeId as string),
    enabled: Boolean(resumeId)
  });
}

export function useUploadResume() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: uploadResume,
    onSuccess: async (payload) => {
      await queryClient.invalidateQueries({ queryKey: ['resume', 'list'] });
      queryClient.setQueryData(['resume', 'detail', payload.resume.resume_id], payload.resume);
    }
  });
}
