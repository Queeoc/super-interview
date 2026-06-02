import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';

import { FeedbackState, MarkdownRenderer, SectionCard } from '../../components/ui';
import { useDebouncedEffect } from '../../hooks/useDebouncedEffect';
import { submitInterviewAnswerStream } from '../../services/api';
import type {
  InterviewAnswerHistoryDto,
  InterviewContentEvent,
  InterviewErrorEvent,
  InterviewQuestionSnapshot,
  InterviewReportDto,
  InterviewStepCompleteEvent,
  InterviewStreamEvent
} from '../../types';
import { formatDateTime, formatScore } from '../../utils/format';
import { setRecentInterviewSessionId } from '../../utils/storage';
import styles from './InterviewSessionWorkspace.module.css';
import {
  useCompleteInterviewSession,
  useInterviewReport,
  useInterviewReportExport,
  useInterviewSession,
  useSaveInterviewDraft
} from './useInterview';

type InterviewSessionWorkspaceProps = {
  sessionId: string;
};

type OptimisticAnswer = {
  questionKey: string | null;
  roundIndex: number;
  content: string;
};

type ChatMessage =
  | {
      id: string;
      role: 'assistant';
      status: 'answered' | 'current' | 'streaming' | 'closing';
      questionKey: string | null;
      roundIndex: number;
      content: string;
      submittedAt?: string | null;
    }
  | {
      id: string;
      role: 'user';
      status: 'answered';
      questionKey: string | null;
      roundIndex: number;
      content: string;
      submittedAt?: string | null;
    };

export function InterviewSessionWorkspace({ sessionId }: InterviewSessionWorkspaceProps) {
  const queryClient = useQueryClient();
  const sessionQuery = useInterviewSession(sessionId);
  const reportQuery = useInterviewReport(sessionId);
  const exportQuery = useInterviewReportExport(sessionId);
  const draftMutation = useSaveInterviewDraft(sessionId);
  const completeMutation = useCompleteInterviewSession(sessionId);

  const [answerText, setAnswerText] = useState('');
  const [draftMessage, setDraftMessage] = useState('');
  const [streamLog, setStreamLog] = useState<string[]>([]);
  const [streamError, setStreamError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [optimisticAnswer, setOptimisticAnswer] = useState<OptimisticAnswer | null>(null);
  const [streamingQuestionText, setStreamingQuestionText] = useState('');
  const [pendingStreamContent, setPendingStreamContent] = useState('');
  const [closingMessage, setClosingMessage] = useState('');
  const [pendingNextQuestion, setPendingNextQuestion] = useState<InterviewQuestionSnapshot | null>(null);

  const autoDraftEnabledRef = useRef(false);
  const pendingStreamContentRef = useRef('');

  const session = sessionQuery.data;
  const report = reportQuery.data;

  useEffect(() => {
    if (!sessionId) {
      return;
    }
    setRecentInterviewSessionId(sessionId);
  }, [sessionId]);

  useEffect(() => {
    const draftAnswer = session?.last_draft_answer;
    if (!draftAnswer || typeof draftAnswer.answer_text !== 'string') {
      return;
    }

    if (!autoDraftEnabledRef.current) {
      setAnswerText(draftAnswer.answer_text);
      autoDraftEnabledRef.current = true;
    }
  }, [session]);

  const currentQuestion = session?.current_question ?? null;
  const activeQuestion = pendingNextQuestion ?? currentQuestion;
  const canAnswer = Boolean(session && !session.completed && activeQuestion);
  const isInterviewFinished = Boolean(session?.completed);
  const shouldShowReport = Boolean(session?.completed || report?.status === 'generated' || report?.status === 'failed');

  useEffect(() => {
    if (!optimisticAnswer || !session?.answers?.length) {
      return;
    }

    const persisted = session.answers.some(
      (answer) => answer.question_key === optimisticAnswer.questionKey && answer.answer_text === optimisticAnswer.content
    );

    if (persisted) {
      setOptimisticAnswer(null);
    }
  }, [optimisticAnswer, session?.answers]);

  useEffect(() => {
    pendingStreamContentRef.current = pendingStreamContent;
  }, [pendingStreamContent]);

  useEffect(() => {
    if (!pendingNextQuestion || !pendingStreamContent) {
      return;
    }

    setStreamingQuestionText('');
    let index = 0;
    const fullText = pendingStreamContent;
    const timer = window.setInterval(() => {
      index += 1;
      setStreamingQuestionText(fullText.slice(0, index));

      if (index >= fullText.length) {
        window.clearInterval(timer);
      }
    }, 28);

    return () => {
      window.clearInterval(timer);
    };
  }, [pendingNextQuestion?.question_key, pendingStreamContent]);

  useEffect(() => {
    if (pendingNextQuestion && currentQuestion?.question_key === pendingNextQuestion.question_key) {
      setPendingNextQuestion(null);
      setPendingStreamContent('');
      setStreamingQuestionText('');
    }
  }, [currentQuestion, pendingNextQuestion]);

  async function saveDraft(reason: 'manual' | 'auto') {
    if (!activeQuestion || !answerText.trim()) {
      return;
    }

    try {
      const response = await draftMutation.mutateAsync({
        answer_text: answerText,
        question_key: activeQuestion.question_key,
        answer_metadata: {
          source: reason
        }
      });
      setDraftMessage(
        reason === 'manual' ? response.message : `草稿已自动暂存：${formatDateTime(new Date().toISOString())}`
      );
    } catch (error) {
      setDraftMessage(error instanceof Error ? error.message : '草稿保存失败');
    }
  }

  useDebouncedEffect(
    () => {
      if (autoDraftEnabledRef.current && canAnswer && answerText.trim()) {
        void saveDraft('auto');
      }
    },
    1200,
    [answerText, canAnswer, activeQuestion?.question_key]
  );

  async function handleSubmitAnswer() {
    if (!activeQuestion || !answerText.trim()) {
      return;
    }

    const submittedQuestion = activeQuestion;
    const submittedAnswer = answerText.trim();

    setIsSubmitting(true);
    setDraftMessage('');
    setStreamError('');
    setStreamLog([]);
    setOptimisticAnswer({
      questionKey: submittedQuestion.question_key,
      roundIndex: submittedQuestion.round_index,
      content: submittedAnswer
    });
    setPendingNextQuestion(null);
    setPendingStreamContent('');
    setClosingMessage('');
    setStreamingQuestionText('');
    setAnswerText('');

    try {
      await submitInterviewAnswerStream(
        sessionId,
        {
          answer_text: submittedAnswer,
          question_key: submittedQuestion.question_key,
          answer_metadata: {}
        },
        {
          onEvent: (event) => {
            handleStreamEvent(event);
          },
          onError: (error) => {
            setStreamError(error instanceof Error ? error.message : '流式请求失败');
          }
        }
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['interview', 'session', sessionId] }),
        queryClient.invalidateQueries({ queryKey: ['interview', 'report', sessionId] }),
        queryClient.invalidateQueries({ queryKey: ['interview', 'report-export', sessionId] })
      ]);
    } catch (error) {
      setOptimisticAnswer(null);
      setAnswerText(submittedAnswer);
      setStreamError(error instanceof Error ? error.message : '提交失败，请稍后重试');
    } finally {
      setIsSubmitting(false);
    }
  }

  function handleStreamEvent(event: InterviewStreamEvent) {
    if (event.type === 'status') {
      setStreamLog((current) => [...current, `状态：${event.message}`]);
      return;
    }

    if (event.type === 'plan') {
      setStreamLog((current) => [
        ...current,
        `推进决策：${event.action ?? 'unknown'}${event.reason ? ` · ${event.reason}` : ''}`
      ]);
      return;
    }

    if (event.type === 'content') {
      const contentEvent = event as InterviewContentEvent;
      setPendingStreamContent(contentEvent.content);
      setClosingMessage('');
      setStreamLog((current) => [...current, '本轮回答已处理，正在生成下一条提问。']);
      return;
    }

    if (event.type === 'step_complete') {
      const stepEvent = event as InterviewStepCompleteEvent;
      if (stepEvent.response.completed) {
        setPendingNextQuestion(null);
        setStreamingQuestionText('');
        setClosingMessage(pendingStreamContentRef.current || stepEvent.response.message);
        setPendingStreamContent('');
      } else if (stepEvent.response.current_question) {
        setPendingNextQuestion(stepEvent.response.current_question);
      }

      setStreamLog((current) => [...current, `步骤完成：${stepEvent.response.message}`]);
      return;
    }

    if (event.type === 'report') {
      setStreamLog((current) => [...current, '统一评估报告已生成，正在刷新最终结果。']);
      void queryClient.invalidateQueries({ queryKey: ['interview', 'report', sessionId] });
      void queryClient.invalidateQueries({ queryKey: ['interview', 'report-export', sessionId] });
      return;
    }

    if (event.type === 'done') {
      setStreamLog((current) => [...current, '当前轮流式流程已结束。']);
      return;
    }

    if (event.type === 'error') {
      const errorEvent = event as InterviewErrorEvent;
      setStreamError(`${errorEvent.message} (code: ${errorEvent.code})`);
    }
  }

  async function handleCompleteSession() {
    try {
      await completeMutation.mutateAsync();
      setStreamLog((current) => [...current, '已手动结束面试，会话与报告正在刷新。']);
    } catch (error) {
      setStreamError(error instanceof Error ? error.message : '结束面试失败');
    }
  }

  const chatMessages = useMemo(
    () =>
      buildChatMessages(
        session?.answers ?? [],
        currentQuestion,
        pendingNextQuestion,
        streamingQuestionText,
        optimisticAnswer,
        closingMessage
      ),
    [session?.answers, currentQuestion, pendingNextQuestion, streamingQuestionText, optimisticAnswer, closingMessage]
  );

  const reportSummary = useMemo(() => buildReportSummary(report), [report]);
  const reportStrengths = normalizeStringList(report?.strengths);
  const reportWeaknesses = normalizeStringList(report?.weaknesses);
  const reportSuggestions = normalizeStringList(report?.suggestions);
  const reportQuestionEvaluations = normalizeQuestionEvaluations(report?.question_evaluations);
  const reportDimensionScores = normalizeDimensionScores(report?.dimension_scores);

  return (
    <div className={styles.layout}>
      <div className={styles.mainColumn}>
        <SectionCard
          title={session?.title || session?.skill_display_name || '面试会话'}
          description="会话记录与指标都以服务端快照为准；新问题会在提交当前答案后以流式方式逐字出现。"
          actions={
            <button
              className={styles.secondaryButton}
              disabled={!session || session.completed || completeMutation.isPending}
              type="button"
              onClick={handleCompleteSession}
            >
              {completeMutation.isPending ? '结束中...' : '手动结束面试'}
            </button>
          }
        >
          {sessionQuery.isLoading ? (
            <FeedbackState title="正在加载会话" message="当前会话快照正在同步。" />
          ) : sessionQuery.isError ? (
            <FeedbackState
              tone="error"
              title="会话加载失败"
              message={sessionQuery.error instanceof Error ? sessionQuery.error.message : '请稍后重试'}
            />
          ) : session ? (
            <div className={styles.sessionContent}>
              <div className={styles.snapshotGrid}>
                <SnapshotCard label="会话状态" value={session.status} />
                <SnapshotCard label="当前轮次" value={`${session.current_round}/${session.max_rounds}`} />
                <SnapshotCard label="已答题数" value={`${session.answer_count}`} />
                <SnapshotCard label="报告状态" value={session.report_status ?? 'pending'} />
              </div>

              <div className={styles.chatTimeline}>
                {chatMessages.length === 0 ? (
                  <FeedbackState title="等待题目" message="当前还没有可展示的问答记录。" />
                ) : (
                  chatMessages.map((message) => (
                    <ChatBubble key={message.id} message={message} />
                  ))
                )}
              </div>

              {!isInterviewFinished ? (
                <div className={styles.section}>
                  <h3 className={styles.sectionTitle}>当前回答</h3>
                  <textarea
                    className={styles.textarea}
                    disabled={!canAnswer || isSubmitting}
                    value={answerText}
                    onChange={(event) => setAnswerText(event.target.value)}
                    placeholder="在这里输入你的回答，草稿会自动暂存。"
                    rows={9}
                  />

                  <div className={styles.actionRow}>
                    <button
                      className={styles.secondaryButton}
                      disabled={!canAnswer || !answerText.trim() || draftMutation.isPending}
                      type="button"
                      onClick={() => void saveDraft('manual')}
                    >
                      {draftMutation.isPending ? '保存中...' : '保存草稿'}
                    </button>
                    <button
                      className={styles.primaryButton}
                      disabled={!canAnswer || !answerText.trim() || isSubmitting}
                      type="button"
                      onClick={() => void handleSubmitAnswer()}
                    >
                      {isSubmitting ? '提交中...' : '提交答案'}
                    </button>
                  </div>

                  {draftMessage ? (
                    <FeedbackState
                      tone={draftMutation.isError ? 'error' : 'success'}
                      title="草稿状态"
                      message={draftMessage}
                    />
                  ) : null}
                </div>
              ) : (
                <FeedbackState
                  tone="success"
                  title="面试已完成"
                  message="问答流程已经结束，统一评估报告会在下方展示。"
                />
              )}
            </div>
          ) : null}
        </SectionCard>

        <SectionCard
          title="流式推进状态"
          description="保留当前轮的服务端推进信息，便于排查 follow-up、切题和完成时机。"
        >
          <div className={styles.streamSection}>
            {streamLog.length === 0 && !streamError ? (
              <FeedbackState title="等待提交" message="提交答案后，这里会显示本轮的流式状态推进。" />
            ) : (
              <>
                {streamLog.length > 0 ? (
                  <div className={styles.logList}>
                    {streamLog.map((item, index) => (
                      <div key={`${item}-${index}`} className={styles.logItem}>
                        {item}
                      </div>
                    ))}
                  </div>
                ) : null}

                {streamError ? <FeedbackState tone="error" title="流式过程异常" message={streamError} /> : null}
              </>
            )}
          </div>
        </SectionCard>

        {shouldShowReport ? (
          <SectionCard
            title="统一评估报告"
            description="统一评估只在全部问题完成后展示，避免答题中途干扰当前面试。"
          >
            {reportQuery.isLoading ? (
              <FeedbackState title="正在加载报告" message="报告状态与内容正在同步。" />
            ) : reportQuery.isError ? (
              <FeedbackState
                tone="error"
                title="报告加载失败"
                message={reportQuery.error instanceof Error ? reportQuery.error.message : '请稍后重试'}
              />
            ) : report ? (
              <div className={styles.reportSection}>
                <div className={styles.reportMetaGrid}>
                  <SnapshotCard label="报告状态" value={report.status} />
                  <SnapshotCard label="总体评分" value={formatScore(report.overall_score)} />
                  <SnapshotCard label="总体评级" value={report.overall_rating ?? '待生成'} />
                  <SnapshotCard label="生成时间" value={formatDateTime(report.generated_at)} />
                </div>

                <FeedbackState
                  tone={report.status === 'failed' ? 'error' : report.status === 'generated' ? 'success' : 'neutral'}
                  title="报告摘要"
                  message={
                    report.error_message ||
                    report.summary_text ||
                    '统一评估报告尚未生成，请稍后刷新查看。'
                  }
                />

                {reportSummary ? (
                  <div className={styles.reportSummaryGrid}>
                    <ReportList title="亮点" items={reportStrengths} />
                    <ReportList title="短板" items={reportWeaknesses} />
                    <ReportList title="建议" items={reportSuggestions} />
                  </div>
                ) : null}

                {Object.keys(reportDimensionScores).length > 0 ? (
                  <div className={styles.dimensionGrid}>
                    {Object.entries(reportDimensionScores).map(([key, value]) => (
                      <div key={key} className={styles.dimensionCard}>
                        <span>{key}</span>
                        <strong>{formatScore(value)}</strong>
                      </div>
                    ))}
                  </div>
                ) : null}

                {reportQuestionEvaluations.length > 0 ? (
                  <div className={styles.questionEvaluationList}>
                    {reportQuestionEvaluations.map((item) => (
                      <div key={item.question_key} className={styles.questionEvaluationCard}>
                        <div className={styles.questionEvaluationHeader}>
                          <strong>{item.question_key}</strong>
                          <span>
                            {item.rating} · {formatScore(item.score)}
                          </span>
                        </div>
                        <p className={styles.questionEvaluationText}>{item.question_text}</p>
                        <p className={styles.questionEvaluationMeta}>{item.rationale}</p>
                      </div>
                    ))}
                  </div>
                ) : null}

                <div className={styles.section}>
                  <h3 className={styles.sectionTitle}>Markdown 报告</h3>
                  <MarkdownRenderer content={report.markdown_content} />
                </div>

                {exportQuery.data ? (
                  <div className={styles.exportCard}>
                    <strong>导出占位</strong>
                    <span>文件名：{exportQuery.data.file_name}</span>
                    <span>状态：{exportQuery.data.report_status}</span>
                    <span>格式：{exportQuery.data.export_format}</span>
                  </div>
                ) : null}
              </div>
            ) : null}
          </SectionCard>
        ) : (
          <SectionCard
            title="统一评估报告"
            description="统一评估报告会在所有问题都回答完毕后再出现。"
          >
            <FeedbackState
              title="报告暂未开放"
              message="当前仍在面试过程中。为避免干扰答题，报告区会在全部问答结束后再显示。"
            />
          </SectionCard>
        )}
      </div>
    </div>
  );
}

function SnapshotCard({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.snapshotCard}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ReportList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) {
    return null;
  }

  return (
    <div className={styles.reportListCard}>
      <h4>{title}</h4>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function ChatBubble({ message }: { message: ChatMessage }) {
  const bubbleClass =
    message.role === 'assistant'
      ? message.status === 'streaming'
        ? `${styles.chatBubble} ${styles.chatBubbleAssistant} ${styles.chatBubbleStreaming}`
        : `${styles.chatBubble} ${styles.chatBubbleAssistant}`
      : `${styles.chatBubble} ${styles.chatBubbleUser}`;

  return (
    <article className={bubbleClass}>
      <div className={styles.chatMeta}>
        <strong>{message.role === 'assistant' ? '面试官' : '你'}</strong>
        <span>第 {message.roundIndex} 轮</span>
        {message.questionKey ? <span>{message.questionKey}</span> : null}
        {message.submittedAt ? <span>{formatDateTime(message.submittedAt)}</span> : null}
      </div>
      <p className={styles.chatContent}>{message.content}</p>
    </article>
  );
}

function buildChatMessages(
  answers: InterviewAnswerHistoryDto[],
  currentQuestion: InterviewQuestionSnapshot | null,
  pendingNextQuestion: InterviewQuestionSnapshot | null,
  streamingQuestionText: string,
  optimisticAnswer: OptimisticAnswer | null,
  closingMessage: string
): ChatMessage[] {
  const messages: ChatMessage[] = [];
  const answeredQuestionKeys = new Set(answers.map((answer) => answer.question_key).filter(Boolean));

  for (const answer of answers) {
    messages.push({
      id: `question-${answer.answer_id}`,
      role: 'assistant',
      status: 'answered',
      questionKey: answer.question_key,
      roundIndex: answer.round_index,
      content: answer.question_text,
      submittedAt: answer.submitted_at
    });
    messages.push({
      id: `answer-${answer.answer_id}`,
      role: 'user',
      status: 'answered',
      questionKey: answer.question_key,
      roundIndex: answer.round_index,
      content: answer.answer_text,
      submittedAt: answer.submitted_at
    });
  }

  if (currentQuestion && !answeredQuestionKeys.has(currentQuestion.question_key)) {
    messages.push({
      id: `current-${currentQuestion.question_key}`,
      role: 'assistant',
      status: 'current',
      questionKey: currentQuestion.question_key,
      roundIndex: currentQuestion.round_index,
      content: currentQuestion.question_text
    });
  }

  if (optimisticAnswer && !answeredQuestionKeys.has(optimisticAnswer.questionKey)) {
    messages.push({
      id: `optimistic-answer-${optimisticAnswer.questionKey ?? optimisticAnswer.roundIndex}`,
      role: 'user',
      status: 'answered',
      questionKey: optimisticAnswer.questionKey,
      roundIndex: optimisticAnswer.roundIndex,
      content: optimisticAnswer.content
    });
  }

  if (pendingNextQuestion && pendingNextQuestion.question_key !== currentQuestion?.question_key) {
    messages.push({
      id: `pending-${pendingNextQuestion.question_key}`,
      role: 'assistant',
      status:
        streamingQuestionText && streamingQuestionText.length < pendingNextQuestion.question_text.length
          ? 'streaming'
          : 'current',
      questionKey: pendingNextQuestion.question_key,
      roundIndex: pendingNextQuestion.round_index,
      content: streamingQuestionText || pendingNextQuestion.question_text
    });
  } else if (closingMessage) {
    messages.push({
      id: 'closing-message',
      role: 'assistant',
      status: 'closing',
      questionKey: null,
      roundIndex: answers.length > 0 ? answers[answers.length - 1].round_index : 0,
      content: closingMessage
    });
  }

  return messages;
}

function buildReportSummary(report: InterviewReportDto | undefined) {
  if (!report) {
    return false;
  }

  return (
    normalizeStringList(report.strengths).length > 0 ||
    normalizeStringList(report.weaknesses).length > 0 ||
    normalizeStringList(report.suggestions).length > 0
  );
}

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter((item): item is string => typeof item === 'string' && item.length > 0);
}

function normalizeQuestionEvaluations(value: unknown): InterviewReportDto['question_evaluations'] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter((item): item is InterviewReportDto['question_evaluations'][number] => {
    return typeof item === 'object' && item !== null && typeof item.question_key === 'string';
  });
}

function normalizeDimensionScores(value: unknown): Record<string, number> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return {};
  }

  const entries = Object.entries(value as Record<string, unknown>).filter((entry): entry is [string, number] => {
    return typeof entry[1] === 'number' && Number.isFinite(entry[1]);
  });

  return Object.fromEntries(entries);
}
