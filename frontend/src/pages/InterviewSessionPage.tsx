import { useParams } from 'react-router-dom';

import { InterviewSessionWorkspace } from '../features/interview';
import { FeedbackState } from '../components/ui';

export function InterviewSessionPage() {
  const { sessionId } = useParams<{ sessionId: string }>();

  if (!sessionId) {
    return <FeedbackState tone="error" title="缺少 sessionId" message="当前路由未提供有效的面试会话 ID。" />;
  }

  return <InterviewSessionWorkspace sessionId={sessionId} />;
}
