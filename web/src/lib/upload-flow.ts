export interface UploadFlowInput {
  fileCount: number;
  unsettledCount: number;
  errorCount: number;
  readyCount: number;
}

export interface UploadFlowState {
  step: 1 | 2 | 3;
  canAddToDraft: boolean;
}

/** User-facing upload phase, independent from transport implementation details. */
export function getUploadFlowState({
  fileCount,
  unsettledCount,
  errorCount,
  readyCount,
}: UploadFlowInput): UploadFlowState {
  if (fileCount === 0) return { step: 1, canAddToDraft: false };
  if (unsettledCount > 0) return { step: 2, canAddToDraft: false };
  return {
    step: 3,
    canAddToDraft: readyCount > 0 && errorCount === 0,
  };
}
