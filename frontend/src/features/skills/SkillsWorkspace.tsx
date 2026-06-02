import { useEffect, useMemo, useState } from 'react';

import { FeedbackState, MarkdownRenderer, SectionCard } from '../../components/ui';
import type { SkillSummaryDto } from '../../types';
import styles from './SkillsWorkspace.module.css';
import { useSkillDetail, useSkillReferenceSection, useSkillsList } from './useSkills';

export function SkillsWorkspace() {
  const skillsQuery = useSkillsList();
  const [selectedSkillId, setSelectedSkillId] = useState<string | null>(null);

  const skills = skillsQuery.data ?? [];

  useEffect(() => {
    if (!selectedSkillId && skills.length > 0) {
      setSelectedSkillId(skills[0].skill_id);
    }
  }, [selectedSkillId, skills]);

  const selectedSummary = useMemo(
    () => skills.find((item) => item.skill_id === selectedSkillId) ?? null,
    [selectedSkillId, skills]
  );

  const detailQuery = useSkillDetail(selectedSkillId);
  const referenceQuery = useSkillReferenceSection(selectedSkillId);

  return (
    <div className={styles.layout}>
      <SectionCard title="Skill 列表" description="选择一个预设 Skill，查看其面试风格、分类和参考资料。">
        {skillsQuery.isLoading ? (
          <FeedbackState title="正在加载 Skill 列表" message="稍等片刻，我们正在同步后端的预设能力。" />
        ) : skillsQuery.isError ? (
          <FeedbackState
            tone="error"
            title="Skill 列表加载失败"
            message={skillsQuery.error instanceof Error ? skillsQuery.error.message : '请稍后重试'}
          />
        ) : skills.length === 0 ? (
          <FeedbackState title="暂无 Skill" message="当前后端尚未返回任何 Skill 资源。" />
        ) : (
          <div className={styles.list}>
            {skills.map((skill) => (
              <SkillListButton
                key={skill.skill_id}
                item={skill}
                active={skill.skill_id === selectedSkillId}
                onClick={() => setSelectedSkillId(skill.skill_id)}
              />
            ))}
          </div>
        )}
      </SectionCard>

      <SectionCard
        title={selectedSummary ? selectedSummary.display_name : 'Skill 详情'}
        description={selectedSummary ? selectedSummary.description : '选择左侧 Skill 查看完整定义。'}
      >
        {!selectedSkillId ? (
          <FeedbackState title="请选择 Skill" message="左侧列表加载完成后会默认选中第一项。 " />
        ) : detailQuery.isLoading ? (
          <FeedbackState title="正在加载 Skill 详情" message="详情与 reference section 会一起准备。" />
        ) : detailQuery.isError ? (
          <FeedbackState
            tone="error"
            title="Skill 详情加载失败"
            message={detailQuery.error instanceof Error ? detailQuery.error.message : '请稍后重试'}
          />
        ) : detailQuery.data ? (
          <div className={styles.detail}>
            <div className={styles.metaSection}>
              <div>
                <span className={styles.metaLabel}>Skill ID</span>
                <strong>{detailQuery.data.skill_id}</strong>
              </div>
              <div>
                <span className={styles.metaLabel}>启用工具</span>
                <strong>{detailQuery.data.enabled_tools.length || 0}</strong>
              </div>
              <div>
                <span className={styles.metaLabel}>分类数量</span>
                <strong>{detailQuery.data.categories.length}</strong>
              </div>
            </div>

            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>分类</h3>
              <div className={styles.tagGrid}>
                {detailQuery.data.categories.map((category) => (
                  <div key={category.key} className={styles.tagCard}>
                    <strong>{category.label}</strong>
                    <span>{category.key}</span>
                    <span>优先级：{category.priority}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>内容说明</h3>
              <MarkdownRenderer content={detailQuery.data.content_markdown} />
            </div>

            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>Reference Section</h3>
              {referenceQuery.isLoading ? (
                <FeedbackState title="正在加载参考资料" message="后端正在拼装 reference section。" />
              ) : referenceQuery.isError ? (
                <FeedbackState
                  tone="error"
                  title="参考资料加载失败"
                  message={referenceQuery.error instanceof Error ? referenceQuery.error.message : '请稍后重试'}
                />
              ) : (
                <MarkdownRenderer
                  content={referenceQuery.data?.reference_markdown ?? ''}
                  emptyMessage="该 Skill 当前没有可展示的 reference section。"
                />
              )}
            </div>

            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>原始 references</h3>
              <div className={styles.referenceList}>
                {detailQuery.data.references.map((reference) => (
                  <div key={reference.resolved_path} className={styles.referenceCard}>
                    <strong>{reference.title}</strong>
                    <span>{reference.file_name}</span>
                    <span>{reference.resolved_path}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>出题偏好</h3>
              <pre className={styles.preformatted}>
                {JSON.stringify(detailQuery.data.question_preferences, null, 2)}
              </pre>
            </div>
          </div>
        ) : null}
      </SectionCard>
    </div>
  );
}

function SkillListButton({
  item,
  active,
  onClick
}: {
  item: SkillSummaryDto;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={active ? `${styles.listButton} ${styles.listButtonActive}` : styles.listButton}
      onClick={onClick}
    >
      <span className={styles.listButtonTitle}>{item.display_name}</span>
      <span className={styles.listButtonDescription}>{item.description}</span>
      <span className={styles.listButtonMeta}>分类：{item.categories.map((item) => item.label).join(' / ')}</span>
    </button>
  );
}
