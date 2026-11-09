import { useEffect, useState } from "react";
import { fetchStagedQcm } from "@/lib/qcm-draft";
import type { QCMFile } from "@/lib/qcm-types";

interface LoadedQcm {
  fileKey: string;
  qcm: QCMFile | null;
  error: string | null;
}

/** Load the QCM saved to the draft under `fileKey`, if there is one. */
export function useStagedQcm(fileKey: string | null | undefined) {
  const [loaded, setLoaded] = useState<LoadedQcm | null>(null);

  useEffect(() => {
    if (!fileKey) return;
    let cancelled = false;
    fetchStagedQcm(fileKey)
      .then((qcm) => {
        if (!cancelled) setLoaded({ fileKey, qcm, error: null });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setLoaded({
            fileKey,
            qcm: null,
            error: err instanceof Error ? err.message : "Failed to load QCM",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [fileKey]);

  const current = fileKey && loaded?.fileKey === fileKey ? loaded : null;
  return {
    qcm: current?.qcm ?? null,
    error: current?.error ?? null,
    isLoading: !!fileKey && !current,
  };
}
