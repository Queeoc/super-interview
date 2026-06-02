import { marked } from 'marked';

import styles from './MarkdownRenderer.module.css';

type MarkdownRendererProps = {
  content: string;
  emptyMessage?: string;
};

export function MarkdownRenderer({
  content,
  emptyMessage = '暂无可展示内容'
}: MarkdownRendererProps) {
  const normalizedContent = content.trim();

  if (!normalizedContent) {
    return <div className={styles.empty}>{emptyMessage}</div>;
  }

  return (
    <article
      className={styles.markdown}
      dangerouslySetInnerHTML={{
        __html: marked.parse(normalizedContent, { breaks: true }) as string
      }}
    />
  );
}
