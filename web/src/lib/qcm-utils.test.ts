import { describe, it, expect } from "vitest";
import {
  createEmptyAnswer,
  createEmptyChapter,
  createEmptyQuestion,
  countQCMQuestions,
  validateQCMFile,
  MAX_ANSWERS_PER_QUESTION,
  MAX_QUESTIONS_PER_QCM,
} from "./qcm-utils";
import type { QCMFile } from "./qcm-types";

// ── helpers ───────────────────────────────────────────────────────────────────

function minimalQCM(): QCMFile {
  return {
    version: 1,
    chapters: [
      {
        id: "ch1",
        title: "Chapter 1",
        questions: [
          {
            id: "q1",
            text: "What is 2+2?",
            answers: [
              { id: "a1", text: "4", correct: true },
              { id: "a2", text: "5", correct: false },
            ],
          },
        ],
      },
    ],
  };
}

// ── createEmptyChapter ────────────────────────────────────────────────────────

describe("createEmptyChapter", () => {
  it("returns a chapter with a unique id", () => {
    const ch1 = createEmptyChapter();
    const ch2 = createEmptyChapter();
    expect(ch1.id).not.toBe(ch2.id);
    expect(ch1.id.length).toBeGreaterThan(0);
  });

  it("sets provided title", () => {
    const ch = createEmptyChapter("My Chapter");
    expect(ch.title).toBe("My Chapter");
  });

  it("defaults to empty title", () => {
    const ch = createEmptyChapter();
    expect(ch.title).toBe("");
  });

  it("starts with empty questions array", () => {
    const ch = createEmptyChapter();
    expect(ch.questions).toEqual([]);
  });
});

// ── createEmptyQuestion ───────────────────────────────────────────────────────

describe("createEmptyQuestion", () => {
  it("returns a question with a unique id", () => {
    const q1 = createEmptyQuestion();
    const q2 = createEmptyQuestion();
    expect(q1.id).not.toBe(q2.id);
  });

  it("starts with empty text", () => {
    expect(createEmptyQuestion().text).toBe("");
  });

  it("starts with two empty answers", () => {
    const q = createEmptyQuestion();
    expect(q.answers).toHaveLength(2);
  });

  it("answer ids are unique", () => {
    const q = createEmptyQuestion();
    expect(q.answers[0].id).not.toBe(q.answers[1].id);
  });
});

// ── createEmptyAnswer ─────────────────────────────────────────────────────────

describe("createEmptyAnswer", () => {
  it("returns an answer with a unique id", () => {
    const a1 = createEmptyAnswer();
    const a2 = createEmptyAnswer();
    expect(a1.id).not.toBe(a2.id);
  });

  it("defaults correct to false", () => {
    expect(createEmptyAnswer().correct).toBe(false);
  });

  it("defaults text to empty string", () => {
    expect(createEmptyAnswer().text).toBe("");
  });
});

// ── countQCMQuestions ─────────────────────────────────────────────────────────

describe("countQCMQuestions", () => {
  it("counts questions across a single chapter", () => {
    expect(countQCMQuestions(minimalQCM())).toBe(1);
  });

  it("counts questions across multiple chapters", () => {
    const qcm = minimalQCM();
    qcm.chapters.push({
      id: "ch2",
      title: "Chapter 2",
      questions: [
        { id: "q2", text: "Q2?", answers: [{ id: "a3", text: "ans", correct: true }] },
        { id: "q3", text: "Q3?", answers: [{ id: "a4", text: "ans", correct: false }] },
      ],
    });
    expect(countQCMQuestions(qcm)).toBe(3);
  });

  it("returns 0 for empty chapters", () => {
    const qcm: QCMFile = { version: 1, chapters: [] };
    expect(countQCMQuestions(qcm)).toBe(0);
  });

  it("returns 0 for chapter with no questions", () => {
    const qcm: QCMFile = {
      version: 1,
      chapters: [{ id: "ch1", title: "Empty", questions: [] }],
    };
    expect(countQCMQuestions(qcm)).toBe(0);
  });
});

// ── validateQCMFile ───────────────────────────────────────────────────────────

describe("validateQCMFile", () => {
  it("accepts a valid minimal QCM", () => {
    expect(validateQCMFile(minimalQCM())).toBe(true);
  });

  it("rejects null", () => {
    expect(validateQCMFile(null)).toBe(false);
  });

  it("rejects non-object", () => {
    expect(validateQCMFile("string")).toBe(false);
    expect(validateQCMFile(42)).toBe(false);
    expect(validateQCMFile([])).toBe(false);
  });

  it("rejects wrong version", () => {
    expect(validateQCMFile({ ...minimalQCM(), version: 2 })).toBe(false);
    expect(validateQCMFile({ ...minimalQCM(), version: "1" })).toBe(false);
  });

  it("rejects missing chapters", () => {
    const { chapters: _, ...rest } = minimalQCM();
    expect(validateQCMFile(rest)).toBe(false);
  });

  it("rejects non-array chapters", () => {
    expect(validateQCMFile({ version: 1, chapters: "bad" })).toBe(false);
  });

  it("rejects chapter without id", () => {
    const qcm = minimalQCM();
    // @ts-expect-error intentional
    delete qcm.chapters[0].id;
    expect(validateQCMFile(qcm)).toBe(false);
  });

  it("rejects chapter with empty id", () => {
    const qcm = minimalQCM();
    qcm.chapters[0].id = "";
    expect(validateQCMFile(qcm)).toBe(false);
  });

  it("rejects question without id", () => {
    const qcm = minimalQCM();
    // @ts-expect-error intentional
    delete qcm.chapters[0].questions[0].id;
    expect(validateQCMFile(qcm)).toBe(false);
  });

  it("rejects answer with non-boolean correct field", () => {
    const qcm = minimalQCM();
    // @ts-expect-error intentional
    qcm.chapters[0].questions[0].answers[0].correct = 1;
    expect(validateQCMFile(qcm)).toBe(false);
  });

  it("rejects zero answers", () => {
    const qcm = minimalQCM();
    qcm.chapters[0].questions[0].answers = [];
    expect(validateQCMFile(qcm)).toBe(false);
  });

  it("rejects more than MAX_ANSWERS_PER_QUESTION answers", () => {
    const qcm = minimalQCM();
    qcm.chapters[0].questions[0].answers = Array.from(
      { length: MAX_ANSWERS_PER_QUESTION + 1 },
      (_, i) => ({ id: `a${i}`, text: `A${i}`, correct: false }),
    );
    expect(validateQCMFile(qcm)).toBe(false);
  });

  it("accepts exactly MAX_ANSWERS_PER_QUESTION answers", () => {
    const qcm = minimalQCM();
    qcm.chapters[0].questions[0].answers = Array.from(
      { length: MAX_ANSWERS_PER_QUESTION },
      (_, i) => ({ id: `a${i}`, text: `A${i}`, correct: i === 0 }),
    );
    expect(validateQCMFile(qcm)).toBe(true);
  });

  it("rejects when total questions exceeds MAX_QUESTIONS_PER_QCM", () => {
    const questions = Array.from({ length: MAX_QUESTIONS_PER_QCM + 1 }, (_, i) => ({
      id: `q${i}`,
      text: `Q${i}`,
      answers: [{ id: `a${i}`, text: "A", correct: true }],
    }));
    const qcm: QCMFile = {
      version: 1,
      chapters: [{ id: "ch1", title: "Big", questions }],
    };
    expect(validateQCMFile(qcm)).toBe(false);
  });

  it("accepts exactly MAX_QUESTIONS_PER_QCM questions", () => {
    const questions = Array.from({ length: MAX_QUESTIONS_PER_QCM }, (_, i) => ({
      id: `q${i}`,
      text: `Q${i}`,
      answers: [{ id: `a${i}`, text: "A", correct: true }],
    }));
    const qcm: QCMFile = {
      version: 1,
      chapters: [{ id: "ch1", title: "Big", questions }],
    };
    expect(validateQCMFile(qcm)).toBe(true);
  });

  it("accepts optional explanation field on questions", () => {
    const qcm = minimalQCM();
    qcm.chapters[0].questions[0].explanation = "Because math.";
    expect(validateQCMFile(qcm)).toBe(true);
  });
});
