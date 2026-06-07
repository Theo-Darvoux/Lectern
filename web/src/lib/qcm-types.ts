// QCM (Multiple Choice Questions) type definitions

export interface QCMFile {
  version: 1;
  chapters: QCMChapter[];
  /**
   * Self-contained image store, keyed by a short id. Values are `data:` URLs.
   * Question / answer / explanation markdown references an image with
   * `![alt](qcmimg:<id>)`. Keeping images here (rather than inline in the
   * char-limited text fields) keeps the QCM portable for PDF / Moodle export.
   */
  images?: Record<string, string>;
}

export interface QCMChapter {
  id: string;
  title: string;
  questions: QCMQuestion[];
}

export interface QCMQuestion {
  id: string;
  text: string; // Markdown + LaTeX
  answers: QCMAnswer[];
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

// Image limits (must stay in sync with the API: QCM_MAX_IMAGES / QCM_MAX_IMAGE_CHARS)
export const MAX_IMAGES_PER_QCM = 30;
/** Longest side (px) an embedded image is downscaled to before encoding. */
export const QCM_IMAGE_MAX_DIMENSION = 1024;
/** Hard cap on a single image's encoded `data:` URL length (~365 KB). */
export const QCM_IMAGE_MAX_CHARS = 500_000;
/** Marker prefix used in markdown image refs: `![alt](qcmimg:<id>)`. */
export const QCM_IMAGE_REF_PREFIX = "qcmimg:";

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
