import { useParams } from 'react-router-dom';

import { FeedbackState } from '../components/ui';
import { InterviewReportWorkspace } from '../features/interview';

export function InterviewReportPage() {
  const { sessionId } = useParams<{ sessionId: string }>();

  if (!sessionId) {
    return <FeedbackState tone="error" title="缺少 sessionId" message="当前路由未提供有效的面试会话 ID。" />;
  }

  return <InterviewReportWorkspace sessionId={sessionId} />;
}
