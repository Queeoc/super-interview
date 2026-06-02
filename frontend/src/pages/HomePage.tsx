import { useNavigate } from 'react-router-dom';

import { FeedbackState, SectionCard } from '../components/ui';
import { useResumeList } from '../features/resume/useResume';
import { useSkillsList } from '../features/skills/useSkills';
import { getRecentInterviewSessionId } from '../utils/storage';
import styles from './Pages.module.css';

const quickLinks = [
  {
    to: '/interview',
    label: '开始文字面试',
    description: '选择 Skill、可选简历并创建新的会话。'
  },
  {
    to: '/resume',
    label: '进入简历中心',
    description: '上传 PDF/Markdown 简历并查看标准化详情。'
  },
  {
    to: '/skills',
    label: '查看 Skill 列表',
    description: '浏览预设面试官风格、分类和 reference section。'
  },
  {
    to: '/knowledge',
    label: '打开知识库工作台',
    description: 'Phase 9.3 再接入上传与检索验证。'
  }
];

export function HomePage() {
  const navigate = useNavigate();
  const skillsQuery = useSkillsList();
  const resumesQuery = useResumeList();
  const recentSessionId = getRecentInterviewSessionId();

  return (
    <div className={styles.homeLayout}>
      <SectionCard
        title="super-interview 工作台"
        description="Phase 9.2 已接入 Skill、简历和文字面试主流程，后续页面都从这套工作台进入。"
        actions={
          recentSessionId ? (
            <button
              className={styles.secondaryButton}
              type="button"
              onClick={() => navigate(`/interview/${recentSessionId}`)}
            >
              继续最近会话
            </button>
          ) : undefined
        }
      >
        <div className={styles.cardGrid}>
          {quickLinks.map((link) => (
            <button key={link.to} className={styles.featureCard} type="button" onClick={() => navigate(link.to)}>
              <span className={styles.featureCardLabel}>{link.label}</span>
              <span className={styles.featureCardDescription}>{link.description}</span>
              <span className={styles.featureCardHint}>{link.to}</span>
            </button>
          ))}
        </div>
      </SectionCard>

      <div className={styles.statusGrid}>
        <SectionCard title="当前系统能力" description="这些数据直接来自当前可用的后端契约，不依赖本地假状态。">
          <div className={styles.metricGrid}>
            <div className={styles.metricCard}>
              <span>Skill 数量</span>
              <strong>{skillsQuery.data?.length ?? 0}</strong>
            </div>
            <div className={styles.metricCard}>
              <span>已上传简历</span>
              <strong>{resumesQuery.data?.length ?? 0}</strong>
            </div>
            <div className={styles.metricCard}>
              <span>最近会话入口</span>
              <strong>{recentSessionId ? '可继续' : '暂无'}</strong>
            </div>
          </div>
        </SectionCard>

        <SectionCard title="接入说明" description="当前首页不会伪造会话历史；最近会话只保存一个本地快捷入口，并在点击后回源校验。">
          {skillsQuery.isError || resumesQuery.isError ? (
            <FeedbackState
              tone="error"
              title="部分工作台数据加载失败"
              message={
                skillsQuery.error instanceof Error
                  ? skillsQuery.error.message
                  : resumesQuery.error instanceof Error
                    ? resumesQuery.error.message
                    : '请稍后重试'
              }
            />
          ) : (
            <div className={styles.noteList}>
              <div className={styles.noteCard}>
                <strong>服务端事实源</strong>
                <span>Skill、简历、会话快照和报告都以服务端返回结果为准。</span>
              </div>
              <div className={styles.noteCard}>
                <strong>草稿保存</strong>
                <span>会话页支持显式保存和轻量自动暂存，刷新后可回填最近草稿。</span>
              </div>
              <div className={styles.noteCard}>
                <strong>SSE 推进</strong>
                <span>答案正式提交后，会话页会实时展示 `status/plan/content/report/done` 过程。</span>
              </div>
            </div>
          )}
        </SectionCard>
      </div>
    </div>
  );
}
