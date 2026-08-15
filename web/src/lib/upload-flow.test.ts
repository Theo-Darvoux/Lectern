import { describe, expect, it } from "vitest";

import { getUploadFlowState } from "./upload-flow";

describe("getUploadFlowState", () => {
  it("starts with file selection when the queue is empty", () => {
    expect(getUploadFlowState({ fileCount: 0, unsettledCount: 0, errorCount: 0, readyCount: 0 })).toEqual({
      step: 1,
      canAddToDraft: false,
    });
  });

  it("moves to processing while uploads or checks are active", () => {
    expect(getUploadFlowState({ fileCount: 3, unsettledCount: 2, errorCount: 0, readyCount: 1 })).toEqual({
      step: 2,
      canAddToDraft: false,
    });
  });

  it("requires failed files to be resolved before adding the batch", () => {
    expect(getUploadFlowState({ fileCount: 3, unsettledCount: 0, errorCount: 1, readyCount: 2 })).toEqual({
      step: 3,
      canAddToDraft: false,
    });
  });

  it("allows a settled, successful batch to be added to the draft", () => {
    expect(getUploadFlowState({ fileCount: 3, unsettledCount: 0, errorCount: 0, readyCount: 3 })).toEqual({
      step: 3,
      canAddToDraft: true,
    });
  });
});
