"use client";

import { useEffect, useRef } from "react";
import { useUIStore } from "@/lib/stores";
import { useIsMobile } from "./use-media-query";

export interface ScrollHideOptions {
  onlyShowAtTop?: boolean;
}

export function useScrollHide(
  scrollRef: React.RefObject<HTMLElement | null>,
  options?: ScrollHideOptions
) {
  const setNavbarVisible = useUIStore((s) => s.setNavbarVisible);
  const isMobile = useIsMobile();
  const lastY = useRef(0);
  const accumulated = useRef(0);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    // We only use touch tracking on mobile if we're not in onlyShowAtTop mode
    const useTouch = isMobile && !options?.onlyShowAtTop;

    if (useTouch) {
      const onTouchStart = (e: TouchEvent) => {
        lastY.current = e.touches[0].clientY;
      };

      const onTouchMove = (e: TouchEvent) => {
        const y = e.touches[0].clientY;
        const delta = lastY.current - y;
        lastY.current = y;

        // Find the active scrollable container
        let scrollContainer = el;
        let target = e.target as HTMLElement | null;
        while (target && target !== el) {
          if (target.scrollHeight > target.clientHeight) {
            scrollContainer = target;
            break;
          }
          target = target.parentElement;
        }

        const scrollTop = scrollContainer.scrollTop;
        const scrollHeight = scrollContainer.scrollHeight;
        const clientHeight = scrollContainer.clientHeight;

        // Always show navbar when the user is at (or near) the top of the document and scrolls up
        if (scrollTop < 40) {
          accumulated.current = 0;
          if (delta < 0) {
            setNavbarVisible(true);
          }
          return;
        }

        // Never hide the navbar if the scrollable area is small
        if (scrollHeight - clientHeight < 150) {
          return;
        }

        // Ignore micro-movements (e.g. touch jitter)
        if (Math.abs(delta) < 2) return;

        // Reset accumulator on direction change
        if ((delta > 0 && accumulated.current < 0) || (delta < 0 && accumulated.current > 0)) {
          accumulated.current = 0;
        }

        accumulated.current += delta;

        // On mobile, show after 750px of upscroll (delta < 0), hide after 100px of downscroll (delta > 0)
        const threshold = accumulated.current < 0 ? 750 : 100;
        if (Math.abs(accumulated.current) >= threshold) {
          setNavbarVisible(accumulated.current < 0);
          accumulated.current = 0;
        }
      };

      el.addEventListener("touchstart", onTouchStart, { passive: true });
      el.addEventListener("touchmove", onTouchMove, { passive: true });

      return () => {
        el.removeEventListener("touchstart", onTouchStart);
        el.removeEventListener("touchmove", onTouchMove);
      };
    } else {
      const onScroll = () => {
        const y = el.scrollTop;
        const delta = y - lastY.current;
        lastY.current = y;

        if (options?.onlyShowAtTop) {
          // Only show when fully at the top (y <= 0)
          setNavbarVisible(y <= 0);
          return;
        }

        // Always show navbar when the user is at (or near) the top of the document and scrolls up
        if (y < 40) {
          accumulated.current = 0;
          if (delta < 0) {
            setNavbarVisible(true);
          }
          return;
        }

        // Never hide the navbar if the scrollable area is small
        if (el.scrollHeight - el.clientHeight < 150) {
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
        // upward scroll (1000px) before showing the toolbar again.
        const threshold = accumulated.current < 0 ? 1000 : 150;
        if (Math.abs(accumulated.current) >= threshold) {
          setNavbarVisible(accumulated.current < 0);
          accumulated.current = 0;
        }
      };

      el.addEventListener("scroll", onScroll, { passive: true });
      return () => el.removeEventListener("scroll", onScroll);
    }
  }, [scrollRef, setNavbarVisible, options?.onlyShowAtTop, isMobile]);
}

