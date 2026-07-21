import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';

import { FeedbackState, ProcessTimeline, type ProcessTimelineStep, SectionCard } from '../../components/ui';
import type { InterviewReportDto } from '../../types';
import { formatDateTime, formatScore } from '../../utils/format';
import styles from './InterviewReportWorkspace.module.css';
import { useInterviewReport, useInterviewSession } from './useInterview';

type InterviewReportWorkspaceProps = {
  sessionId: string;
};

type DimensionEntry = {
  key: string;
  label: string;
  value: number;
};

const FIXED_DIMENSION_ORDER = [
  'project_experience',
  'technical_depth',
  'skill_match',
  'content_completeness',
  'communication_clarity'
] as const;

type FixedDimensionKey = (typeof FIXED_DIMENSION_ORDER)[number];

const DIMENSION_LABELS: Record<string, string> = {
  project_experience: '项目经验',
  technical_depth: '技术深度',
  skill_match: '技能匹配',
  content_completeness: '内容完整性',
  communication_clarity: '表达清晰度',
  implementation_clarity: '表达清晰度',
  problem_solving: '问题解决'
};

const DIMENSION_ALIASES: Record<string, FixedDimensionKey> = {
  project: 'project_experience',
  experience: 'project_experience',
  implementation_clarity: 'communication_clarity',
  problem_solving: 'technical_depth',
  clarity: 'communication_clarity',
  completeness: 'content_completeness',
  skills: 'skill_match',
  depth: 'technical_depth'
};

const REPORT_GENERATION_STEPS: ProcessTimelineStep[] = [
  {
    id: 'collect',
    label: '收集本场问答',
    description: '读取面试题目、候选人回答和过程信号。'
  },
  {
    id: 'question-evaluation',
    label: '逐题评估',
    description: '按主问题组评估每轮回答表现。'
  },
  {
    id: 'summary',
    label: '汇总整体表现',
    description: '整理优势亮点、待提升领域和总体评价。'
  },
  {
    id: 'dimension',
    label: '生成维度评分',
    description: '计算项目经验、技术深度、技能匹配等固定维度。'
  },
  {
    id: 'details',
    label: '整理报告细则',
    description: '准备逐题详情和最终展示内容。'
  }
];

export function InterviewReportWorkspace({ sessionId }: InterviewReportWorkspaceProps) {
  const sessionQuery = useInterviewSession(sessionId);
  const reportQuery = useInterviewReport(sessionId);
  const reportStartedAtRef = useRef(Date.now());

  const session = sessionQuery.data;
  const report = reportQuery.data;
  const isReportGenerating = !report || report.status === 'pending' || report.status === 'processing';
  const dimensionEntries = normalizeDimensionScores(report?.dimension_scores, report?.overall_score);
  const questionEvaluations = normalizeQuestionEvaluations(report?.question_evaluations);

  useEffect(() => {
    if (!session?.completed || !isReportGenerating) {
      return;
    }

    const timer = window.setInterval(() => {
      void reportQuery.refetch();
    }, 2000);

    return () => window.clearInterval(timer);
  }, [isReportGenerating, reportQuery, session?.completed]);

  if (sessionQuery.isLoading) {
    return <FeedbackState title="正在加载会话" message="正在同步当前面试会话状态。" />;
  }

  if (sessionQuery.isError) {
    return (
      <FeedbackState
        tone="error"
        title="会话加载失败"
        message={sessionQuery.error instanceof Error ? sessionQuery.error.message : '请稍后重试'}
      />
    );
  }

  if (session && !session.completed) {
    return (
      <SectionCard
        title="统一评估报告"
        description="统一评估会在面试完成后生成，避免答题过程中受到报告反馈干扰。"
      >
        <FeedbackState
          title="面试未完成"
          message="当前会话仍在进行中，请完成所有问答后再查看统一评估报告。"
          action={
            <Link className={styles.secondaryButton} to={`/interview/${sessionId}`}>
              返回面试会话
            </Link>
          }
        />
      </SectionCard>
    );
  }

  if (reportQuery.isLoading) {
    return <FeedbackState title="正在加载报告" message="统一评估报告状态与内容正在同步。" />;
  }

  if (reportQuery.isError) {
    return (
      <FeedbackState
        tone="error"
        title="报告加载失败"
        message={reportQuery.error instanceof Error ? reportQuery.error.message : '请稍后重试'}
      />
    );
  }

  if (isReportGenerating) {
    return (
      <SectionCard
        title="统一评估报告"
        description="报告正在由服务端统一评估生成，稍后刷新即可查看完整细则。"
        actions={
          <Link className={styles.secondaryButton} to={`/interview/${sessionId}`}>
            返回面试会话
          </Link>
        }
      >
        <ProcessTimeline
          title="报告生成中"
          description="统一评估尚未完成，页面会自动刷新报告状态。"
          slowMessage="报告还在生成，可能包含较多题目或模型调用较慢。你可以稍后回来查看。"
          steps={REPORT_GENERATION_STEPS}
          currentStepId="question-evaluation"
          completedStepIds={['collect']}
          elapsedFrom={reportStartedAtRef.current}
        />
      </SectionCard>
    );
  }

  if (report.status === 'failed') {
    return (
      <SectionCard
        title="统一评估报告"
        description="报告生成失败，错误信息如下。"
        actions={
          <Link className={styles.secondaryButton} to={`/interview/${sessionId}`}>
            返回面试会话
          </Link>
        }
      >
        <FeedbackState
          tone="error"
          title="报告生成失败"
          message={report.error_message || report.summary_text || '统一评估未能完成，请稍后重试。'}
        />
      </SectionCard>
    );
  }

  return (
    <div className={styles.layout}>
      <SectionCard
        title={session?.title || session?.skill_display_name || '统一评估报告'}
        description="从总体表现、能力维度和逐题细则三个层面复盘本次面试。"
        actions={
          <Link className={styles.secondaryButton} to={`/interview/${sessionId}`}>
            返回面试会话
          </Link>
        }
      >
        <div className={styles.heroGrid}>
          <div className={styles.summaryPanel}>
            <span className={styles.eyebrow}>核心评价</span>
            <p className={styles.summaryText}>{report.summary_text || '统一评估已完成。'}</p>
            <div className={styles.metricGrid}>
              <MetricCard label="总分" value={formatScore(report.overall_score)} highlight />
              <MetricCard label="总体评级" value={report.overall_rating || '待生成'} />
              <MetricCard label="报告状态" value={report.status} />
              <MetricCard label="生成时间" value={formatDateTime(report.generated_at)} />
            </div>
          </div>

          <ReportList title="优势亮点" items={normalizeStringList(report.strengths)} tone="success" />
          <ReportList title="待提升领域" items={normalizeStringList(report.weaknesses)} tone="warning" />
        </div>
      </SectionCard>

      <SectionCard title="维度评分图" description="按项目经验、技术深度、技能匹配、内容完整性和表达清晰度五个固定维度展示。">
        {dimensionEntries.length > 0 ? (
          <div className={styles.dimensionLayout}>
            <RadarChart dimensions={dimensionEntries} />
            <div className={styles.dimensionBars}>
              {dimensionEntries.map((dimension) => (
                <div key={dimension.key} className={styles.dimensionBarItem}>
                  <div className={styles.dimensionBarHeader}>
                    <span>{dimension.label}</span>
                    <strong>{formatScore(dimension.value)}</strong>
                  </div>
                  <div className={styles.progressTrack} aria-hidden="true">
                    <span className={styles.progressFill} style={{ width: `${clampScore(dimension.value)}%` }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <FeedbackState title="暂无维度分" message="当前报告没有返回可展示的维度评分。" />
        )}
      </SectionCard>

      <SectionCard title="逐题详细评价" description="每道题保留评分理由、亮点、短板和后续复盘方向。">
        {questionEvaluations.length > 0 ? (
          <div className={styles.questionList}>
            {questionEvaluations.map((item) => (
              <article key={item.question_key} className={styles.questionCard}>
                <div className={styles.questionHeader}>
                  <div>
                    <span className={styles.questionKey}>{item.question_key}</span>
                    <h3>{item.question_text}</h3>
                  </div>
                  <div className={styles.scoreBadge}>
                    <strong>{formatScore(item.score)}</strong>
                    <span>{item.rating}</span>
                  </div>
                </div>

                <div className={styles.answerBlock}>
                  <span>候选人回答</span>
                  <p>{item.answer_text}</p>
                </div>

                <div className={styles.rationaleBlock}>
                  <span>评分理由</span>
                  <p>{item.rationale}</p>
                </div>

                <div className={styles.detailGrid}>
                  <ReportList title="本题亮点" items={normalizeStringList(item.strengths)} tone="success" compact />
                  <ReportList title="本题短板" items={normalizeStringList(item.weaknesses)} tone="danger" compact />
                  <ReportList title="改进方向" items={normalizeStringList(item.suggestions)} tone="warning" compact />
                </div>
              </article>
            ))}
          </div>
        ) : (
          <FeedbackState title="暂无逐题评价" message="当前报告没有返回逐题评估明细。" />
        )}
      </SectionCard>
    </div>
  );
}

function MetricCard({ label, value, highlight = false }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className={highlight ? `${styles.metricCard} ${styles.metricCardHighlight}` : styles.metricCard}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ReportList({
  title,
  items,
  tone = 'neutral',
  compact = false
}: {
  title: string;
  items: string[];
  tone?: 'neutral' | 'success' | 'warning' | 'danger';
  compact?: boolean;
}) {
  if (items.length === 0) {
    return null;
  }

  return (
    <div className={`${styles.reportListCard} ${styles[`tone_${tone}`]} ${compact ? styles.compactCard : ''}`}>
      <h4>{title}</h4>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function RadarChart({ dimensions }: { dimensions: DimensionEntry[] }) {
  const size = 280;
  const center = size / 2;
  const radius = 92;
  const rings = [0.25, 0.5, 0.75, 1];
  const points = dimensions.map((dimension, index) => {
    const angle = (Math.PI * 2 * index) / dimensions.length - Math.PI / 2;
    const valueRadius = radius * (clampScore(dimension.value) / 100);
    return {
      ...dimension,
      axisX: center + Math.cos(angle) * radius,
      axisY: center + Math.sin(angle) * radius,
      valueX: center + Math.cos(angle) * valueRadius,
      valueY: center + Math.sin(angle) * valueRadius,
      labelX: center + Math.cos(angle) * (radius + 30),
      labelY: center + Math.sin(angle) * (radius + 30)
    };
  });
  const polygonPoints = points.map((point) => `${point.valueX},${point.valueY}`).join(' ');

  return (
    <div className={styles.radarCard}>
      <svg className={styles.radarSvg} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="维度评分雷达图">
        {rings.map((ring) => {
          const ringPoints = dimensions
            .map((_, index) => {
              const angle = (Math.PI * 2 * index) / dimensions.length - Math.PI / 2;
              return `${center + Math.cos(angle) * radius * ring},${center + Math.sin(angle) * radius * ring}`;
            })
            .join(' ');
          return <polygon key={ring} points={ringPoints} className={styles.radarRing} />;
        })}

        {points.map((point) => (
          <line
            key={point.key}
            x1={center}
            y1={center}
            x2={point.axisX}
            y2={point.axisY}
            className={styles.radarAxis}
          />
        ))}

        <polygon points={polygonPoints} className={styles.radarArea} />

        {points.map((point) => (
          <g key={point.key}>
            <circle cx={point.valueX} cy={point.valueY} r="4" className={styles.radarDot} />
            <text x={point.labelX} y={point.labelY} textAnchor="middle" className={styles.radarLabel}>
              {point.label}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

function normalizeDimensionScores(value: unknown, fallbackScore?: number | null): DimensionEntry[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return [];
  }

  const normalizedScores = new Map<FixedDimensionKey, number>();
  for (const [key, rawValue] of Object.entries(value as Record<string, unknown>)) {
    if (typeof rawValue !== 'number' || !Number.isFinite(rawValue)) {
      continue;
    }
    const canonicalKey = DIMENSION_ALIASES[key] ?? key;
    if (!isFixedDimensionKey(canonicalKey)) {
      continue;
    }
    if (!normalizedScores.has(canonicalKey)) {
      normalizedScores.set(canonicalKey, clampScore(rawValue));
    }
  }

  if (normalizedScores.size === 0) {
    return [];
  }

  const fallbackValue =
    typeof fallbackScore === 'number' && Number.isFinite(fallbackScore)
      ? clampScore(fallbackScore)
      : Array.from(normalizedScores.values()).reduce((sum, score) => sum + score, 0) / normalizedScores.size;

  for (const key of FIXED_DIMENSION_ORDER) {
    if (!normalizedScores.has(key)) {
      normalizedScores.set(key, fallbackValue);
    }
  }

  return FIXED_DIMENSION_ORDER.map((key) => ({
    key,
    label: DIMENSION_LABELS[key] ?? key,
    value: clampScore(normalizedScores.get(key) ?? fallbackValue)
  }));
}

function isFixedDimensionKey(key: string): key is FixedDimensionKey {
  return FIXED_DIMENSION_ORDER.includes(key as FixedDimensionKey);
}

function normalizeQuestionEvaluations(value: unknown): InterviewReportDto['question_evaluations'] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter((item): item is InterviewReportDto['question_evaluations'][number] => {
    return typeof item === 'object' && item !== null && typeof item.question_key === 'string';
  });
}

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter((item): item is string => typeof item === 'string' && item.length > 0);
}

function clampScore(value: number) {
  return Math.max(0, Math.min(100, value));
}
