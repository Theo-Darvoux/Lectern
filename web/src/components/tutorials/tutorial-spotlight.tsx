"use client";

import { cn } from "@/lib/utils";
import type { TargetRect } from "@/lib/tutorials/types";

interface TutorialSpotlightProps {
    /** Target rectangle in viewport coords, or null for a full-screen dim. */
    rect: TargetRect | null;
    /** Allow clicks to reach the highlighted element. */
    allowInteraction: boolean;
    /** Clicking the dimmed backdrop triggers this (used to skip/close). */
    onBackdropClick?: () => void;
}

const DIM = "rgba(2, 6, 23, 0.55)";

/**
 * Renders the dimming backdrop with an animated cut-out around the target.
 * The visual hole is a single `box-shadow`-spread div so it morphs smoothly
 * between steps. A separate click layer enforces interaction blocking.
 */
export function TutorialSpotlight({ rect, allowInteraction, onBackdropClick }: TutorialSpotlightProps) {
    if (!rect) {
        return (
            <div
                className="fixed inset-0 z-[1000] animate-in fade-in duration-200"
                style={{ backgroundColor: DIM }}
                onClick={onBackdropClick}
                aria-hidden
            />
        );
    }

    return (
        <>
            {/* Visual hole — pointer-events disabled so it never intercepts. */}
            <div
                aria-hidden
                className="pointer-events-none fixed z-[1000] rounded-xl transition-all duration-300 ease-out"
                style={{
                    top: rect.top,
                    left: rect.left,
                    width: rect.width,
                    height: rect.height,
                    boxShadow: `0 0 0 9999px ${DIM}`,
                    outline: "2px solid var(--primary)",
                    outlineOffset: "4px",
                }}
            />
            {/* Soft pulsing ring for emphasis. */}
            <div
                aria-hidden
                className="pointer-events-none fixed z-[1001] rounded-xl ring-2 ring-primary/40 transition-all duration-300 ease-out motion-safe:animate-pulse"
                style={{ top: rect.top, left: rect.left, width: rect.width, height: rect.height }}
            />
            {/* Click layer: full-screen blocker, or 4 rects leaving the hole open. */}
            {allowInteraction ? (
                <ClickFrame rect={rect} onClick={onBackdropClick} />
            ) : (
                <div
                    className="fixed inset-0 z-[999]"
                    onClick={onBackdropClick}
                    aria-hidden
                />
            )}
        </>
    );
}

/** Four transparent click-catchers around the hole, leaving it interactive. */
function ClickFrame({ rect, onClick }: { rect: TargetRect; onClick?: () => void }) {
    const base = "fixed z-[999]";
    const right = rect.left + rect.width;
    const bottom = rect.top + rect.height;
    return (
        <>
            <div className={cn(base, "left-0 right-0 top-0")} style={{ height: Math.max(0, rect.top) }} onClick={onClick} aria-hidden />
            <div className={cn(base, "left-0 right-0 bottom-0")} style={{ top: bottom }} onClick={onClick} aria-hidden />
            <div className={cn(base, "left-0")} style={{ top: rect.top, height: rect.height, width: Math.max(0, rect.left) }} onClick={onClick} aria-hidden />
            <div className={cn(base, "right-0")} style={{ top: rect.top, height: rect.height, left: right }} onClick={onClick} aria-hidden />
        </>
    );
}
