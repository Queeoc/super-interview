import type { ChangeEvent, FormEvent } from 'react';
import { useEffect, useMemo, useState } from 'react';

import { FeedbackState, SectionCard } from '../../components/ui';
import type {
  AdminKnowledgeBaseDto,
  AdminKnowledgeDocumentDto,
} from '../../types';
import { formatDateTime, formatFileSize } from '../../utils/format';
import { useSkillsList } from '../skills/useSkills';
import styles from './KnowledgeWorkspace.module.css';
import {
  useAdminKnowledgeBaseDetail,
  useAdminKnowledgeBases,
  useCreateAdminKnowledgeBase,
  useDeleteAdminKnowledgeBase,
  useDeleteAdminKnowledgeDocument,
  useReindexAdminKnowledgeBase,
  useSearchAdminKnowledge,
  useUpdateAdminKnowledgeBase,
  useUpdateAdminKnowledgeDocument,
  useUploadAdminKnowledgeDocuments
} from './useAdminKnowledge';
import {
  usePublicKnowledgeBaseDetail,
  usePublicKnowledgeBases,
  usePublicKnowledgeSearch
} from './useKnowledge';

const ADMIN_TOKEN_STORAGE_KEY = 'super_interview_admin_token';
const FIXED_KNOWLEDGE_CATEGORY = 'reference_knowledge';
const DOCUMENT_PAGE_SIZE = 10;

type KnowledgeMode = 'public' | 'admin';

function readStoredAdminToken() {
  if (typeof window === 'undefined') {
    return '';
  }
  return window.sessionStorage.getItem(ADMIN_TOKEN_STORAGE_KEY) ?? '';
}

function getMutationMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function normalizeText(value: string) {
  return value.trim();
}

function asOptionalText(value: string) {
  const text = value.trim();
  return text.length > 0 ? text : null;
}

function getDocumentPageCount(totalItems: number) {
  return Math.max(1, Math.ceil(totalItems / DOCUMENT_PAGE_SIZE));
}

function getVisiblePageItems<T>(items: T[], page: number) {
  const startIndex = (page - 1) * DOCUMENT_PAGE_SIZE;
  return items.slice(startIndex, startIndex + DOCUMENT_PAGE_SIZE);
}

type DocumentPaginationProps = {
  currentPage: number;
  totalItems: number;
  onPageChange: (page: number) => void;
};

function DocumentPagination({ currentPage, totalItems, onPageChange }: DocumentPaginationProps) {
  if (totalItems <= DOCUMENT_PAGE_SIZE) {
    return null;
  }

  const pageCount = getDocumentPageCount(totalItems);
  return (
    <div className={styles.paginationBar}>
      <button
        className={styles.paginationButton}
        type="button"
        disabled={currentPage <= 1}
        onClick={() => onPageChange(currentPage - 1)}
      >
        上一页
      </button>
      <span>
        第 {currentPage} / {pageCount} 页
      </span>
      <button
        className={styles.paginationButton}
        type="button"
        disabled={currentPage >= pageCount}
        onClick={() => onPageChange(currentPage + 1)}
      >
        下一页
      </button>
      <span>共 {totalItems} 个文档</span>
    </div>
  );
}

export function KnowledgeWorkspace() {
  const [mode, setMode] = useState<KnowledgeMode>('public');
  const storedAdminToken = readStoredAdminToken();
  const [adminTokenInput, setAdminTokenInput] = useState(storedAdminToken);
  const [confirmedAdminToken, setConfirmedAdminToken] = useState(storedAdminToken);

  const publicBasesQuery = usePublicKnowledgeBases();
  const publicBases = publicBasesQuery.data?.items ?? [];
  const [publicSelectedBaseId, setPublicSelectedBaseId] = useState<string | null>(null);
  const effectivePublicBaseId = publicSelectedBaseId ?? publicBases[0]?.id ?? null;
  const publicSelectedBase = useMemo(
    () => publicBases.find((item) => item.id === effectivePublicBaseId) ?? null,
    [publicBases, effectivePublicBaseId]
  );
  const publicDetailQuery = usePublicKnowledgeBaseDetail(effectivePublicBaseId);
  const publicSearchMutation = usePublicKnowledgeSearch();
  const [publicSearchForm, setPublicSearchForm] = useState({
    query: '',
    top_k: 5
  });

  const skillsQuery = useSkillsList();
  const skills = skillsQuery.data ?? [];
  const adminBasesQuery = useAdminKnowledgeBases(confirmedAdminToken);
  const adminBases = adminBasesQuery.data?.items ?? [];
  const [adminSelectedBaseId, setAdminSelectedBaseId] = useState<string | null>(null);
  const effectiveAdminBaseId = adminSelectedBaseId ?? adminBases[0]?.id ?? null;
  const adminSelectedBase = useMemo(
    () => adminBases.find((item) => item.id === effectiveAdminBaseId) ?? null,
    [adminBases, effectiveAdminBaseId]
  );
  const adminDetailQuery = useAdminKnowledgeBaseDetail(confirmedAdminToken, effectiveAdminBaseId);
  const createMutation = useCreateAdminKnowledgeBase(confirmedAdminToken);
  const updateBaseMutation = useUpdateAdminKnowledgeBase(confirmedAdminToken);
  const uploadMutation = useUploadAdminKnowledgeDocuments(confirmedAdminToken);
  const updateDocumentMutation = useUpdateAdminKnowledgeDocument(confirmedAdminToken);
  const deleteBaseMutation = useDeleteAdminKnowledgeBase(confirmedAdminToken);
  const deleteDocumentMutation = useDeleteAdminKnowledgeDocument(confirmedAdminToken);
  const reindexMutation = useReindexAdminKnowledgeBase(confirmedAdminToken);
  const adminSearchMutation = useSearchAdminKnowledge(confirmedAdminToken);

  const [createForm, setCreateForm] = useState({
    name: '',
    skill_id: '',
    description: ''
  });
  const [adminSearchForm, setAdminSearchForm] = useState({
    query: '',
    top_k: 5
  });
  const [operationMessage, setOperationMessage] = useState('');
  const [publicDocumentPage, setPublicDocumentPage] = useState(1);
  const [adminDocumentPage, setAdminDocumentPage] = useState(1);
  const [uploadingFileNames, setUploadingFileNames] = useState<string[]>([]);
  const [uploadingFileCount, setUploadingFileCount] = useState(0);
  const adminAccessReady = Boolean(confirmedAdminToken) && adminBasesQuery.isSuccess;
  const adminAccessChecking = Boolean(confirmedAdminToken) && adminBasesQuery.isLoading;
  const adminAccessError = Boolean(confirmedAdminToken) && adminBasesQuery.isError;
  const isUploadingDocuments = uploadMutation.isPending;
  const shouldShowUploadOverlay = isUploadingDocuments || uploadingFileCount > 0;
  const visibleUploadFileNames = uploadingFileNames.slice(0, 3);
  const remainingUploadFileCount = Math.max(0, uploadingFileCount - visibleUploadFileNames.length);
  const displayUploadFileCount = uploadingFileCount || 1;
  const publicDocuments = publicDetailQuery.data?.documents ?? [];
  const adminDocuments = adminDetailQuery.data?.documents ?? [];
  const publicVisibleDocuments = useMemo(
    () => getVisiblePageItems(publicDocuments, publicDocumentPage),
    [publicDocuments, publicDocumentPage]
  );
  const adminVisibleDocuments = useMemo(
    () => getVisiblePageItems(adminDocuments, adminDocumentPage),
    [adminDocuments, adminDocumentPage]
  );

  useEffect(() => {
    if (!confirmedAdminToken) {
      window.sessionStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY);
      return;
    }
    if (adminBasesQuery.isSuccess) {
      window.sessionStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, confirmedAdminToken);
      return;
    }
    if (adminBasesQuery.isError) {
      window.sessionStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY);
    }
  }, [adminBasesQuery.isError, adminBasesQuery.isSuccess, confirmedAdminToken]);

  useEffect(() => {
    if (publicBases.length === 0) {
      setPublicSelectedBaseId(null);
      return;
    }
    if (!publicSelectedBaseId || !publicBases.some((item) => item.id === publicSelectedBaseId)) {
      setPublicSelectedBaseId(publicBases[0].id);
    }
  }, [publicBases, publicSelectedBaseId]);

  useEffect(() => {
    if (adminBases.length === 0) {
      setAdminSelectedBaseId(null);
      return;
    }
    if (!adminSelectedBaseId || !adminBases.some((item) => item.id === adminSelectedBaseId)) {
      setAdminSelectedBaseId(adminBases[0].id);
    }
  }, [adminBases, adminSelectedBaseId]);

  useEffect(() => {
    setPublicDocumentPage(1);
  }, [effectivePublicBaseId]);

  useEffect(() => {
    setAdminDocumentPage(1);
  }, [effectiveAdminBaseId]);

  useEffect(() => {
    setPublicDocumentPage((currentPage) =>
      Math.min(currentPage, getDocumentPageCount(publicDocuments.length))
    );
  }, [publicDocuments.length]);

  useEffect(() => {
    setAdminDocumentPage((currentPage) =>
      Math.min(currentPage, getDocumentPageCount(adminDocuments.length))
    );
  }, [adminDocuments.length]);

  async function handleAdminTokenSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setOperationMessage('');
    setConfirmedAdminToken(normalizeText(adminTokenInput));
  }

  function handleAdminTokenClear() {
    setAdminTokenInput('');
    setConfirmedAdminToken('');
    setOperationMessage('');
  }

  async function handlePublicSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = normalizeText(publicSearchForm.query);
    if (!query) {
      return;
    }

    await publicSearchMutation.mutateAsync({
      query,
      knowledge_base_id: effectivePublicBaseId,
      top_k: publicSearchForm.top_k,
      rewrite_enabled: false
    });
  }

  async function handleCreateKnowledgeBase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setOperationMessage('');
    try {
      const result = await createMutation.mutateAsync({
        name: normalizeText(createForm.name),
        category: FIXED_KNOWLEDGE_CATEGORY,
        skill_id: asOptionalText(createForm.skill_id),
        description: asOptionalText(createForm.description)
      });
      setAdminSelectedBaseId(result.id);
      setCreateForm({
        name: '',
        skill_id: result.skill_id ?? '',
        description: ''
      });
      setOperationMessage('知识库创建成功。');
    } catch (error) {
      setOperationMessage(getMutationMessage(error, '知识库创建失败。'));
    }
  }

  async function handleAdminBaseEnabledChange(knowledgeBase: AdminKnowledgeBaseDto) {
    setOperationMessage('');
    try {
      await updateBaseMutation.mutateAsync({
        knowledgeBaseId: knowledgeBase.id,
        payload: {
          is_enabled: !knowledgeBase.is_enabled
        }
      });
      setOperationMessage(knowledgeBase.is_enabled ? '知识库已停用。' : '知识库已启用。');
    } catch (error) {
      setOperationMessage(getMutationMessage(error, '知识库启停失败。'));
    }
  }

  async function handleAdminFileChange(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    if (files.length === 0 || !effectiveAdminBaseId) {
      return;
    }
    setOperationMessage('');
    setUploadingFileNames(files.map((file) => file.name));
    setUploadingFileCount(files.length);
    try {
      const result = await uploadMutation.mutateAsync({
        knowledgeBaseId: effectiveAdminBaseId,
        files
      });
      if (result.failed_count > 0) {
        const failedNames = result.items
          .filter((item) => !item.success)
          .map((item) => item.file_name)
          .join('、');
        setOperationMessage(
          `批量上传完成：成功 ${result.success_count} 个，失败 ${result.failed_count} 个。${failedNames ? `失败文件：${failedNames}` : ''}`
        );
      } else {
        setOperationMessage(
          result.total_files > 1
            ? `批量上传完成：${result.success_count} 个文档已上传并索引。`
            : '文档上传并索引完成。'
        );
      }
    } catch (error) {
      setOperationMessage(getMutationMessage(error, '文档批量上传失败。'));
    } finally {
      event.target.value = '';
      setUploadingFileNames([]);
      setUploadingFileCount(0);
    }
  }

  async function handleAdminDocumentEnabledChange(document: AdminKnowledgeDocumentDto) {
    setOperationMessage('');
    try {
      await updateDocumentMutation.mutateAsync({
        knowledgeBaseId: document.knowledge_base_id,
        documentId: document.id,
        payload: {
          is_enabled: !document.is_enabled
        }
      });
      setOperationMessage(document.is_enabled ? '文档已停用。' : '文档已启用。');
    } catch (error) {
      setOperationMessage(getMutationMessage(error, '文档启停失败。'));
    }
  }

  async function handleAdminDeleteDocument(document: AdminKnowledgeDocumentDto) {
    if (
      typeof window !== "undefined" &&
      !window.confirm(`Delete document "${document.original_file_name}"? This cannot be undone.`)
    ) {
      return;
    }
    setOperationMessage('');
    try {
      await deleteDocumentMutation.mutateAsync({
        knowledgeBaseId: document.knowledge_base_id,
        documentId: document.id
      });
      setOperationMessage('Document deleted.');
    } catch (error) {
      setOperationMessage(getMutationMessage(error, 'Document delete failed.'));
    }
  }

  async function handleAdminReindex() {
    if (!effectiveAdminBaseId) {
      return;
    }
    setOperationMessage('');
    try {
      const result = await reindexMutation.mutateAsync(effectiveAdminBaseId);
      setOperationMessage(`重建索引完成：成功 ${result.success_count}，失败 ${result.failed_count}。`);
    } catch (error) {
      setOperationMessage(getMutationMessage(error, '重建索引失败。'));
    }
  }

  async function handleAdminSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = normalizeText(adminSearchForm.query);
    if (!query) {
      return;
    }

    await adminSearchMutation.mutateAsync({
      query,
      knowledge_base_id: effectiveAdminBaseId,
      top_k: adminSearchForm.top_k,
      rewrite_enabled: false
    });
  }

  async function handleAdminDeleteBase() {
    if (!effectiveAdminBaseId) {
      return;
    }
    const targetName = adminSelectedBase?.name ?? 'current knowledge base';
    if (
      typeof window !== "undefined" &&
      !window.confirm(`Delete knowledge base "${targetName}"? This cannot be undone.`)
    ) {
      return;
    }

    setOperationMessage('');
    try {
      await deleteBaseMutation.mutateAsync(effectiveAdminBaseId);
      setAdminSelectedBaseId(null);
      setOperationMessage('Knowledge base deleted.');
    } catch (error) {
      setOperationMessage(getMutationMessage(error, 'Knowledge base delete failed.'));
    }
  }

  return (
    <div className={styles.layout}>
      <header className={styles.pageHeader}>
        <div>
          <h1 className={styles.pageTitle}>知识库</h1>
          <p className={styles.pageDescription}>
            普通用户可以浏览启用中的知识库并检索内容，管理员输入 token 后可进入管理模式。
          </p>
        </div>
        <div className={styles.modeTabs} role="tablist" aria-label="知识库模式切换">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'public'}
            className={mode === 'public' ? styles.modeButtonActive : styles.modeButton}
            onClick={() => setMode('public')}
          >
            公开浏览
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'admin'}
            className={mode === 'admin' ? styles.modeButtonActive : styles.modeButton}
            onClick={() => setMode('admin')}
          >
            管理模式
          </button>
        </div>
      </header>

      {mode === 'public' ? (
        <>
          <div className={styles.publicGrid}>
            <SectionCard title="公开知识库列表" description="默认只展示启用中的知识库。">
              {publicBasesQuery.isLoading ? (
                <FeedbackState title="正在加载" message="公开知识库列表正在同步。" />
              ) : publicBasesQuery.isError ? (
                <FeedbackState
                  tone="error"
                  title="加载失败"
                  message={getMutationMessage(publicBasesQuery.error, '公开知识库列表加载失败。')}
                />
              ) : publicBases.length === 0 ? (
                <FeedbackState title="暂无知识库" message="当前没有可公开浏览的启用知识库。" />
              ) : (
                <div className={styles.baseList}>
                  {publicBases.map((knowledgeBase) => (
                    <button
                      key={knowledgeBase.id}
                      type="button"
                      className={
                        knowledgeBase.id === publicSelectedBaseId
                          ? `${styles.baseButton} ${styles.baseButtonActive}`
                          : styles.baseButton
                      }
                      onClick={() => setPublicSelectedBaseId(knowledgeBase.id)}
                    >
                      <strong>{knowledgeBase.name}</strong>
                      <span>{knowledgeBase.category}</span>
                      <span>{knowledgeBase.skill_id ?? '未绑定 skill'}</span>
                      <span>文档 {knowledgeBase.document_count}</span>
                    </button>
                  ))}
                </div>
              )}
            </SectionCard>

            <div className={styles.columnWide}>
              <SectionCard
                title={publicSelectedBase ? publicSelectedBase.name : '知识库详情'}
                description={
                  publicSelectedBase
                    ? `${publicSelectedBase.category} · ${publicSelectedBase.skill_id ?? '未绑定 skill'}`
                    : '从左侧选择一个知识库查看详情。'
                }
              >
                {!publicSelectedBaseId ? (
                  <FeedbackState title="未选择知识库" message="选择一个知识库后，这里会展示文档列表。" />
                ) : publicDetailQuery.isLoading ? (
                  <FeedbackState title="正在加载详情" message="公开知识库详情正在同步。" />
                ) : publicDetailQuery.isError ? (
                  <FeedbackState
                    tone="error"
                    title="详情加载失败"
                    message={getMutationMessage(publicDetailQuery.error, '知识库详情加载失败。')}
                  />
                ) : publicDetailQuery.data ? (
                  <div className={styles.detailStack}>
                    <div className={styles.summaryRow}>
                      <span className={styles.badge}>文档 {publicDetailQuery.data.knowledge_base.document_count}</span>
                      <span className={styles.badge}>分类 {publicDetailQuery.data.knowledge_base.category}</span>
                      <span className={styles.badge}>
                        Skill {publicDetailQuery.data.knowledge_base.skill_id ?? '未绑定'}
                      </span>
                    </div>
                    {publicDetailQuery.data.knowledge_base.description ? (
                      <p className={styles.detailDescription}>{publicDetailQuery.data.knowledge_base.description}</p>
                    ) : null}
                    {publicDocuments.length === 0 ? (
                      <FeedbackState title="暂无文档" message="该知识库暂时没有可公开浏览的启用文档。" />
                    ) : (
                      <>
                        <div className={styles.documentList}>
                          {publicVisibleDocuments.map((document) => (
                            <article key={document.id} className={styles.documentRow}>
                              <div className={styles.documentMain}>
                                <strong>{document.original_file_name}</strong>
                                <span>
                                  {document.index_status} · {formatFileSize(document.file_size)}
                                </span>
                                <span>
                                  {document.category} · {document.skill_id ?? '未绑定 skill'} · {document.mime_type}
                                </span>
                                <span>索引时间：{formatDateTime(document.indexed_at)}</span>
                              </div>
                            </article>
                          ))}
                        </div>
                        <DocumentPagination
                          currentPage={publicDocumentPage}
                          totalItems={publicDocuments.length}
                          onPageChange={setPublicDocumentPage}
                        />
                      </>
                    )}
                  </div>
                ) : null}
              </SectionCard>

              <SectionCard title="知识检索" description="面向普通用户的公开检索入口，默认只查当前选中的知识库。">
                <form className={styles.searchForm} onSubmit={handlePublicSearch}>
                  <label className={styles.field}>
                    <span>检索问题</span>
                    <input
                      value={publicSearchForm.query}
                      onChange={(event) =>
                        setPublicSearchForm((current) => ({ ...current, query: event.target.value }))
                      }
                      placeholder="Python 解释器相关面试题"
                    />
                  </label>
                  <label className={styles.field}>
                    <span>Top K</span>
                    <input
                      type="number"
                      min={1}
                      max={20}
                      value={publicSearchForm.top_k}
                      onChange={(event) =>
                        setPublicSearchForm((current) => ({
                          ...current,
                          top_k: Number(event.target.value) || 5
                        }))
                      }
                    />
                  </label>
                  <button className={styles.primaryButton} type="submit" disabled={!publicSearchForm.query.trim()}>
                    检索
                  </button>
                </form>

                {publicSearchMutation.isError ? (
                  <FeedbackState
                    tone="error"
                    title="检索失败"
                    message={getMutationMessage(publicSearchMutation.error, '公开知识检索失败。')}
                  />
                ) : publicSearchMutation.data ? (
                  <div className={styles.searchResults}>
                    {publicSearchMutation.data.hits.length === 0 ? (
                      <FeedbackState title="无命中" message={publicSearchMutation.data.message} />
                    ) : (
                      publicSearchMutation.data.hits.map((hit) => (
                        <article key={`${hit.document_id}-${hit.score}`} className={styles.hitCard}>
                          <div className={styles.hitMeta}>
                            <strong>{hit.file_name}</strong>
                            <span>{hit.category} · {hit.score.toFixed(3)}</span>
                          </div>
                          <p>{hit.content}</p>
                        </article>
                      ))
                    )}
                  </div>
                ) : null}
              </SectionCard>
            </div>
          </div>
        </>
      ) : (
        <>
          <SectionCard title="管理员入口" description="输入管理员 token 后，可以管理全局知识库并重建索引。">
            <form className={styles.tokenRow} onSubmit={handleAdminTokenSubmit}>
              <label className={styles.field}>
                <span>X-Admin-Token</span>
                <input
                  type="password"
                  value={adminTokenInput}
                  placeholder="输入管理员 token"
                  onChange={(event) => setAdminTokenInput(event.target.value)}
                />
              </label>
              <button className={styles.primaryButton} type="submit">
                提交
              </button>
              <button className={styles.secondaryButton} type="button" onClick={handleAdminTokenClear}>
                清除
              </button>
            </form>
            {!confirmedAdminToken ? (
              <FeedbackState title="需要管理员 token" message="填写 token 后才会加载管理列表与操作入口。" />
            ) : adminAccessChecking ? (
              <FeedbackState title="正在验证" message="正在检查管理员 token，请稍候。" />
            ) : adminAccessError ? (
              <FeedbackState tone="error" title="token 无效" message="管理员 token 校验失败，请重新输入。" />
            ) : null}
          </SectionCard>

          {adminAccessReady ? (
            <div className={styles.grid}>
              <div className={styles.column}>
                <SectionCard title="创建知识库" description="先建逻辑知识库，再向其中追加多个技术文档。">
                  <form className={styles.form} onSubmit={handleCreateKnowledgeBase}>
                    <label className={styles.field}>
                      <span>名称</span>
                      <input
                        required
                        value={createForm.name}
                        onChange={(event) =>
                          setCreateForm((current) => ({ ...current, name: event.target.value }))
                        }
                        placeholder="Python 核心知识库"
                      />
                    </label>
                    <label className={styles.field}>
                      <span>分类</span>
                      <div className={styles.badge}>{FIXED_KNOWLEDGE_CATEGORY}</div>
                    </label>
                    <label className={styles.field}>
                      <span>Skill</span>
                      <select
                        value={createForm.skill_id}
                        disabled={skillsQuery.isLoading}
                        onChange={(event) =>
                          setCreateForm((current) => ({ ...current, skill_id: event.target.value }))
                        }
                      >
                        <option value="">
                          {skillsQuery.isLoading
                            ? '正在加载 Skill 列表'
                            : skills.length === 0
                              ? '暂无可选 Skill'
                              : '通用知识库（不绑定 Skill）'}
                        </option>
                        {skills.map((skill) => (
                          <option key={skill.skill_id} value={skill.skill_id}>
                            {skill.display_name} ({skill.skill_id})
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className={styles.field}>
                      <span>描述</span>
                      <textarea
                        value={createForm.description}
                        onChange={(event) =>
                          setCreateForm((current) => ({ ...current, description: event.target.value }))
                        }
                        placeholder="用于面试过程中的技术知识补充"
                        rows={3}
                      />
                    </label>
                    <button className={styles.primaryButton} type="submit" disabled={createMutation.isPending}>
                      {createMutation.isPending ? '创建中' : '创建知识库'}
                    </button>
                  </form>
                </SectionCard>

                <SectionCard title="知识库列表" description="选中一个知识库后，可以查看文档、上传和重建索引。">
                  {adminBasesQuery.isLoading ? (
                    <FeedbackState title="正在加载" message="正在读取管理员知识库列表。" />
                  ) : adminBasesQuery.isError ? (
                    <FeedbackState
                      tone="error"
                      title="加载失败"
                      message={getMutationMessage(adminBasesQuery.error, '管理员知识库列表加载失败。')}
                    />
                  ) : adminBases.length === 0 ? (
                    <FeedbackState title="暂无知识库" message="先创建一个逻辑知识库，再上传文档。" />
                  ) : (
                    <div className={styles.baseList}>
                      {adminBases.map((knowledgeBase) => (
                        <button
                          key={knowledgeBase.id}
                          type="button"
                          className={
                            knowledgeBase.id === adminSelectedBaseId
                              ? `${styles.baseButton} ${styles.baseButtonActive}`
                              : styles.baseButton
                          }
                          onClick={() => setAdminSelectedBaseId(knowledgeBase.id)}
                        >
                          <strong>{knowledgeBase.name}</strong>
                          <span>{knowledgeBase.category}</span>
                          <span>{knowledgeBase.skill_id ?? '未绑定 skill'}</span>
                          <span>
                            {knowledgeBase.is_enabled ? '启用' : '停用'} · {knowledgeBase.document_count} 文档
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                </SectionCard>
              </div>

              <div className={styles.columnWide}>
                <SectionCard
                  title={adminSelectedBase ? adminSelectedBase.name : '知识库详情'}
                  description={
                    adminSelectedBase
                      ? `${adminSelectedBase.category} · ${adminSelectedBase.skill_id ?? '未绑定 skill'}`
                      : '从左侧选择一个知识库。'
                  }
                  actions={
                    adminSelectedBase ? (
                      <div className={styles.actionRow}>
                        <button
                          className={styles.secondaryButton}
                          type="button"
                          onClick={() => handleAdminBaseEnabledChange(adminSelectedBase)}
                          disabled={isUploadingDocuments}
                        >
                          {adminSelectedBase.is_enabled ? '停用知识库' : '启用知识库'}
                        </button>
                        <button
                          className={styles.dangerButton}
                          type="button"
                          onClick={handleAdminDeleteBase}
                          disabled={deleteBaseMutation.isPending || isUploadingDocuments}
                        >
                          {deleteBaseMutation.isPending ? '删除中…' : '删除知识库'}
                        </button>
                        <button
                          className={styles.secondaryButton}
                          type="button"
                          onClick={handleAdminReindex}
                          disabled={reindexMutation.isPending || isUploadingDocuments}
                        >
                          {reindexMutation.isPending ? '重建中' : '重建索引'}
                        </button>
                      </div>
                    ) : undefined
                  }
                >
                  {operationMessage ? (
                    <FeedbackState
                      tone={
                        createMutation.isError ||
                        uploadMutation.isError ||
                        updateBaseMutation.isError ||
                        updateDocumentMutation.isError ||
                        deleteBaseMutation.isError ||
                        deleteDocumentMutation.isError ||
                        reindexMutation.isError
                          ? 'error'
                          : 'success'
                      }
                      title="操作结果"
                      message={operationMessage}
                    />
                  ) : null}

                  {!adminSelectedBaseId ? (
                    <FeedbackState title="未选择知识库" message="创建或选择一个知识库后，这里会显示文档和上传入口。" />
                  ) : adminDetailQuery.isLoading ? (
                    <FeedbackState title="正在加载详情" message="正在读取知识库文档列表。" />
                  ) : adminDetailQuery.isError ? (
                    <FeedbackState
                      tone="error"
                      title="详情加载失败"
                      message={getMutationMessage(adminDetailQuery.error, '知识库详情加载失败。')}
                    />
                  ) : adminDetailQuery.data ? (
                    <div className={styles.detailStack}>
                      <div className={styles.summaryRow}>
                        <span className={styles.badge}>文档 {adminDetailQuery.data.knowledge_base.document_count}</span>
                        <span className={styles.badge}>状态 {adminDetailQuery.data.knowledge_base.status}</span>
                        <span className={styles.badge}>
                          {adminDetailQuery.data.knowledge_base.is_enabled ? '启用' : '停用'}
                        </span>
                      </div>

                      <label
                        className={
                          isUploadingDocuments
                            ? `${styles.uploadBox} ${styles.uploadBoxDisabled}`
                            : styles.uploadBox
                        }
                      >
                        <span className={styles.uploadTitle}>上传 Markdown / TXT 文档</span>
                        <span className={styles.uploadHint}>可一次选择多个文件，上传后会自动追加到当前知识库并进入索引流程。</span>
                        <span className={styles.uploadAction}>{isUploadingDocuments ? '上传中' : '点击选择文件'}</span>
                        <input
                          className={styles.hiddenInput}
                          type="file"
                          multiple
                          accept=".md,.txt"
                          onChange={handleAdminFileChange}
                          disabled={!adminSelectedBaseId || isUploadingDocuments}
                        />
                      </label>

                      {adminDocuments.length === 0 ? (
                        <FeedbackState title="暂无文档" message="上传第一份文档后，索引状态会显示在这里。" />
                      ) : (
                        <>
                          <div className={styles.documentList}>
                            {adminVisibleDocuments.map((document) => (
                              <article key={document.id} className={styles.documentRow}>
                                <div className={styles.documentMain}>
                                  <strong>{document.original_file_name}</strong>
                                  <span>
                                    {document.index_status} · {document.is_enabled ? '启用' : '停用'} ·{' '}
                                    {formatFileSize(document.file_size)}
                                  </span>
                                  <span>
                                    {document.category} · {document.skill_id ?? '未绑定 skill'} · {document.mime_type}
                                  </span>
                                  <span>索引时间：{formatDateTime(document.indexed_at)}</span>
                                  {document.error_message ? (
                                    <span className={styles.errorText}>{document.error_message}</span>
                                  ) : null}
                                </div>
                                <div className={styles.actionRow}>
                                  <button
                                    className={styles.secondaryButton}
                                    type="button"
                                    onClick={() => handleAdminDocumentEnabledChange(document)}
                                    disabled={isUploadingDocuments}
                                  >
                                    {document.is_enabled ? '停用' : '启用'}
                                  </button>
                                  <button
                                    className={styles.dangerButton}
                                    type="button"
                                    onClick={() => handleAdminDeleteDocument(document)}
                                    disabled={isUploadingDocuments}
                                  >
                                    删除
                                  </button>
                                </div>
                              </article>
                            ))}
                          </div>
                          <DocumentPagination
                            currentPage={adminDocumentPage}
                            totalItems={adminDocuments.length}
                            onPageChange={setAdminDocumentPage}
                          />
                        </>
                      )}
                    </div>
                  ) : null}
                </SectionCard>

                <SectionCard title="检索调试" description="用当前知识库验证 RAG 命中结果，仅供管理员调试。">
                  <form className={styles.searchForm} onSubmit={handleAdminSearch}>
                    <label className={styles.field}>
                      <span>检索问题</span>
                      <input
                        value={adminSearchForm.query}
                        onChange={(event) =>
                          setAdminSearchForm((current) => ({ ...current, query: event.target.value }))
                        }
                        placeholder="Python 解释器相关面试题"
                      />
                    </label>
                  <label className={styles.field}>
                    <span>Top K</span>
                    <input
                        type="number"
                        min={1}
                        max={20}
                        value={adminSearchForm.top_k}
                        onChange={(event) =>
                          setAdminSearchForm((current) => ({
                            ...current,
                            top_k: Number(event.target.value) || 5
                          }))
                        }
                      />
                    </label>
                    <button className={styles.primaryButton} type="submit" disabled={!adminSearchForm.query.trim()}>
                      检索
                    </button>
                  </form>

                  {adminSearchMutation.isError ? (
                    <FeedbackState
                      tone="error"
                      title="检索失败"
                      message={getMutationMessage(adminSearchMutation.error, '知识库检索失败。')}
                    />
                  ) : adminSearchMutation.data ? (
                    <div className={styles.searchResults}>
                      {adminSearchMutation.data.hits.length === 0 ? (
                        <FeedbackState title="无命中" message={adminSearchMutation.data.message} />
                      ) : (
                        adminSearchMutation.data.hits.map((hit) => (
                          <article key={`${hit.document_id}-${hit.score}`} className={styles.hitCard}>
                            <div className={styles.hitMeta}>
                              <strong>{hit.file_name}</strong>
                              <span>{hit.category} · {hit.score.toFixed(3)}</span>
                            </div>
                            <p>{hit.content}</p>
                          </article>
                        ))
                      )}
                    </div>
                  ) : null}
                </SectionCard>
              </div>
            </div>
          ) : null}
        </>
      )}
      {shouldShowUploadOverlay ? (
        <div className={styles.uploadOverlay} role="status" aria-live="polite">
          <div className={styles.uploadDialog}>
            <div className={styles.uploadSpinner} aria-hidden="true" />
            <div className={styles.uploadDialogMain}>
              <strong>正在上传并索引 {displayUploadFileCount} 个文档</strong>
              <span>请不要关闭页面或重复点击上传，完成后会自动刷新文档列表。</span>
              {visibleUploadFileNames.length > 0 ? (
                <div className={styles.uploadFileList}>
                  {visibleUploadFileNames.map((fileName) => (
                    <span key={fileName}>{fileName}</span>
                  ))}
                  {remainingUploadFileCount > 0 ? <span>等 {remainingUploadFileCount} 个文件</span> : null}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
