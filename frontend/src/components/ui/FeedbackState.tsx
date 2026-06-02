import styles from './FeedbackState.module.css';

type FeedbackStateProps = {
  title: string;
  message: string;
  tone?: 'neutral' | 'error' | 'success';
  action?: React.ReactNode;
};

export function FeedbackState({
  title,
  message,
  tone = 'neutral',
  action
}: FeedbackStateProps) {
  const toneClass =
    tone === 'error'
      ? styles.error
      : tone === 'success'
        ? styles.success
        : styles.neutral;

  return (
    <div className={`${styles.state} ${toneClass}`}>
      <h3 className={styles.title}>{title}</h3>
      <p className={styles.message}>{message}</p>
      {action ? <div className={styles.action}>{action}</div> : null}
    </div>
  );
}
