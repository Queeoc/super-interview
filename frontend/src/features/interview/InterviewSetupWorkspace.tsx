import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { FeedbackState, ProcessTimeline, type ProcessTimelineStep, SectionCard } from '../../components/ui';
import { formatDateTime } from '../../utils/format';
import { useResumeList } from '../resume/useResume';
import { useSkillsList } from '../skills/useSkills';
import styles from './InterviewSetupWorkspace.module.css';
import { useCreateInterviewSession, useInterviewSessionsList } from './useInterview';

const CREATE_INTERVIEW_STEPS: ProcessTimelineStep[] = [
  {
    id: 'config',
    label: '读取面试配置',
    description: '确认 Skill、语言、轮次和简历关联方式。'
  },
  {
    id: 'context',
    label: '加载 Skill / 简历上下文',
    description: '整理出题需要的能力要求、参考资料和简历线索。'
  },
  {
    id: 'planning',
    label: '规划首轮问题',
    description: '根据面试方向生成第一轮问题计划。'
  },
  {
    id: 'first-question',
    label: '生成面试官开场与首题',
    description: '面试官正在把计划转成可回答的问题。'
  },
  {
    id: 'enter-session',
    label: '进入面试会话',
    description: '保存会话并跳转到答题工作台。'
  }
];

export function InterviewSetupWorkspace() {
  const navigate = useNavigate();
  const skillsQuery = useSkillsList();
  const resumesQuery = useResumeList();
  const sessionsQuery = useInterviewSessionsList();
  const createSessionMutation = useCreateInterviewSession();

  const skills = skillsQuery.data ?? [];
  const resumes = resumesQuery.data ?? [];

  const defaultSkillId = useMemo(() => skills[0]?.skill_id ?? '', [skills]);

  const [skillId, setSkillId] = useState('');
  const [resumeId, setResumeId] = useState('');
  const [title, setTitle] = useState('');
  const [language, setLanguage] = useState('zh-CN');
  const [maxRounds, setMaxRounds] = useState(5);
  const [validationMessage, setValidationMessage] = useState('');
  const [creationStartedAt, setCreationStartedAt] = useState<number | null>(null);
  const [creationStepIndex, setCreationStepIndex] = useState(0);
  const [createdSessionId, setCreatedSessionId] = useState<string | null>(null);

  const resolvedSkillId = skillId || defaultSkillId;
  const isCreatingSession = creationStartedAt !== null || createSessionMutation.isPending;
  const displayedCreationStepIndex = creationStartedAt !== null ? creationStepIndex : 3;
  const creationCompletedStepIds = CREATE_INTERVIEW_STEPS
    .slice(0, Math.min(displayedCreationStepIndex, CREATE_INTERVIEW_STEPS.length))
    .map((step) => step.id);
  const creationCurrentStepId = CREATE_INTERVIEW_STEPS[Math.min(displayedCreationStepIndex, CREATE_INTERVIEW_STEPS.length - 1)]?.id;

  useEffect(() => {
    if (!creationStartedAt || creationStepIndex >= 3) {
      return;
    }

    const timer = window.setInterval(() => {
      setCreationStepIndex((current) => Math.min(current + 1, 3));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [creationStartedAt, creationStepIndex]);

  useEffect(() => {
    if (!createdSessionId || creationStepIndex < 3) {
      return;
    }

    setCreationStepIndex(4);
    const timer = window.setTimeout(() => {
      navigate(`/interview/${createdSessionId}`);
    }, 650);
    return () => window.clearTimeout(timer);
  }, [createdSessionId, creationStepIndex, navigate]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setValidationMessage('');

    if (!resolvedSkillId) {
      setValidationMessage('请先选择一个 Skill。');
      return;
    }

    setCreationStartedAt(Date.now());
    setCreationStepIndex(0);
    setCreatedSessionId(null);
    try {
      const session = await createSessionMutation.mutateAsync({
        skill_id: resolvedSkillId,
        resume_id: resumeId || null,
        title: title.trim() || null,
        language,
        max_rounds: maxRounds
      });
      setCreatedSessionId(session.session_id);
    } catch (error) {
      setCreationStartedAt(null);
      setCreationStepIndex(0);
      setCreatedSessionId(null);
      setValidationMessage(error instanceof Error ? error.message : '创建面试失败，请稍后重试。');
    }
  }

  return (
    <div className={styles.layout}>
      <SectionCard
        title="创建文字面试"
        description="选择 Skill、可选简历和面试参数后，即可创建新的文字面试会话。"
      >
        {(skillsQuery.isLoading || resumesQuery.isLoading) && (
          <FeedbackState title="正在准备配置数据" message="Skill 列表和简历列表正在加载。" />
        )}

        {(skillsQuery.isError || resumesQuery.isError) && (
          <FeedbackState
            tone="error"
            title="配置数据加载失败"
            message={
              skillsQuery.error instanceof Error
                ? skillsQuery.error.message
                : resumesQuery.error instanceof Error
                  ? resumesQuery.error.message
                  : '请稍后重试'
            }
          />
        )}

        {!skillsQuery.isLoading && !skillsQuery.isError ? (
          <form className={styles.form} onSubmit={handleSubmit}>
            <div className={styles.fieldGrid}>
              <label className={styles.field}>
                <span>Skill</span>
                <select value={resolvedSkillId} onChange={(event) => setSkillId(event.target.value)}>
                  {skills.map((skill) => (
                    <option key={skill.skill_id} value={skill.skill_id}>
                      {skill.display_name}
                    </option>
                  ))}
                </select>
              </label>

              <label className={styles.field}>
                <span>关联简历（可选）</span>
                <select value={resumeId} onChange={(event) => setResumeId(event.target.value)}>
                  <option value="">不关联简历</option>
                  {resumes.map((resume) => (
                    <option key={resume.resume_id} value={resume.resume_id}>
                      {resume.original_file_name}
                    </option>
                  ))}
                </select>
              </label>

              <label className={styles.field}>
                <span>会话标题</span>
                <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="例如：Python 后端模拟面试" />
              </label>

              <label className={styles.field}>
                <span>面试语言</span>
                <select value={language} onChange={(event) => setLanguage(event.target.value)}>
                  <option value="zh-CN">中文</option>
                  <option value="en-US">English</option>
                </select>
              </label>

              <label className={styles.field}>
                <span>最大轮次</span>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={maxRounds}
                  onChange={(event) => setMaxRounds(Number(event.target.value))}
                />
              </label>
            </div>

            <div className={styles.tipGrid}>
              <div className={styles.tipCard}>
                <strong>Skill 必选</strong>
                <span>问题规划与追问逻辑都依赖预设 Skill。</span>
              </div>
              <div className={styles.tipCard}>
                <strong>简历可选</strong>
                <span>如果选择简历，后端会在出题时结合简历内容做更有针对性的深挖。</span>
              </div>
            </div>

            {validationMessage ? (
              <FeedbackState tone="error" title="无法创建会话" message={validationMessage} />
            ) : null}

            {isCreatingSession ? (
              <ProcessTimeline
                title="创建面试准备中"
                description="正在生成首题，模型需要结合 Skill、简历和参考资料做一次完整规划。"
                steps={CREATE_INTERVIEW_STEPS}
                currentStepId={creationCurrentStepId}
                completedStepIds={creationCompletedStepIds}
                elapsedFrom={creationStartedAt ?? createSessionMutation.submittedAt ?? Date.now()}
              />
            ) : null}

            <div className={styles.actions}>
              <button className={styles.primaryButton} disabled={isCreatingSession} type="submit">
                {isCreatingSession ? getCreationButtonLabel(creationCurrentStepId) : '开始面试'}
              </button>
            </div>
          </form>
        ) : null}
      </SectionCard>

      <SectionCard
        title="历史面试记录"
        description="这里展示当前 visitor 已创建的所有文字面试会话，可继续进入未完成会话或查看已完成结果。"
      >
        {sessionsQuery.isLoading ? (
          <FeedbackState title="正在加载历史记录" message="当前访客的面试会话正在同步。" />
        ) : sessionsQuery.isError ? (
          <FeedbackState
            tone="error"
            title="历史记录加载失败"
            message={sessionsQuery.error instanceof Error ? sessionsQuery.error.message : '请稍后重试'}
          />
        ) : (sessionsQuery.data?.length ?? 0) === 0 ? (
          <FeedbackState title="暂无历史面试" message="创建第一场文字面试后，这里会显示对应的会话记录。" />
        ) : (
          <div className={styles.historyList}>
            {sessionsQuery.data?.map((session) => (
              <button
                key={session.session_id}
                type="button"
                className={styles.historyCard}
                onClick={() => navigate(`/interview/${session.session_id}`)}
              >
                <div className={styles.historyHeader}>
                  <strong>{session.title || `${session.skill_display_name} 模拟面试`}</strong>
                  <span className={styles.historyStatus}>
                    {resolveHistoryStatusLabel(session.completed, session.report_status)}
                  </span>
                </div>
                <div className={styles.historyMeta}>
                  <span>Skill：{session.skill_display_name}</span>
                  <span>轮次：{session.current_round}/{session.max_rounds}</span>
                  <span>已答题数：{session.answer_count}</span>
                  <span>报告：{session.report_status ?? 'pending'}</span>
                </div>
                <div className={styles.historyFooter}>
                  <span>
                    当前题目：
                    {session.current_question?.question_text ?? (session.completed ? '面试已结束' : '待生成')}
                  </span>
                  <span>更新时间：{formatDateTime(session.updated_at)}</span>
                </div>
              </button>
            ))}
          </div>
        )}
      </SectionCard>
    </div>
  );
}

function resolveHistoryStatusLabel(completed: boolean, reportStatus: string | null) {
  if (completed && reportStatus === 'processing') {
    return '报告生成中';
  }

  return completed ? '已完成' : '进行中';
}

function getCreationButtonLabel(currentStepId: string | undefined) {
  if (currentStepId === 'enter-session') {
    return '进入面试中...';
  }
  if (currentStepId === 'first-question') {
    return '生成首题中...';
  }
  return '准备面试中...';
}
