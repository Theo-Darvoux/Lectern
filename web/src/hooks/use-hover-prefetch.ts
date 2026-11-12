"use client";

import { useCallback, useEffect, useRef } from "react";
import { prefetchBrowsePath } from "@/lib/browse-prefetch";

// Long enough to skip rows the pointer only sweeps across.
const HOVER_PREFETCH_DELAY_MS = 100;

/** The browse path (after /browse/) of an item inside the listing at `pathBase`. */
export function childBrowsePath(pathBase: string, slug: string): string {
  return `${pathBase}/${slug}`.replace(/^\/browse\/?/, "").replace(/\/$/, "");
}

/**
 * Pointer handlers that prefetch a browse path after a short hover, so the
 * click that follows renders from cache. `browsePath` null disables it
 * (drafts, deleted items, PR preview pages…).
 */
export function useHoverPrefetch(browsePath: string | null) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancel = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  useEffect(() => cancel, [cancel]);

  const onPointerEnter = useCallback(() => {
    if (browsePath === null) return;
    cancel();
    timer.current = setTimeout(() => {
      timer.current = null;
      void prefetchBrowsePath(browsePath);
    }, HOVER_PREFETCH_DELAY_MS);
  }, [browsePath, cancel]);

  return { onPointerEnter, onPointerLeave: cancel };
}
