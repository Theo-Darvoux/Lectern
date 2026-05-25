"use client";

import { useEffect, useRef } from "react";
import { useUIStore } from "@/lib/stores";

export function useScrollHide(scrollRef: React.RefObject<HTMLElement | null>) {
  const setNavbarVisible = useUIStore((s) => s.setNavbarVisible);
  const lastY = useRef(0);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const onScroll = () => {
      const y = el.scrollTop;
      const delta = y - lastY.current;
      if (Math.abs(delta) < 5) return;
      // Show when near top or scrolling up; hide when scrolling down
      setNavbarVisible(delta < 0 || y < 40);
      lastY.current = y;
    };

    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [scrollRef, setNavbarVisible]);
}
