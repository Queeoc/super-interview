import { useEffect, useMemo, useState } from 'react';

import { FeedbackState, MarkdownRenderer, SectionCard } from '../../components/ui';
import { formatDateTime, formatFileSize } from '../../utils/format';
import styles from './ResumeWorkspace.module.css';
import { useResumeDetail, useResumeList, useUploadResume } from './useResume';

export function ResumeWorkspace() {
  const resumeListQuery = useResumeList();
  const uploadMutation = useUploadResume();
  const [selectedResumeId, setSelectedResumeId] = useState<string | null>(null);
  const [uploadMessage, setUploadMessage] = useState<string>('');

  const resumes = resumeListQuery.data ?? [];

  useEffect(() => {
    if (!selectedResumeId && resumes.length > 0) {
      setSelectedResumeId(resumes[0].resume_id);
    }
  }, [selectedResumeId, resumes]);

  const detailQuery = useResumeDetail(selectedResumeId);

  const selectedResume = useMemo(
    () => resumes.find((item) => item.resume_id === selectedResumeId) ?? null,
    [resumes, selectedResumeId]
  );

  async function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    setUploadMessage('');

    try {
      const response = await uploadMutation.mutateAsync(file);
      setSelectedResumeId(response.resume.resume_id);
      setUploadMessage(response.reused_existing ? '已复用已有简历。' : '简历上传成功，已同步刷新列表。');
    } catch (error) {
      setUploadMessage(error instanceof Error ? error.message : '上传失败，请稍后重试。');
    } finally {
      event.target.value = '';
    }
  }

  return (
    <div className={styles.layout}>
      <div className={styles.leftColumn}>
        <SectionCard title="上传简历" description="支持 PDF 与 Markdown，上传后会同步解析并进入当前访客的简历列表。">
          <label className={styles.uploadBox}>
            <span className={styles.uploadTitle}>选择文件上传</span>
            <span className={styles.uploadHint}>支持 `.pdf`、`.md`，后端将同步完成标准化处理。</span>
            <input
              className={styles.hiddenInput}
              type="file"
              accept=".pdf,.md"
              onChange={handleFileChange}
            />
          </label>

          {uploadMutation.isPending ? (
            <FeedbackState title="上传中" message="简历正在上传并解析，请稍候。" />
          ) : null}

          {uploadMessage ? (
            <FeedbackState
              tone={uploadMutation.isError ? 'error' : 'success'}
              title={uploadMutation.isError ? '上传失败' : '上传结果'}
              message={uploadMessage}
            />
          ) : null}
        </SectionCard>

        <SectionCard title="简历列表" description="所有数据都来自当前 `visitor_id` Cookie 对应的后端访客空间。">
          {resumeListQuery.isLoading ? (
            <FeedbackState title="正在加载简历列表" message="稍等片刻，我们正在同步简历记录。" />
          ) : resumeListQuery.isError ? (
            <FeedbackState
              tone="error"
              title="简历列表加载失败"
              message={resumeListQuery.error instanceof Error ? resumeListQuery.error.message : '请稍后重试'}
            />
          ) : resumes.length === 0 ? (
            <FeedbackState title="暂无简历" message="先上传一份简历，之后这里会展示历史记录。" />
          ) : (
            <div className={styles.resumeList}>
              {resumes.map((resume) => (
                <button
                  key={resume.resume_id}
                  type="button"
                  className={
                    resume.resume_id === selectedResumeId
                      ? `${styles.resumeButton} ${styles.resumeButtonActive}`
                      : styles.resumeButton
                  }
                  onClick={() => setSelectedResumeId(resume.resume_id)}
                >
                  <strong>{resume.original_file_name}</strong>
                  <span>
                    {resume.file_extension.toUpperCase()} · {formatFileSize(resume.file_size)}
                  </span>
                  <span>状态：{resume.status}</span>
                  <span>更新时间：{formatDateTime(resume.updated_at)}</span>
                </button>
              ))}
            </div>
          )}
        </SectionCard>
      </div>

      <SectionCard
        title={selectedResume ? `简历详情 · ${selectedResume.original_file_name}` : '简历详情'}
        description={selectedResume ? '右侧展示标准化 Markdown 与来源元数据。' : '从左侧列表中选择一份简历。'}
      >
        {!selectedResumeId ? (
          <FeedbackState title="请选择简历" message="上传或选择左侧列表中的简历后，这里会显示标准化内容。" />
        ) : detailQuery.isLoading ? (
          <FeedbackState title="正在加载简历详情" message="后端正在返回这份简历的标准化结果。" />
        ) : detailQuery.isError ? (
          <FeedbackState
            tone="error"
            title="简历详情加载失败"
            message={detailQuery.error instanceof Error ? detailQuery.error.message : '请稍后重试'}
          />
        ) : detailQuery.data ? (
          <div className={styles.detail}>
            <div className={styles.metaGrid}>
              <div className={styles.metaCard}>
                <span>文件类型</span>
                <strong>{detailQuery.data.file_extension.toUpperCase()}</strong>
              </div>
              <div className={styles.metaCard}>
                <span>大小</span>
                <strong>{formatFileSize(detailQuery.data.file_size)}</strong>
              </div>
              <div className={styles.metaCard}>
                <span>状态</span>
                <strong>{detailQuery.data.status}</strong>
              </div>
              <div className={styles.metaCard}>
                <span>上传时间</span>
                <strong>{formatDateTime(detailQuery.data.uploaded_at)}</strong>
              </div>
            </div>

            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>Markdown 正文</h3>
              <MarkdownRenderer
                content={detailQuery.data.markdown_content}
                emptyMessage="当前简历尚未生成可展示的 Markdown 内容。"
              />
            </div>

            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>来源元数据</h3>
              <pre className={styles.preformatted}>
                {JSON.stringify(detailQuery.data.source_metadata, null, 2)}
              </pre>
            </div>
          </div>
        ) : null}
      </SectionCard>
    </div>
  );
}
