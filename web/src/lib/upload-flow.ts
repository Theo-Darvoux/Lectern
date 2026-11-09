export interface UploadFlowInput {
  fileCount: number;
  unsettledCount: number;
}

export interface UploadFlowState {
  step: 1 | 2 | 3;
}

/** User-facing upload phase, independent from transport implementation details. */
export function getUploadFlowState({
  fileCount,
  unsettledCount,
}: UploadFlowInput): UploadFlowState {
  if (fileCount === 0) return { step: 1 };
  if (unsettledCount > 0) return { step: 2 };
  return { step: 3 };
}
