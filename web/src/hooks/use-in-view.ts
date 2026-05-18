import { startTransition, useEffect, useState } from "react";

// Shared IntersectionObserver instances keyed by rootMargin.
// Using one observer for all elements is far cheaper than one-per-element:
// each separate IntersectionObserver registers its own internal scroll tracking.
const sharedObservers = new Map<string, IntersectionObserver>();
const callbacks = new Map<Element, (inView: boolean) => void>();

function getObserver(rootMargin: string): IntersectionObserver | null {
  if (typeof window === "undefined" || !("IntersectionObserver" in window)) return null;
  if (sharedObservers.has(rootMargin)) return sharedObservers.get(rootMargin)!;

  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const cb = callbacks.get(entry.target);
        if (cb) cb(entry.isIntersecting);
      }
    },
    { rootMargin, threshold: 0 },
  );
  sharedObservers.set(rootMargin, observer);
  return observer;
}

/**
 * Returns true once the element attached to `ref` has intersected the viewport.
 * Stays true after the first intersection (load-once semantics).
 * rootMargin defaults to "150px" so loading starts slightly before the element
 * scrolls into view, avoiding a visible pop-in on fast scrolls.
 *
 * Uses a shared IntersectionObserver per rootMargin value so that mounting
 * 50+ cards doesn't create 50+ observers, each with its own scroll tracking.
 * State updates are wrapped in startTransition to batch a burst of simultaneous
 * intersections during fast scroll instead of each update blocking the paint frame.
 */
export function useInView(
  ref: React.RefObject<Element | null>,
  rootMargin = "150px",
): boolean {
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || inView) return;

    const observer = getObserver(rootMargin);
    if (!observer) {
      setInView(true);
      return;
    }

    const callback = (isIntersecting: boolean) => {
      if (isIntersecting) {
        startTransition(() => setInView(true));
        callbacks.delete(el);
        observer.unobserve(el);
      }
    };

    callbacks.set(el, callback);
    observer.observe(el);

    return () => {
      callbacks.delete(el);
      observer.unobserve(el);
    };
  }, [ref, inView, rootMargin]);

  return inView;
}
