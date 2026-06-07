import type { QCMFile, QCMChapter, QCMQuestion, QCMAnswer } from "./qcm-types";
import {
  MAX_ANSWERS_PER_QUESTION,
  MAX_QUESTIONS_PER_QCM,
  MAX_IMAGES_PER_QCM,
} from "./qcm-types";
import type { QcmLimits } from "./qcm-limits";

export function generateQCMId(): string {
  return crypto.randomUUID();
}

export function createEmptyChapter(title = ""): QCMChapter {
  return {
    id: generateQCMId(),
    title,
    questions: [],
  };
}

export function createEmptyQuestion(): QCMQuestion {
  return {
    id: generateQCMId(),
    text: "",
    answers: [createEmptyAnswer(), createEmptyAnswer()],
  };
}

export function createEmptyAnswer(): QCMAnswer {
  return {
    id: generateQCMId(),
    text: "",
    correct: false,
  };
}

export function countQCMQuestions(qcm: QCMFile): number {
  return qcm.chapters.reduce((sum, ch) => sum + ch.questions.length, 0);
}

/** Runtime validation — returns true if the unknown value is a valid QCMFile. */
export function validateQCMFile(qcm: unknown, limits?: QcmLimits): qcm is QCMFile {
  const maxAnswers = limits?.max_answers_per_question ?? MAX_ANSWERS_PER_QUESTION;
  const maxQuestions = limits?.max_questions_per_qcm ?? MAX_QUESTIONS_PER_QCM;
  if (typeof qcm !== "object" || qcm === null) return false;
  const obj = qcm as Record<string, unknown>;
  if (obj.version !== 1) return false;
  if (!Array.isArray(obj.chapters)) return false;

  // Optional embedded image store: { [id]: dataUrl }
  if (obj.images !== undefined) {
    if (typeof obj.images !== "object" || obj.images === null || Array.isArray(obj.images))
      return false;
    const entries = Object.entries(obj.images as Record<string, unknown>);
    if (entries.length > MAX_IMAGES_PER_QCM) return false;
    for (const [key, val] of entries) {
      if (!key || typeof val !== "string" || !val.startsWith("data:image/")) return false;
    }
  }

  for (const ch of obj.chapters as unknown[]) {
    if (typeof ch !== "object" || ch === null) return false;
    const chapter = ch as Record<string, unknown>;
    if (typeof chapter.id !== "string" || !chapter.id) return false;
    if (typeof chapter.title !== "string") return false;
    if (!Array.isArray(chapter.questions)) return false;

    for (const q of chapter.questions as unknown[]) {
      if (typeof q !== "object" || q === null) return false;
      const question = q as Record<string, unknown>;
      if (typeof question.id !== "string" || !question.id) return false;
      if (typeof question.text !== "string") return false;
      if (!Array.isArray(question.answers)) return false;
      if (question.answers.length < 1 || question.answers.length > maxAnswers)
        return false;

      for (const a of question.answers as unknown[]) {
        if (typeof a !== "object" || a === null) return false;
        const answer = a as Record<string, unknown>;
        if (typeof answer.id !== "string" || !answer.id) return false;
        if (typeof answer.text !== "string") return false;
        if (typeof answer.correct !== "boolean") return false;
      }
    }
  }

  const totalQuestions = (obj.chapters as QCMChapter[]).reduce(
    (sum: number, ch: QCMChapter) => sum + ch.questions.length,
    0,
  );
  return totalQuestions <= maxQuestions;
}

export { MAX_ANSWERS_PER_QUESTION, MAX_QUESTIONS_PER_QCM };
