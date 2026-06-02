export type InterviewQuestionSnapshot = {
  question_key: string;
  round_index: number;
  category_key: string;
  question_text: string;
  parent_question_key: string | null;
  source: string;
  status: string;
  is_follow_up: boolean;
  asked_at: string | null;
  answered_at: string | null;
};

export type CreateInterviewRequest = {
  skill_id: string;
  resume_id?: string | null;
  title?: string | null;
  language?: string | null;
  max_rounds?: number | null;
};

export type SubmitAnswerRequest = {
  answer_text: string;
  question_key?: string | null;
  answer_metadata?: Record<string, unknown>;
};

export type InterviewSessionDto = {
  session_id: string;
  resume_id: string | null;
  skill_id: string;
  skill_display_name: string;
  title: string | null;
  language: string;
  status: string;
  current_round: number;
  max_rounds: number;
  current_question: InterviewQuestionSnapshot | null;
  questions: InterviewQuestionSnapshot[];
  answers: InterviewAnswerHistoryDto[];
  answer_count: number;
  follow_up_count: number;
  completed: boolean;
  report_status: string | null;
  last_draft_answer: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
};

export type InterviewSessionSummaryDto = {
  session_id: string;
  resume_id: string | null;
  skill_id: string;
  skill_display_name: string;
  title: string | null;
  language: string;
  status: string;
  current_round: number;
  max_rounds: number;
  answer_count: number;
  completed: boolean;
  report_status: string | null;
  current_question: InterviewQuestionSnapshot | null;
  started_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
};

export type InterviewAnswerHistoryDto = {
  answer_id: string;
  round_index: number;
  question_key: string | null;
  question_text: string;
  answer_text: string;
  answer_status: string;
  submitted_at: string | null;
  answer_metadata: Record<string, unknown>;
  feedback: Record<string, unknown>;
};

export type SubmitAnswerResponse = {
  session_id: string;
  action: string;
  status: string;
  completed: boolean;
  draft_saved: boolean;
  message: string;
  current_question: InterviewQuestionSnapshot | null;
  feedback: Record<string, unknown>;
  report_status: string | null;
};

export type InterviewQuestionEvaluationDto = {
  question_key: string;
  round_index: number;
  question_text: string;
  answer_text: string;
  score: number;
  rating: string;
  strengths: string[];
  weaknesses: string[];
  suggestions: string[];
  rationale: string;
  source: string;
};

export type InterviewReportDto = {
  session_id: string;
  skill_id: string;
  status: string;
  summary_text: string | null;
  overall_score: number | null;
  overall_rating: string | null;
  strengths: string[];
  weaknesses: string[];
  suggestions: string[];
  dimension_scores: Record<string, number>;
  question_evaluations: InterviewQuestionEvaluationDto[];
  rubric_name: string | null;
  rubric_path: string | null;
  generation_mode: string;
  markdown_content: string;
  error_message: string | null;
  generated_at: string | null;
};

export type InterviewReportExportDto = {
  session_id: string;
  report_status: string;
  export_format: string;
  file_name: string;
  content: string;
  message: string;
};

export type InterviewStatusEvent = {
  type: 'status';
  session_id: string;
  message: string;
};

export type InterviewPlanEvent = {
  type: 'plan';
  session_id: string;
  action: string | null;
  reason: string | null;
};

export type InterviewContentEvent = {
  type: 'content';
  session_id: string;
  content: string;
};

export type InterviewReportEvent = {
  type: 'report';
  session_id: string;
  report: InterviewReportDto | Record<string, unknown>;
};

export type InterviewStepCompleteEvent = {
  type: 'step_complete';
  session_id: string;
  response: SubmitAnswerResponse;
};

export type InterviewDoneEvent = {
  type: 'done';
  session_id: string;
  session: InterviewSessionDto;
};

export type InterviewErrorEvent = {
  type: 'error';
  code: number;
  message: string;
  data: Record<string, unknown>;
};

export type InterviewStreamEvent =
  | InterviewStatusEvent
  | InterviewPlanEvent
  | InterviewContentEvent
  | InterviewReportEvent
  | InterviewStepCompleteEvent
  | InterviewDoneEvent
  | InterviewErrorEvent;
