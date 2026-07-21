import { useEffect, useMemo, useState } from 'react';

import styles from './ProcessTimeline.module.css';

export type ProcessTimelineStep = {
  id: string;
  label: string;
  description?: string;
};

type ProcessTimelineProps = {
  title: string;
  description: string;
  steps: ProcessTimelineStep[];
  currentStepId: string | null;
  completedStepIds?: string[];
  failedStepId?: string | null;
  elapsedFrom?: number | null;
  slowAfterSeconds?: number;
  slowMessage?: string;
  footer?: React.ReactNode;
};

export function ProcessTimeline({
  title,
  description,
  steps,
  currentStepId,
  completedStepIds = [],
  failedStepId = null,
  elapsedFrom = null,
  slowAfterSeconds = 20,
  slowMessage,
  footer
}: ProcessTimelineProps) {
  const elapsedSeconds = useElapsedSeconds(elapsedFrom);
  const completedSet = useMemo(() => new Set(completedStepIds), [completedStepIds]);
  const isSlow = elapsedSeconds !== null && elapsedSeconds >= slowAfterSeconds;

  return (
    <div className={styles.timelineCard}>
      <div className={styles.header}>
        <div>
          <h3>{title}</h3>
          <p>{isSlow && slowMessage ? slowMessage : description}</p>
        </div>
        {elapsedSeconds !== null ? <span className={styles.elapsed}>已等待 {elapsedSeconds}s</span> : null}
      </div>

      <ol className={styles.stepList}>
        {steps.map((step, index) => {
          const status = resolveStepStatus({
            stepId: step.id,
            currentStepId,
            failedStepId,
            completedSet
          });
          return (
            <li key={step.id} className={`${styles.stepItem} ${styles[`status_${status}`]}`}>
              <span className={styles.marker} aria-hidden="true">
                {status === 'completed' ? 'OK' : index + 1}
              </span>
              <div className={styles.stepBody}>
                <strong>{step.label}</strong>
                {step.description ? <span>{step.description}</span> : null}
              </div>
            </li>
          );
        })}
      </ol>

      {footer ? <div className={styles.footer}>{footer}</div> : null}
    </div>
  );
}

function resolveStepStatus({
  stepId,
  currentStepId,
  failedStepId,
  completedSet
}: {
  stepId: string;
  currentStepId: string | null;
  failedStepId: string | null;
  completedSet: Set<string>;
}) {
  if (failedStepId === stepId) {
    return 'failed';
  }
  if (completedSet.has(stepId)) {
    return 'completed';
  }
  if (currentStepId === stepId) {
    return 'active';
  }
  return 'pending';
}

function useElapsedSeconds(startedAt: number | null) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!startedAt) {
      return;
    }

    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  if (!startedAt) {
    return null;
  }

  return Math.max(0, Math.floor((now - startedAt) / 1000));
}
