"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { usePathname, useRouter } from "next/navigation";
import { useTutorialRun } from "@/lib/tutorials/tutorial-store";
import { useTutorial } from "@/lib/tutorials/use-tutorial";
import type { TargetRect } from "@/lib/tutorials/types";
import { TutorialSpotlight } from "./tutorial-spotlight";
import { TutorialTooltip } from "./tutorial-tooltip";

function measure(el: Element): TargetRect {
    const r = el.getBoundingClientRect();
    return { top: r.top, left: r.left, width: r.width, height: r.height };
}

/**
 * Resolve a selector to the first *visible* match. The same anchor often exists
 * twice (mobile + desktop variants), with one hidden via `display:none`; a
 * hidden element measures as a zero-size rect, so prefer a laid-out one.
 */
function findVisibleTarget(selector: string): Element | null {
    const els = document.querySelectorAll(selector);
    for (const el of els) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) return el;
    }
    return els[0] ?? null;
}

/**
 * Drives the active tutorial: resolves each step's target (navigating and
 * polling for async-rendered UI), keeps the spotlight in sync on scroll/resize,
 * and renders the dim + card via a portal. Steps whose target never appears are
 * skipped in the direction of travel.
 */
export function TutorialOverlay() {
    const { active, stepIndex, next, prev, cancel } = useTutorialRun();
    const { markComplete } = useTutorial();
    const router = useRouter();
    const pathname = usePathname();

    const [rect, setRect] = useState<TargetRect | null>(null);
    const [ready, setReady] = useState(false);
    // Once the first step has been revealed, keep the overlay mounted across
    // step changes so the card/dim glide to the next spot instead of vanishing
    // while the next target is resolved.
    const [shownOnce, setShownOnce] = useState(false);
    const directionRef = useRef<1 | -1>(1);

    const step = active?.steps[stepIndex] ?? null;
    const total = active?.steps.length ?? 0;

    const handleNext = useCallback(() => {
        directionRef.current = 1;
        next();
    }, [next]);

    const handlePrev = useCallback(() => {
        directionRef.current = -1;
        prev();
    }, [prev]);

    const finish = useCallback(() => {
        if (active) void markComplete(active.id);
        cancel();
    }, [active, markComplete, cancel]);

    // Track first reveal / teardown so the overlay stays mounted between steps.
    useEffect(() => {
        if (ready) setShownOnce(true);
    }, [ready]);
    useEffect(() => {
        if (!active) setShownOnce(false);
    }, [active]);

    // Resolve the current step's target: navigate if needed, then poll for it.
    useEffect(() => {
        if (!active || !step) return;
        let cancelled = false;
        setReady(false);

        // Navigate to the step's route first if we're not already there.
        if (step.route && pathname !== step.route) {
            router.push(step.route);
        }

        // Centered step — no target to resolve.
        if (!step.target) {
            setRect(null);
            setReady(true);
            return;
        }

        const target = step.target;
        const deadline = Date.now() + (step.waitForTarget ? 3000 : 1200);
        let raf = 0;

        const poll = () => {
            if (cancelled) return;
            const el = findVisibleTarget(target);
            if (el) {
                const r = el.getBoundingClientRect();
                const fullyInView =
                    r.top >= 0 &&
                    r.left >= 0 &&
                    r.bottom <= window.innerHeight &&
                    r.right <= window.innerWidth;
                if (fullyInView) {
                    // Already visible (e.g. fixed nav) — measure now so the card
                    // glides straight to it with no dead wait.
                    setRect(measure(el));
                    setReady(true);
                    return;
                }
                el.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
                // Let the smooth scroll settle before measuring.
                window.setTimeout(() => {
                    if (cancelled) return;
                    setRect(measure(el));
                    setReady(true);
                }, 280);
                return;
            }
            if (Date.now() > deadline) {
                // Target absent (e.g. role can't see it) — skip in travel direction.
                if (directionRef.current === 1) {
                    if (stepIndex < total - 1) next();
                    else finish();
                } else if (stepIndex > 0) {
                    prev();
                } else {
                    next();
                }
                return;
            }
            raf = requestAnimationFrame(poll);
        };
        poll();

        return () => {
            cancelled = true;
            if (raf) cancelAnimationFrame(raf);
        };
    }, [active, step, stepIndex, total, pathname, router, next, prev, finish]);

    // Keep the spotlight aligned while the step is shown.
    useEffect(() => {
        if (!ready || !step?.target) return;
        const el = findVisibleTarget(step.target);
        if (!el) return;
        const update = () => setRect(measure(el));
        const ro = new ResizeObserver(update);
        ro.observe(el);
        window.addEventListener("scroll", update, true);
        window.addEventListener("resize", update);
        return () => {
            ro.disconnect();
            window.removeEventListener("scroll", update, true);
            window.removeEventListener("resize", update);
        };
    }, [ready, step]);

    // Keyboard navigation.
    useEffect(() => {
        if (!active) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") finish();
            else if (e.key === "ArrowRight" || e.key === "Enter") handleNext();
            else if (e.key === "ArrowLeft") handlePrev();
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [active, finish, handleNext, handlePrev]);

    if (!active || !step || !(ready || shownOnce) || typeof document === "undefined") return null;

    return createPortal(
        <>
            <TutorialSpotlight
                rect={rect}
                allowInteraction={step.allowInteraction ?? false}
                onBackdropClick={finish}
            />
            <TutorialTooltip
                tutorial={active}
                step={step}
                stepIndex={stepIndex}
                total={total}
                rect={rect}
                onNext={handleNext}
                onPrev={handlePrev}
                onSkip={finish}
                onFinish={finish}
            />
            {/* Jump-to-step affordance is handled via goTo from the dots if needed. */}
            <span className="sr-only" aria-live="polite">
                {stepIndex + 1} / {total}
            </span>
        </>,
        document.body,
    );
}
