// QCM (Multiple Choice Questions) type definitions

export interface QCMFile {
  version: 1;
  chapters: QCMChapter[];
}

export interface QCMChapter {
  id: string;
  title: string;
  questions: QCMQuestion[];
}

export interface QCMQuestion {
  id: string;
  text: string; // Markdown + LaTeX
  answers: QCMAnswer[]; // 1–4 answers
  explanation?: string; // optional Markdown + LaTeX
}

export interface QCMAnswer {
  id: string;
  text: string; // Markdown + LaTeX
  correct: boolean;
}

export const MAX_ANSWERS_PER_QUESTION = 10;
export const MAX_QUESTIONS_PER_QCM = 100;
export const MAX_CHAPTERS_PER_QCM = 20;

// Text field length limits
export const MAX_LEN_TITLE = 128; // must match CreateMaterialOp.title max_length in the API
export const MAX_LEN_DESCRIPTION = 500;
export const MAX_LEN_CHAPTER_TITLE = 100;
export const MAX_LEN_QUESTION = 2000;
export const MAX_LEN_ANSWER = 500;
export const MAX_LEN_EXPLANATION = 3000;

export interface QCMMeta {
  title: string;
  type: string;
  description?: string;
  tags?: string[];
}
