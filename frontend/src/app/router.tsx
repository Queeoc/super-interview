import { createBrowserRouter } from 'react-router-dom';

import { AppShell } from './AppShell';
import { HomePage } from '../pages/HomePage';
import { InterviewPage } from '../pages/InterviewPage';
import { InterviewReportPage } from '../pages/InterviewReportPage';
import { InterviewSessionPage } from '../pages/InterviewSessionPage';
import { ResumePage } from '../pages/ResumePage';
import { KnowledgePage } from '../pages/KnowledgePage';
import { SkillsPage } from '../pages/SkillsPage';

export const appRouter = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      {
        index: true,
        element: <HomePage />
      },
      {
        path: 'interview',
        element: <InterviewPage />
      },
      {
        path: 'interview/:sessionId',
        element: <InterviewSessionPage />
      },
      {
        path: 'interview/:sessionId/report',
        element: <InterviewReportPage />
      },
      {
        path: 'resume',
        element: <ResumePage />
      },
      {
        path: 'knowledge',
        element: <KnowledgePage />
      },
      {
        path: 'skills',
        element: <SkillsPage />
      }
    ]
  }
]);
