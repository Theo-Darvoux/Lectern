import { describe, expect, it } from "vitest";

import { getUploadFlowState } from "./upload-flow";

describe("getUploadFlowState", () => {
  it("starts with file selection when the queue is empty", () => {
    expect(getUploadFlowState({ fileCount: 0, unsettledCount: 0 })).toEqual({ step: 1 });
  });

  it("moves to processing while uploads or checks are active", () => {
    expect(getUploadFlowState({ fileCount: 3, unsettledCount: 2 })).toEqual({ step: 2 });
  });

  it("moves to review once every file has settled", () => {
    expect(getUploadFlowState({ fileCount: 3, unsettledCount: 0 })).toEqual({ step: 3 });
  });
});
