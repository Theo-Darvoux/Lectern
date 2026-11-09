import { describe, expect, it } from "vitest";

import { canStageSuccessfulUploads, isInterruptedQueueMatch } from "./use-upload-engine";
import type { QueueItem } from "@/lib/upload-queue";

describe("canStageSuccessfulUploads", () => {
  it("allows clean files to be staged after failed siblings finish", () => {
    expect(canStageSuccessfulUploads(3, 0)).toBe(true);
  });

  it("waits until all in-flight siblings reach a terminal state", () => {
    expect(canStageSuccessfulUploads(3, 1)).toBe(false);
  });
});

describe("isInterruptedQueueMatch", () => {
  const interrupted: QueueItem = {
    clientId: "client-1",
    uploadId: "upload-1",
    fileName: "notes.pdf",
    fileSize: 123,
    fileMimeType: "application/pdf",
    title: "notes",
    status: "error",
    progress: 0,
    processingStatus: "",
    targetDirPath: "course/week-1",
    referenceLost: true,
  };

  it("reattaches a matching file after browser references were lost", () => {
    const file = new File([new Uint8Array(123)], "notes.pdf", { type: "application/pdf" });
    expect(isInterruptedQueueMatch(
      interrupted,
      { file, relativePath: "course/week-1/notes.pdf" },
    )).toBe(true);
  });

  it("does not confuse a same-named file with a different size", () => {
    const file = new File([new Uint8Array(124)], "notes.pdf", { type: "application/pdf" });
    expect(isInterruptedQueueMatch(
      interrupted,
      { file, relativePath: "course/week-1/notes.pdf" },
    )).toBe(false);
  });

  it("does not confuse a same-sized file saved at another time", () => {
    const file = new File([new Uint8Array(123)], "notes.pdf", { lastModified: 2_000 });
    expect(isInterruptedQueueMatch(
      { ...interrupted, fileLastModified: 1_000 },
      { file, relativePath: "course/week-1/notes.pdf" },
    )).toBe(false);
  });

  it("ignores rows that still hold their file", () => {
    const file = new File([new Uint8Array(123)], "notes.pdf");
    expect(isInterruptedQueueMatch(
      { ...interrupted, referenceLost: false },
      { file, relativePath: "course/week-1/notes.pdf" },
    )).toBe(false);
  });
});
