"use client";

import { useEffect, useRef } from "react";
import { useUIStore } from "@/lib/stores";

export interface ScrollHideOptions {
  onlyShowAtTop?: boolean;
}

export function useScrollHide(
  scrollRef: React.RefObject<HTMLElement | null>,
  options?: ScrollHideOptions
) {
  const setNavbarVisible = useUIStore((s) => s.setNavbarVisible);
  const lastY = useRef(0);
  const accumulated = useRef(0);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const onScroll = () => {
      const y = el.scrollTop;
      const delta = y - lastY.current;
      lastY.current = y;

      if (options?.onlyShowAtTop) {
        // Only show when fully at the top (y <= 0)
        setNavbarVisible(y <= 0);
        return;
      }

      // Always show navbar when the user is at (or near) the top of the document
      if (y < 40) {
        accumulated.current = 0;
        setNavbarVisible(true);
        return;
      }

      // Ignore micro-movements (e.g. trackpad jitter)
      if (Math.abs(delta) < 2) return;

      // Reset accumulator on direction change
      if ((delta > 0 && accumulated.current < 0) || (delta < 0 && accumulated.current > 0)) {
        accumulated.current = 0;
      }

      accumulated.current += delta;

      // Hide quickly on scroll-down (150px), but require a strong intentional
      // upward scroll (500px) before showing the toolbar again.
      const threshold = accumulated.current < 0 ? 500 : 150;
      if (Math.abs(accumulated.current) >= threshold) {
        setNavbarVisible(accumulated.current < 0);
        accumulated.current = 0;
      }
    };

    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [scrollRef, setNavbarVisible, options?.onlyShowAtTop]);
}

