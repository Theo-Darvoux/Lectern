"use client";

import { useState, useEffect } from "react";

export type ViewMode = "list" | "grid";

/** Grid or list layout, remembered per screen in localStorage. */
export function useViewMode(storageKey = "browse-view-mode") {
  const [mode, setModeState] = useState<ViewMode>("grid");

  useEffect(() => {
    try {
      const stored = localStorage.getItem(storageKey);
      if (stored === "grid" || stored === "list") setModeState(stored);
    } catch {}
  }, [storageKey]);

  const setMode = (m: ViewMode) => {
    try {
      localStorage.setItem(storageKey, m);
    } catch {}
    setModeState(m);
  };

  return { mode, setMode };
}
