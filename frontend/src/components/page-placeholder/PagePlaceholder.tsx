import type { ReactNode } from 'react';

import styles from './PagePlaceholder.module.css';

type PagePlaceholderProps = {
  eyebrow: string;
  title: string;
  description: string;
  pathLabel: string;
  children?: ReactNode;
};

export function PagePlaceholder({
  eyebrow,
  title,
  description,
  pathLabel,
  children
}: PagePlaceholderProps) {
  return (
    <section className={styles.panel}>
      <div className={styles.header}>
        <span className={styles.eyebrow}>{eyebrow}</span>
        <code className={styles.path}>{pathLabel}</code>
      </div>
      <h2 className={styles.title}>{title}</h2>
      <p className={styles.description}>{description}</p>
      {children ? <div className={styles.body}>{children}</div> : null}
    </section>
  );
}
