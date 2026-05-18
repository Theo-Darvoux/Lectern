"use client";

import { useState, useEffect } from "react";

const STORAGE_KEY = "browse-view-mode";

export type ViewMode = "list" | "grid";

export function useViewMode() {
  const [mode, setModeState] = useState<ViewMode>("list");

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === "grid" || stored === "list") setModeState(stored);
    } catch {}
  }, []);

  const setMode = (m: ViewMode) => {
    try {
      localStorage.setItem(STORAGE_KEY, m);
    } catch {}
    setModeState(m);
  };

  return { mode, setMode };
}
