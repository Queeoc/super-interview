import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import { FeedbackState, SectionCard } from '../../components/ui';
import { useDebouncedEffect } from '../../hooks/useDebouncedEffect';
import { submitInterviewAnswerStream } from '../../services/api';
import type {
  InterviewAnswerHistoryDto,
  InterviewContentEvent,
  InterviewErrorEvent,
  InterviewProcessRunDto,
  InterviewProcessStepStatus,
  InterviewProcessToolSummary,
  InterviewQuestionSnapshot,
  InterviewStepCompleteEvent,
  InterviewStatusEvent,
  InterviewStreamEvent
} from '../../types';
import { formatDateTime } from '../../utils/format';
import { setRecentInterviewSessionId } from '../../utils/storage';
import styles from './InterviewSessionWorkspace.module.css';
import {
  useCompleteInterviewSession,
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

type ProcessStepStatus = InterviewProcessStepStatus;

type ProcessStepId =
  | 'submit'
  | 'observation'
  | 'replan'
  | 'tool_prepare'
  | 'tool_call'
  | 'llm'
  | 'persist'
  | 'report'
  | 'done';

type ProcessToolSummary = InterviewProcessToolSummary;

type ProcessStep = {
  id: ProcessStepId;
  label: string;
  detail?: string;
  status: ProcessStepStatus;
  toolSummary?: ProcessToolSummary;
  timestamp: number;
};

type ProcessRun = {
  id: string;
  questionKey: string | null;
  roundIndex: number;
  status: 'running' | 'completed' | 'failed';
  collapsed: boolean;
  steps: ProcessStep[];
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
  const draftMutation = useSaveInterviewDraft(sessionId);
  const completeMutation = useCompleteInterviewSession(sessionId);

  const [answerText, setAnswerText] = useState('');
  const [draftMessage, setDraftMessage] = useState('');
  const [streamLog, setStreamLog] = useState<string[]>([]);
  const [streamError, setStreamError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [currentProcessStepId, setCurrentProcessStepId] = useState<ProcessStepId | null>(null);
  const [processRuns, setProcessRuns] = useState<ProcessRun[]>([]);
  const [optimisticAnswer, setOptimisticAnswer] = useState<OptimisticAnswer | null>(null);
  const [streamingQuestionText, setStreamingQuestionText] = useState('');
  const [pendingStreamContent, setPendingStreamContent] = useState('');
  const [closingMessage, setClosingMessage] = useState('');
  const [pendingNextQuestion, setPendingNextQuestion] = useState<InterviewQuestionSnapshot | null>(null);

  const autoDraftEnabledRef = useRef(false);
  const activeProcessRunIdRef = useRef<string | null>(null);
  const pendingStreamContentRef = useRef('');

  const session = sessionQuery.data;

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
    const persistedRuns = buildProcessRunsFromAnswers(session?.answers ?? []);
    if (persistedRuns.length === 0) {
      return;
    }

    setProcessRuns((current) => mergeProcessRuns(current, persistedRuns));
  }, [session?.answers]);

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

  function updateActiveProcessStep({
    stepId,
    label,
    detail,
    status,
    toolSummary
  }: {
    stepId: ProcessStepId;
    label: string;
    detail?: string;
    status: ProcessStepStatus;
    toolSummary?: ProcessToolSummary;
  }) {
    const runId = activeProcessRunIdRef.current;
    if (!runId) {
      return;
    }

    setProcessRuns((current) =>
      current.map((run) => {
        if (run.id !== runId) {
          return run;
        }

        const existingStep = run.steps.find((step) => step.id === stepId);
        const nextSteps =
          status === 'active'
            ? run.steps.map((step) =>
                step.status === 'active' && step.id !== stepId ? { ...step, status: 'completed' as const } : step
              )
            : [...run.steps];
        const nextStep: ProcessStep = {
          id: stepId,
          label,
          detail: detail || existingStep?.detail,
          status,
          toolSummary: toolSummary || existingStep?.toolSummary,
          timestamp: existingStep?.timestamp ?? Date.now()
        };

        if (existingStep) {
          return {
            ...run,
            steps: nextSteps.map((step) => (step.id === stepId ? nextStep : step))
          };
        }

        return {
          ...run,
          steps: [...nextSteps, nextStep]
        };
      })
    );
  }

  function failActiveProcessRun(message: string) {
    const runId = activeProcessRunIdRef.current;
    if (!runId) {
      return;
    }

    setProcessRuns((current) =>
      current.map((run) => {
        if (run.id !== runId) {
          return run;
        }

        const activeStep = run.steps.find((step) => step.status === 'active') ?? run.steps[run.steps.length - 1];
        const failedStepId = activeStep?.id ?? 'submit';
        const failedStep: ProcessStep = {
          id: failedStepId,
          label: activeStep?.label ?? '提交答案',
          detail: message,
          status: 'failed',
          toolSummary: activeStep?.toolSummary,
          timestamp: activeStep?.timestamp ?? Date.now()
        };
        const hasFailedStep = run.steps.some((step) => step.id === failedStepId);

        return {
          ...run,
          status: 'failed',
          steps: hasFailedStep
            ? run.steps.map((step) => (step.id === failedStepId ? failedStep : step))
            : [...run.steps, failedStep]
        };
      })
    );
    activeProcessRunIdRef.current = null;
    setCurrentProcessStepId(null);
  }

  function completeActiveProcessRun() {
    const runId = activeProcessRunIdRef.current;
    if (!runId) {
      return;
    }

    setProcessRuns((current) =>
      current.map((run) =>
        run.id === runId
          ? {
              ...run,
              status: 'completed',
              collapsed: true,
              steps: [
                ...run.steps.map((step) =>
                  step.status === 'active' ? { ...step, status: 'completed' as const } : step
                ),
                ...(run.steps.some((step) => step.id === 'done')
                  ? []
                  : [
                      {
                        id: 'done' as const,
                        label: '本轮完成',
                        detail: '当前轮流式流程已结束，可以继续作答或查看结果。',
                        status: 'completed' as const,
                        timestamp: Date.now()
                      }
                    ])
              ]
            }
          : run
      )
    );
    activeProcessRunIdRef.current = null;
    setCurrentProcessStepId(null);
  }

  function toggleProcessRunCollapsed(runId: string) {
    setProcessRuns((current) =>
      current.map((run) => (run.id === runId ? { ...run, collapsed: !run.collapsed } : run))
    );
  }

  async function handleSubmitAnswer() {
    if (!activeQuestion || !answerText.trim()) {
      return;
    }

    const submittedQuestion = activeQuestion;
    const submittedAnswer = answerText.trim();
    const processRunId = `${submittedQuestion.question_key ?? 'round'}-${submittedQuestion.round_index}-${Date.now()}`;

    setIsSubmitting(true);
    setCurrentProcessStepId('submit');
    activeProcessRunIdRef.current = processRunId;
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
    setProcessRuns((current) => [
      ...current,
      {
        id: processRunId,
        questionKey: submittedQuestion.question_key,
        roundIndex: submittedQuestion.round_index,
        status: 'running',
        collapsed: false,
        steps: [
          {
            id: 'submit',
            label: '提交答案',
            detail: '答案已发送到服务端，正在进入本轮处理流程。',
            status: 'completed',
            timestamp: Date.now()
          }
        ]
      }
    ]);
    setAnswerText('');

    try {
      await submitInterviewAnswerStream(
        sessionId,
        {
          answer_text: submittedAnswer,
          question_key: submittedQuestion.question_key,
          answer_metadata: {
            process_run_id: processRunId
          }
        },
        {
          onEvent: (event) => {
            handleStreamEvent(event);
          },
          onError: (error) => {
            const message = error instanceof Error ? error.message : '流式请求失败';
            setStreamError(message);
            failActiveProcessRun(message);
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
      const message = error instanceof Error ? error.message : '提交失败，请稍后重试';
      setStreamError(message);
      failActiveProcessRun(message);
    } finally {
      setIsSubmitting(false);
    }
  }

  function handleStreamEvent(event: InterviewStreamEvent) {
    if (event.type === 'status') {
      applyStatusStageToProcess(event, updateActiveProcessStep, setCurrentProcessStepId);
      setStreamLog((current) => [...current, formatStatusLog(event)]);
      return;
    }

    if (event.type === 'plan') {
      updateActiveProcessStep({
        stepId: 'replan',
        label: '判断下一步动作',
        detail: `推进决策：${event.action ?? 'unknown'}${event.reason ? ` · ${event.reason}` : ''}`,
        status: 'completed'
      });
      setCurrentProcessStepId('llm');
      setStreamLog((current) => [
        ...current,
        `推进决策：${event.action ?? 'unknown'}${event.reason ? ` · ${event.reason}` : ''}`
      ]);
      return;
    }

    if (event.type === 'content') {
      const contentEvent = event as InterviewContentEvent;
      updateActiveProcessStep({
        stepId: 'llm',
        label: '生成下一题 / 结束语',
        detail: '面试官已生成下一步内容，正在保存本轮结果。',
        status: 'completed'
      });
      setCurrentProcessStepId('persist');
      setPendingStreamContent(contentEvent.content);
      setClosingMessage('');
      setStreamLog((current) => [...current, '本轮回答已处理，正在生成下一条提问。']);
      return;
    }

    if (event.type === 'step_complete') {
      const stepEvent = event as InterviewStepCompleteEvent;
      updateActiveProcessStep({
        stepId: 'persist',
        label: '保存本轮结果',
        detail: stepEvent.response.message,
        status: 'completed'
      });
      setCurrentProcessStepId(stepEvent.response.completed ? 'report' : 'done');
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
      updateActiveProcessStep({
        stepId: 'report',
        label: '生成统一评估报告',
        detail: '统一评估报告已生成，正在刷新最终结果。',
        status: 'completed'
      });
      setCurrentProcessStepId('done');
      setStreamLog((current) => [...current, '统一评估报告已生成，正在刷新最终结果。']);
      void queryClient.invalidateQueries({ queryKey: ['interview', 'report', sessionId] });
      void queryClient.invalidateQueries({ queryKey: ['interview', 'report-export', sessionId] });
      return;
    }

    if (event.type === 'done') {
      completeActiveProcessRun();
      setStreamLog((current) => [...current, '当前轮流式流程已结束。']);
      return;
    }

    if (event.type === 'error') {
      const errorEvent = event as InterviewErrorEvent;
      const message = `${errorEvent.message} (code: ${errorEvent.code})`;
      setStreamError(message);
      failActiveProcessRun(message);
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
        closingMessage,
        false
      ),
    [session?.answers, currentQuestion, pendingNextQuestion, streamingQuestionText, optimisticAnswer, closingMessage, isSubmitting, pendingStreamContent]
  );
  const submitButtonLabel = getSubmitButtonLabel(isSubmitting, currentProcessStepId);

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
                  renderChatTimeline(chatMessages, processRuns, toggleProcessRunCollapsed)
                )}
              </div>

              {streamLog.length > 0 ? (
                <details className={styles.logDetails}>
                  <summary>查看技术日志</summary>
                  <div className={styles.logList}>
                    {streamLog.map((item, index) => (
                      <div key={`${item}-${index}`} className={styles.logItem}>
                        {item}
                      </div>
                    ))}
                  </div>
                </details>
              ) : null}

              {streamError ? <FeedbackState tone="error" title="流式过程异常" message={streamError} /> : null}

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
                      {submitButtonLabel}
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
                  message="问答流程已经结束，可以进入独立页面查看统一评估细则。"
                  action={
                    <Link className={styles.primaryButton} to={`/interview/${sessionId}/report`}>
                      查看统一评估报告
                    </Link>
                  }
                />
              )}
            </div>
          ) : null}
        </SectionCard>

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

function renderChatTimeline(
  messages: ChatMessage[],
  processRuns: ProcessRun[],
  onToggleProcessRun: (runId: string) => void
) {
  const renderedRunIds = new Set<string>();
  const nodes = messages.map((message) => {
    const matchedRuns =
      message.role === 'user'
        ? processRuns.filter(
            (run) =>
              !renderedRunIds.has(run.id) &&
              run.questionKey === message.questionKey &&
              run.roundIndex === message.roundIndex
          )
        : [];
    matchedRuns.forEach((run) => renderedRunIds.add(run.id));

    return (
      <div key={`row-${message.id}`} className={styles.chatTimelineRow}>
        <ChatBubble message={message} />
        {matchedRuns.map((run) => (
          <AssistantProcessBubble key={run.id} run={run} onToggle={() => onToggleProcessRun(run.id)} />
        ))}
      </div>
    );
  });

  const danglingRuns = processRuns.filter((run) => !renderedRunIds.has(run.id));
  if (danglingRuns.length > 0) {
    nodes.push(
      <div key="dangling-process-runs" className={styles.chatTimelineRow}>
        {danglingRuns.map((run) => (
          <AssistantProcessBubble key={run.id} run={run} onToggle={() => onToggleProcessRun(run.id)} />
        ))}
      </div>
    );
  }
  return nodes;
}

function AssistantProcessBubble({ run, onToggle }: { run: ProcessRun; onToggle: () => void }) {
  const kickerText =
    run.status === 'completed' ? '面试官处理完成' : run.status === 'failed' ? '面试官处理异常' : '面试官正在处理';
  const bubbleClass = [
    styles.chatBubble,
    styles.chatBubbleAssistant,
    styles.processBubble,
    styles[`processBubble_${run.status}`],
    run.collapsed ? styles.processBubbleCollapsed : ''
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <article className={bubbleClass}>
      <div className={styles.processHeader}>
        <div>
          <span className={styles.processKicker}>{kickerText}</span>
          <strong>运行过程</strong>
          {run.collapsed ? <span className={styles.processCollapsedHint}>已生成下方问题，可展开回顾处理细节。</span> : null}
        </div>
        <button className={styles.processToggle} type="button" onClick={onToggle}>
          {run.collapsed ? '展开运行过程' : '收起运行过程'}
        </button>
      </div>

      {!run.collapsed ? (
        <div className={styles.processSteps}>
          {run.steps.map((step) => (
            <div key={step.id} className={`${styles.processStep} ${styles[`processStep_${step.status}`]}`}>
              <span className={styles.processMarker} aria-hidden="true">
                {step.status === 'active' ? <span className={styles.processSpinner} /> : getProcessStepMarker(step.status)}
              </span>
              <div className={styles.processStepBody}>
                <span className={styles.processStepLabel}>{step.label}</span>
                {step.detail ? <span className={styles.processStepDetail}>{step.detail}</span> : null}
                {step.toolSummary ? <ProcessToolSummaryView tool={step.toolSummary} /> : null}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </article>
  );
}

function ProcessToolSummaryView({ tool }: { tool: ProcessToolSummary }) {
  const matchedItems = [
    ...(tool.matched_files ?? []),
    ...(tool.matched_projects ?? []),
    ...(tool.matched_skills ?? [])
  ].filter(Boolean);

  return (
    <div className={styles.processToolDetail}>
      <span>
        工具：{tool.display_name ?? tool.name ?? '证据工具'}
        {tool.source ? `，来源：${tool.source}` : ''}
      </span>
      {matchedItems.length > 0 ? (
        <div className={styles.processToolMatches}>
          {matchedItems.slice(0, 4).map((item) => (
            <span key={item}>{item}</span>
          ))}
        </div>
      ) : null}
      {tool.retrieval_reason ? <small>检索说明：{tool.retrieval_reason}</small> : null}
    </div>
  );
}

function getProcessStepMarker(status: ProcessStepStatus) {
  if (status === 'completed') {
    return 'OK';
  }
  if (status === 'skipped') {
    return 'skip';
  }
  if (status === 'failed') {
    return '!';
  }
  return '';
}

function buildProcessRunsFromAnswers(answers: InterviewAnswerHistoryDto[]): ProcessRun[] {
  return answers
    .map((answer) => normalizePersistedProcessRun(answer.answer_metadata?.process_run))
    .filter((run): run is ProcessRun => Boolean(run));
}

function normalizePersistedProcessRun(rawRun: unknown): ProcessRun | null {
  if (!isRecord(rawRun)) {
    return null;
  }

  const id = typeof rawRun.id === 'string' && rawRun.id.trim() ? rawRun.id : '';
  const roundIndex = typeof rawRun.round_index === 'number' ? rawRun.round_index : Number(rawRun.round_index);
  const status = normalizeProcessRunStatus(rawRun.status);
  const rawSteps = Array.isArray(rawRun.steps) ? rawRun.steps : [];
  const steps = rawSteps
    .map((step) => normalizePersistedProcessStep(step))
    .filter((step): step is ProcessStep => Boolean(step));

  if (!id || !Number.isFinite(roundIndex) || steps.length === 0) {
    return null;
  }

  return {
    id,
    questionKey: typeof rawRun.question_key === 'string' ? rawRun.question_key : null,
    roundIndex,
    status,
    collapsed: status === 'completed',
    steps
  };
}

function normalizePersistedProcessStep(rawStep: unknown): ProcessStep | null {
  if (!isRecord(rawStep) || !isProcessStepId(rawStep.id)) {
    return null;
  }

  const status = normalizeProcessStepStatus(rawStep.status);
  const timestamp = typeof rawStep.timestamp === 'number' ? rawStep.timestamp : Number(rawStep.timestamp);
  return {
    id: rawStep.id,
    label: typeof rawStep.label === 'string' && rawStep.label.trim() ? rawStep.label : getDefaultProcessStepLabel(rawStep.id),
    detail: typeof rawStep.detail === 'string' ? rawStep.detail : undefined,
    status,
    toolSummary: normalizeProcessToolSummary(rawStep.tool_summary),
    timestamp: Number.isFinite(timestamp) ? timestamp : Date.now()
  };
}

function mergeProcessRuns(currentRuns: ProcessRun[], persistedRuns: ProcessRun[]) {
  const nextRuns = [...currentRuns];
  for (const persistedRun of persistedRuns) {
    const existingIndex = nextRuns.findIndex(
      (run) =>
        run.id === persistedRun.id ||
        (run.questionKey === persistedRun.questionKey && run.roundIndex === persistedRun.roundIndex)
    );

    if (existingIndex < 0) {
      nextRuns.push(persistedRun);
      continue;
    }

    const existingRun = nextRuns[existingIndex];
    if (existingRun.status === 'running' && persistedRun.status !== 'completed' && persistedRun.status !== 'failed') {
      continue;
    }

    nextRuns[existingIndex] = {
      ...persistedRun,
      collapsed: existingRun.status === 'running' ? persistedRun.status === 'completed' : existingRun.collapsed
    };
  }
  return nextRuns;
}

function normalizeProcessRunStatus(status: unknown): ProcessRun['status'] {
  if (status === 'completed' || status === 'failed' || status === 'running') {
    return status;
  }
  return 'completed';
}

function normalizeProcessStepStatus(status: unknown): ProcessStepStatus {
  if (status === 'pending' || status === 'active' || status === 'completed' || status === 'skipped' || status === 'failed') {
    return status;
  }
  return 'completed';
}

function normalizeProcessToolSummary(rawTool: unknown): ProcessToolSummary | undefined {
  if (!isRecord(rawTool)) {
    return undefined;
  }

  return {
    name: typeof rawTool.name === 'string' ? rawTool.name : undefined,
    display_name: typeof rawTool.display_name === 'string' ? rawTool.display_name : undefined,
    source: typeof rawTool.source === 'string' ? rawTool.source : undefined,
    summary: typeof rawTool.summary === 'string' ? rawTool.summary : undefined,
    matched_files: normalizeStringList(rawTool.matched_files),
    matched_projects: normalizeStringList(rawTool.matched_projects),
    matched_skills: normalizeStringList(rawTool.matched_skills),
    retrieval_reason: typeof rawTool.retrieval_reason === 'string' ? rawTool.retrieval_reason : null
  };
}

function normalizeStringList(value: unknown) {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isProcessStepId(value: unknown): value is ProcessStepId {
  return (
    value === 'submit' ||
    value === 'observation' ||
    value === 'replan' ||
    value === 'tool_prepare' ||
    value === 'tool_call' ||
    value === 'llm' ||
    value === 'persist' ||
    value === 'report' ||
    value === 'done'
  );
}

function getDefaultProcessStepLabel(stepId: ProcessStepId) {
  const labels: Record<ProcessStepId, string> = {
    submit: '提交答案',
    observation: '评估回答完整性',
    replan: '判断下一步动作',
    tool_prepare: '准备证据工具',
    tool_call: '调用证据工具',
    llm: '生成下一题 / 结束语',
    persist: '保存本轮结果',
    report: '生成统一评估报告',
    done: '本轮完成'
  };
  return labels[stepId];
}

function buildChatMessages(
  answers: InterviewAnswerHistoryDto[],
  currentQuestion: InterviewQuestionSnapshot | null,
  pendingNextQuestion: InterviewQuestionSnapshot | null,
  streamingQuestionText: string,
  optimisticAnswer: OptimisticAnswer | null,
  closingMessage: string,
  waitingForAssistant: boolean
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

  if (waitingForAssistant && optimisticAnswer) {
    messages.push({
      id: `assistant-thinking-${optimisticAnswer.questionKey ?? optimisticAnswer.roundIndex}`,
      role: 'assistant',
      status: 'streaming',
      questionKey: null,
      roundIndex: optimisticAnswer.roundIndex,
      content: '面试官正在分析你的回答，准备下一步问题...'
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

function applyStatusStageToProcess(
  event: InterviewStatusEvent,
  updateStep: (step: {
    stepId: ProcessStepId;
    label: string;
    detail?: string;
    status: ProcessStepStatus;
    toolSummary?: ProcessToolSummary;
  }) => void,
  setCurrentStepId: (stepId: ProcessStepId) => void
) {
  switch (event.stage) {
    case 'session_loaded':
    case 'answer_observation_start':
      updateStep({
        stepId: 'observation',
        label: event.label ?? '评估回答完整性',
        detail: event.detail ?? '正在判断当前回答是否覆盖本题关键观察点。',
        status: 'active'
      });
      setCurrentStepId('observation');
      return;
    case 'answer_observation_complete':
      updateStep({
        stepId: 'observation',
        label: event.label ?? '评估回答完整性',
        detail: event.detail ?? '回答完整性评估已完成。',
        status: 'completed'
      });
      setCurrentStepId('replan');
      return;
    case 'replan_start':
      updateStep({
        stepId: 'replan',
        label: event.label ?? '判断下一步动作',
        detail: event.detail ?? '正在判断需要追问、切换主题还是结束面试。',
        status: 'active'
      });
      setCurrentStepId('replan');
      return;
    case 'replan_complete':
      updateStep({
        stepId: 'replan',
        label: event.label ?? '判断下一步动作',
        detail: event.detail ?? '下一步动作判断完成。',
        status: 'completed'
      });
      setCurrentStepId('llm');
      return;
    case 'tool_prepare_start':
      updateStep({
        stepId: 'tool_prepare',
        label: event.label ?? '准备证据工具',
        detail: event.detail ?? '正在判断是否需要结合简历、GitHub 代码证据或其他工具结果。',
        status: 'active'
      });
      setCurrentStepId('tool_prepare');
      return;
    case 'tool_call_start':
      updateStep({
        stepId: 'tool_prepare',
        label: '准备证据工具',
        detail: '证据工具上下文准备完成。',
        status: 'completed'
      });
      updateStep({
        stepId: 'tool_call',
        label: event.label ?? '调用证据工具',
        detail: event.detail ?? '正在调用证据工具补充追问上下文。',
        status: 'active'
      });
      setCurrentStepId('tool_call');
      return;
    case 'tool_call_complete':
      updateStep({
        stepId: 'tool_prepare',
        label: '准备证据工具',
        detail: '证据工具上下文准备完成。',
        status: 'completed'
      });
      updateStep({
        stepId: 'tool_call',
        label: event.label ?? '调用证据工具',
        detail: buildToolStepDetail(event),
        status: 'completed',
        toolSummary: event.tool
      });
      setCurrentStepId('llm');
      return;
    case 'tool_skipped':
      updateStep({
        stepId: 'tool_prepare',
        label: event.label ?? '本轮未调用证据工具',
        detail: event.detail ?? event.message,
        status: 'skipped'
      });
      setCurrentStepId('llm');
      return;
    case 'llm_generation_start':
      updateStep({
        stepId: 'llm',
        label: event.label ?? '生成下一题 / 结束语',
        detail: event.detail ?? '面试官正在组织下一条提问或收尾说明。',
        status: 'active'
      });
      setCurrentStepId('llm');
      return;
    case 'llm_generation_complete':
      updateStep({
        stepId: 'llm',
        label: event.label ?? '生成下一题 / 结束语',
        detail: event.detail ?? '下一步内容已生成。',
        status: 'completed'
      });
      setCurrentStepId('persist');
      return;
    case 'persist_start':
      updateStep({
        stepId: 'persist',
        label: event.label ?? '保存本轮结果',
        detail: event.detail ?? '正在保存答案、题目状态和流程结果。',
        status: 'active'
      });
      setCurrentStepId('persist');
      return;
    case 'persist_complete':
      updateStep({
        stepId: 'persist',
        label: event.label ?? '保存本轮结果',
        detail: event.detail ?? '本轮答案和面试状态已保存。',
        status: 'completed'
      });
      setCurrentStepId('done');
      return;
    case 'report_start':
      updateStep({
        stepId: 'report',
        label: event.label ?? '生成统一评估报告',
        detail: event.detail ?? '面试已结束，正在生成或刷新统一评估报告。',
        status: 'active'
      });
      setCurrentStepId('report');
      return;
    case 'report_complete':
      updateStep({
        stepId: 'report',
        label: event.label ?? '生成统一评估报告',
        detail: event.detail ?? '统一评估报告已生成。',
        status: 'completed'
      });
      setCurrentStepId('done');
      return;
    default:
      if (event.message.includes('评估报告')) {
        updateStep({
          stepId: 'report',
          label: event.label ?? '生成统一评估报告',
          detail: event.detail ?? event.message,
          status: 'active'
        });
        setCurrentStepId('report');
      } else {
        updateStep({
          stepId: 'observation',
          label: event.label ?? '评估回答完整性',
          detail: event.detail ?? event.message,
          status: 'active'
        });
        setCurrentStepId('observation');
      }
  }
}

function formatStatusLog(event: InterviewStatusEvent) {
  if (event.stage || event.label || event.detail) {
    return `状态：${event.label ?? event.message}${event.stage ? ` [${event.stage}]` : ''}${
      event.detail ? ` · ${event.detail}` : ''
    }`;
  }
  return `状态：${event.message}`;
}

function buildToolStepDetail(event: InterviewStatusEvent) {
  const tool = event.tool;
  const matchedItems = [
    ...(tool?.matched_files ?? []),
    ...(tool?.matched_projects ?? []),
    ...(tool?.matched_skills ?? [])
  ].filter(Boolean);
  const toolName = tool?.display_name ?? tool?.name ?? '证据工具';
  const sourceText = tool?.source ? `，从 ${tool.source} 获取相关信息` : '';
  const matchedText = matchedItems.length > 0 ? `，命中：${matchedItems.slice(0, 3).join('、')}` : '';
  return tool?.summary || event.detail || `已调用${toolName}${sourceText}${matchedText}。`;
}

function getSubmitButtonLabel(isSubmitting: boolean, currentStepId: string | null) {
  if (!isSubmitting) {
    return '提交答案';
  }

  if (currentStepId === 'observation' || currentStepId === 'replan') {
    return '分析回答中...';
  }
  if (currentStepId === 'tool_prepare') {
    return '准备工具中...';
  }
  if (currentStepId === 'tool_call') {
    return '检索证据中...';
  }
  if (currentStepId === 'llm') {
    return '生成下一题中...';
  }
  if (currentStepId === 'report') {
    return '生成报告中...';
  }
  if (currentStepId === 'persist' || currentStepId === 'done') {
    return '保存结果中...';
  }
  return '提交中...';
}
