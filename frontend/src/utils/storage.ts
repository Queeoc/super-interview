const RECENT_INTERVIEW_SESSION_KEY = 'super-interview:recent-session-id';

export function getRecentInterviewSessionId(): string | null {
  if (typeof window === 'undefined') {
    return null;
  }

  return window.localStorage.getItem(RECENT_INTERVIEW_SESSION_KEY);
}

export function setRecentInterviewSessionId(sessionId: string): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem(RECENT_INTERVIEW_SESSION_KEY, sessionId);
}
