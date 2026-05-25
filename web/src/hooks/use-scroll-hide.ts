"use client";

import { useEffect, useRef } from "react";
import { useUIStore } from "@/lib/stores";

export function useScrollHide(scrollRef: React.RefObject<HTMLElement | null>) {
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

      // Only toggle after the user has scrolled 150px in one sustained direction
      if (Math.abs(accumulated.current) >= 150) {
        setNavbarVisible(accumulated.current < 0);
        accumulated.current = 0;
      }
    };

    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [scrollRef, setNavbarVisible]);
}
