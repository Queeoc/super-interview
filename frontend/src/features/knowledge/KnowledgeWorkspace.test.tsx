import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, test, vi } from 'vitest';

import { KnowledgeWorkspace } from './KnowledgeWorkspace';
import * as adminKnowledgeHooks from './useAdminKnowledge';
import * as publicKnowledgeHooks from './useKnowledge';
import * as skillsHooks from '../skills/useSkills';
import { createMutationResult, createQuerySuccessResult } from '../../test/fixtures/queryMocks';
import { renderWithProviders } from '../../test/testUtils';
import type {
  AdminKnowledgeBaseDto,
  AdminKnowledgeDocumentDto,
  KnowledgeBaseDetailResponse,
  KnowledgeBaseListResponse
} from '../../types';

const publicKnowledgeBase = {
  id: 'kb-public-1',
  name: 'Python 核心知识库',
  description: 'Python 面试知识',
  category: 'reference_knowledge',
  skill_id: 'python-backend',
  document_count: 1,
  created_at: '2026-07-01T10:00:00Z',
  updated_at: '2026-07-01T10:00:00Z'
};

const publicDocument = {
  id: 'doc-public-1',
  knowledge_base_id: 'kb-public-1',
  original_file_name: 'python-basic.md',
  file_extension: '.md',
  mime_type: 'text/markdown',
  file_size: 128,
  source_type: 'admin',
  category: 'reference_knowledge',
  skill_id: 'python-backend',
  index_status: 'indexed',
  uploaded_at: '2026-07-01T10:00:00Z',
  indexed_at: '2026-07-01T10:01:00Z',
  created_at: '2026-07-01T10:00:00Z',
  updated_at: '2026-07-01T10:01:00Z'
};

const adminKnowledgeBase: AdminKnowledgeBaseDto = {
  id: 'kb-admin-1',
  name: 'Python 管理知识库',
  description: 'Python 管理知识库',
  category: 'reference_knowledge',
  skill_id: 'python-backend',
  source_type: 'admin',
  status: 'active',
  is_enabled: true,
  document_count: 1,
  metadata_json: {},
  created_at: '2026-07-01T10:00:00Z',
  updated_at: '2026-07-01T10:00:00Z'
};

const adminDocument: AdminKnowledgeDocumentDto = {
  id: 'doc-admin-1',
  knowledge_base_id: 'kb-admin-1',
  original_file_name: 'python.md',
  storage_path: '/tmp/python.md',
  file_extension: '.md',
  mime_type: 'text/markdown',
  file_size: 128,
  source_type: 'admin',
  category: 'reference_knowledge',
  skill_id: 'python-backend',
  index_status: 'indexed',
  is_enabled: true,
  error_message: null,
  metadata_json: { chunk_count: 2 },
  uploaded_at: '2026-07-01T10:00:00Z',
  indexed_at: '2026-07-01T10:01:00Z',
  created_at: '2026-07-01T10:00:00Z',
  updated_at: '2026-07-01T10:01:00Z'
};

function buildPublicDocument(index: number) {
  return {
    ...publicDocument,
    id: `doc-public-${index}`,
    original_file_name: `public-doc-${index}.md`
  };
}

function buildAdminDocument(index: number): AdminKnowledgeDocumentDto {
  return {
    ...adminDocument,
    id: `doc-admin-${index}`,
    original_file_name: `admin-doc-${index}.md`
  };
}

function mockKnowledgeHooks() {
  const publicSearchMutateAsync = vi.fn().mockResolvedValue({
    query: 'Python 解释器',
    rewritten_query: 'Python 解释器',
    used_rewrite: false,
    top_k: 5,
    score_threshold: null,
    hits: [],
    message: '没有命中'
  });
  const adminCreateMutateAsync = vi.fn().mockResolvedValue(adminKnowledgeBase);
  const adminUploadMutateAsync = vi.fn().mockResolvedValue({
    knowledge_base_id: 'kb-admin-1',
    total_files: 2,
    success_count: 2,
    failed_count: 0,
    items: [
      {
        file_name: 'python.md',
        success: true,
        document_id: 'doc-admin-1',
        index_status: 'indexed',
        file_size: 8,
        chunk_count: 1,
        error_message: ''
      },
      {
        file_name: 'java.md',
        success: true,
        document_id: 'doc-admin-2',
        index_status: 'indexed',
        file_size: 6,
        chunk_count: 1,
        error_message: ''
      }
    ]
  });
  const adminDeleteBaseMutateAsync = vi.fn().mockResolvedValue(adminKnowledgeBase);
  const adminSearchMutateAsync = vi.fn().mockResolvedValue({
    query: 'Python 解释器',
    rewritten_query: 'Python 解释器',
    used_rewrite: false,
    top_k: 5,
    score_threshold: null,
    hits: [],
    message: '没有命中'
  });

  vi.spyOn(publicKnowledgeHooks, 'usePublicKnowledgeBases').mockReturnValue(
    createQuerySuccessResult<KnowledgeBaseListResponse>({
      items: [publicKnowledgeBase]
    })
  );
  vi.spyOn(publicKnowledgeHooks, 'usePublicKnowledgeBaseDetail').mockReturnValue(
    createQuerySuccessResult<KnowledgeBaseDetailResponse>({
      knowledge_base: publicKnowledgeBase,
      documents: [publicDocument]
    })
  );
  vi.spyOn(publicKnowledgeHooks, 'usePublicKnowledgeSearch').mockReturnValue(
    createMutationResult({
      mutateAsync: publicSearchMutateAsync
    })
  );
  vi.spyOn(skillsHooks, 'useSkillsList').mockReturnValue(
    createQuerySuccessResult([
      {
        skill_id: 'python-backend',
        display_name: 'Python 后端开发',
        description: 'Python 后端开发',
        display: {},
        categories: []
      },
      {
        skill_id: 'java-backend',
        display_name: 'Java 后端开发',
        description: 'Java 后端开发',
        display: {},
        categories: []
      }
    ])
  );

  vi.spyOn(adminKnowledgeHooks, 'useAdminKnowledgeBases').mockReturnValue(
    createQuerySuccessResult({
      items: [adminKnowledgeBase]
    })
  );
  vi.spyOn(adminKnowledgeHooks, 'useAdminKnowledgeBaseDetail').mockReturnValue(
    createQuerySuccessResult({
      knowledge_base: adminKnowledgeBase,
      documents: [adminDocument]
    })
  );
  vi.spyOn(adminKnowledgeHooks, 'useCreateAdminKnowledgeBase').mockReturnValue(
    createMutationResult({
      mutateAsync: adminCreateMutateAsync
    })
  );
  vi.spyOn(adminKnowledgeHooks, 'useUpdateAdminKnowledgeBase').mockReturnValue(createMutationResult());
  vi.spyOn(adminKnowledgeHooks, 'useUploadAdminKnowledgeDocuments').mockReturnValue(
    createMutationResult({
      mutateAsync: adminUploadMutateAsync
    })
  );
  vi.spyOn(adminKnowledgeHooks, 'useUpdateAdminKnowledgeDocument').mockReturnValue(createMutationResult());
  vi.spyOn(adminKnowledgeHooks, 'useDeleteAdminKnowledgeDocument').mockReturnValue(createMutationResult());
  vi.spyOn(adminKnowledgeHooks, 'useDeleteAdminKnowledgeBase').mockReturnValue(
    createMutationResult({
      mutateAsync: adminDeleteBaseMutateAsync
    })
  );
  vi.spyOn(adminKnowledgeHooks, 'useReindexAdminKnowledgeBase').mockReturnValue(createMutationResult());
  vi.spyOn(adminKnowledgeHooks, 'useSearchAdminKnowledge').mockReturnValue(
    createMutationResult({
      mutateAsync: adminSearchMutateAsync
    })
  );

  return {
    publicSearchMutateAsync,
    adminCreateMutateAsync,
    adminUploadMutateAsync,
    adminDeleteBaseMutateAsync
  };
}

describe('KnowledgeWorkspace', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    vi.restoreAllMocks();
  });

  test('public mode can browse and search enabled knowledge bases', async () => {
    const { publicSearchMutateAsync } = mockKnowledgeHooks();
    renderWithProviders(<KnowledgeWorkspace />);

    expect(screen.getByRole('tab', { name: '公开浏览' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getAllByText('Python 核心知识库').length).toBeGreaterThan(0);

    await userEvent.type(screen.getByLabelText('检索问题'), 'Python 解释器');
    await userEvent.click(screen.getByRole('button', { name: '检索' }));

    await waitFor(() => {
      expect(publicSearchMutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({
          query: 'Python 解释器',
          knowledge_base_id: 'kb-public-1',
          top_k: 5
        })
      );
    });
  });

  test('admin mode requires submit before management controls appear', async () => {
    const { adminCreateMutateAsync } = mockKnowledgeHooks();
    renderWithProviders(<KnowledgeWorkspace />);

    await userEvent.click(screen.getByRole('tab', { name: '管理模式' }));
    await userEvent.type(screen.getByLabelText('X-Admin-Token'), 'secret');

    expect(screen.queryByText('创建知识库')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '提交' }));

    await waitFor(() => {
      expect(window.sessionStorage.getItem('super_interview_admin_token')).toBe('secret');
      expect(screen.getAllByText('创建知识库').length).toBeGreaterThan(0);
    });

    await userEvent.type(screen.getByLabelText('名称'), 'Python 核心知识库');
    await userEvent.selectOptions(screen.getByLabelText('Skill'), 'python-backend');
    await userEvent.click(screen.getByRole('button', { name: '创建知识库' }));

    await waitFor(() => {
      expect(adminCreateMutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Python 核心知识库',
          category: 'reference_knowledge',
          skill_id: 'python-backend'
        })
      );
    });
  });

  test('stored admin token opens management mode and uploads selected documents', async () => {
    const { adminUploadMutateAsync } = mockKnowledgeHooks();
    window.sessionStorage.setItem('super_interview_admin_token', 'secret');
    renderWithProviders(<KnowledgeWorkspace />);

    await userEvent.click(screen.getByRole('tab', { name: '管理模式' }));
    const file = new File(['# Python'], 'python.md', { type: 'text/markdown' });
    const secondFile = new File(['# Java'], 'java.md', { type: 'text/markdown' });
    const input = screen.getByLabelText(/上传 Markdown \/ TXT 文档/i) as HTMLInputElement;
    await userEvent.upload(input, [file, secondFile]);

    await waitFor(() => {
      expect(adminUploadMutateAsync).toHaveBeenCalledWith({
        knowledgeBaseId: 'kb-admin-1',
        files: [file, secondFile]
      });
    });
  });

  test('uploading state shows blocking dialog and disables risky actions', async () => {
    mockKnowledgeHooks();
    vi.spyOn(adminKnowledgeHooks, 'useUploadAdminKnowledgeDocuments').mockReturnValue(
      createMutationResult({
        isPending: true,
        isIdle: false,
        status: 'pending'
      })
    );
    window.sessionStorage.setItem('super_interview_admin_token', 'secret');
    renderWithProviders(<KnowledgeWorkspace />);

    await userEvent.click(screen.getByRole('tab', { name: '管理模式' }));

    expect(screen.getByRole('status')).toHaveTextContent('正在上传并索引 1 个文档');
    expect(screen.getByLabelText(/上传 Markdown \/ TXT 文档/i)).toBeDisabled();
    expect(screen.getByRole('button', { name: '停用知识库' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '删除知识库' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '重建索引' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '停用' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '删除' })).toBeDisabled();
  });

  test('public document list paginates long document sets', async () => {
    const documents = Array.from({ length: 12 }, (_, index) => buildPublicDocument(index + 1));
    mockKnowledgeHooks();
    vi.spyOn(publicKnowledgeHooks, 'usePublicKnowledgeBaseDetail').mockReturnValue(
      createQuerySuccessResult<KnowledgeBaseDetailResponse>({
        knowledge_base: {
          ...publicKnowledgeBase,
          document_count: documents.length
        },
        documents
      })
    );
    renderWithProviders(<KnowledgeWorkspace />);

    expect(screen.getByText('public-doc-1.md')).toBeInTheDocument();
    expect(screen.getByText('public-doc-10.md')).toBeInTheDocument();
    expect(screen.queryByText('public-doc-11.md')).not.toBeInTheDocument();
    expect(screen.getByText('第 1 / 2 页')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '下一页' }));

    expect(screen.getByText('public-doc-11.md')).toBeInTheDocument();
    expect(screen.queryByText('public-doc-1.md')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '上一页' }));

    expect(screen.getByText('public-doc-1.md')).toBeInTheDocument();
  });

  test('admin document list paginates long document sets', async () => {
    const documents = Array.from({ length: 12 }, (_, index) => buildAdminDocument(index + 1));
    mockKnowledgeHooks();
    vi.spyOn(adminKnowledgeHooks, 'useAdminKnowledgeBaseDetail').mockReturnValue(
      createQuerySuccessResult({
        knowledge_base: {
          ...adminKnowledgeBase,
          document_count: documents.length
        },
        documents
      })
    );
    window.sessionStorage.setItem('super_interview_admin_token', 'secret');
    renderWithProviders(<KnowledgeWorkspace />);

    await userEvent.click(screen.getByRole('tab', { name: '管理模式' }));

    expect(screen.getByText('admin-doc-1.md')).toBeInTheDocument();
    expect(screen.getByText('admin-doc-10.md')).toBeInTheDocument();
    expect(screen.queryByText('admin-doc-11.md')).not.toBeInTheDocument();
    expect(screen.getByText('第 1 / 2 页')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '下一页' }));

    expect(screen.getByText('admin-doc-11.md')).toBeInTheDocument();
    expect(screen.queryByText('admin-doc-1.md')).not.toBeInTheDocument();
  });

  test('admin mode can delete knowledge bases after confirmation', async () => {
    const { adminDeleteBaseMutateAsync } = mockKnowledgeHooks();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderWithProviders(<KnowledgeWorkspace />);

    await userEvent.click(screen.getByRole('tab', { name: '管理模式' }));
    await userEvent.type(screen.getByLabelText('X-Admin-Token'), 'secret');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));

    await userEvent.click(screen.getByRole('button', { name: '删除知识库' }));

    await waitFor(() => {
      expect(adminDeleteBaseMutateAsync).toHaveBeenCalledWith('kb-admin-1');
    });
  });
});
