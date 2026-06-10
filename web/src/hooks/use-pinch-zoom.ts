"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface UsePinchZoomOptions {
    /** Initial zoom level as a percentage (default: 100) */
    initial?: number;
    /** Minimum zoom percentage (default: 50) */
    min?: number;
    /** Maximum zoom percentage (default: 300) */
    max?: number;
    /** Step size for button / keyboard increments (default: 25) */
    step?: number;
    /**
     * Ref to the element that should receive touch/wheel events.
     * If omitted the hook attaches to `window` for keyboard events only.
     */
    targetRef?: React.RefObject<HTMLElement | null>;
    /** If true, also intercept Ctrl+= / Ctrl+- / Ctrl+0 keyboard shortcuts */
    handleKeyboard?: boolean;
}

interface UsePinchZoomReturn {
    zoom: number;
    setZoom: React.Dispatch<React.SetStateAction<number>>;
    zoomIn: () => void;
    zoomOut: () => void;
    resetZoom: () => void;
    origin: { x: number; y: number } | null;
}

/**
 * A modular zoom hook that adds:
 * - Pinch-to-zoom on touch screens (via touch events on `targetRef`)
 * - Ctrl + scroll-wheel zoom on pointer devices (via wheel event on `targetRef`)
 * - Optional Ctrl+= / Ctrl+- / Ctrl+0 keyboard shortcuts (via `handleKeyboard`)
 *
 * The returned `zoom` value is a percentage integer (e.g. 100 = 100%).
 */
export function usePinchZoom({
    initial = 100,
    min = 50,
    max = 300,
    step = 25,
    targetRef,
    handleKeyboard = false,
}: UsePinchZoomOptions = {}): UsePinchZoomReturn {
    const [zoom, setZoom] = useState(initial);
    const [origin, setOrigin] = useState<{ x: number; y: number } | null>(null);

    const clamp = useCallback(
        (v: number) => Math.max(min, Math.min(max, v)),
        [min, max],
    );

    const zoomIn = useCallback(() => { setOrigin(null); setZoom((z) => clamp(z + step)); }, [clamp, step]);
    const zoomOut = useCallback(() => { setOrigin(null); setZoom((z) => clamp(z - step)); }, [clamp, step]);
    const resetZoom = useCallback(() => { setOrigin(null); setZoom(initial); }, [initial]);

    // ── Pinch & wheel ────────────────────────────────────────────────────────
    // We keep the initial distance in a ref so we can compute the ratio.
    // `zoomRef` mirrors `zoom` so listeners can read the current value without
    // the effect re-running on every zoom change (avoids listener churn).
    const pinchStartDistRef = useRef<number | null>(null);
    const pinchStartZoomRef = useRef<number>(initial);
    const zoomRef = useRef(zoom);
    useEffect(() => { zoomRef.current = zoom; });

    useEffect(() => {
        const el = targetRef?.current;
        if (!el) return;

        // ----- touch (pinch) -----
        const onTouchStart = (e: TouchEvent) => {
            if (e.touches.length !== 2) return;
            const dx = e.touches[0].clientX - e.touches[1].clientX;
            const dy = e.touches[0].clientY - e.touches[1].clientY;
            pinchStartDistRef.current = Math.hypot(dx, dy);
            pinchStartZoomRef.current = zoomRef.current;
            setOrigin({
                x: (e.touches[0].clientX + e.touches[1].clientX) / 2,
                y: (e.touches[0].clientY + e.touches[1].clientY) / 2,
            });
        };

        const onTouchMove = (e: TouchEvent) => {
            if (e.touches.length !== 2 || pinchStartDistRef.current === null) return;
            e.preventDefault(); // prevent page scroll during pinch
            const dx = e.touches[0].clientX - e.touches[1].clientX;
            const dy = e.touches[0].clientY - e.touches[1].clientY;
            const dist = Math.hypot(dx, dy);
            const ratio = dist / pinchStartDistRef.current;
            setZoom(clamp(Math.round(pinchStartZoomRef.current * ratio)));
        };

        const onTouchEnd = () => {
            pinchStartDistRef.current = null;
        };

        // ----- ctrl + wheel -----
        const onWheel = (e: WheelEvent) => {
            if (!e.ctrlKey && !e.metaKey) return;
            e.preventDefault();
            setOrigin({ x: e.clientX, y: e.clientY });
            // For trackpads, deltaY is small, for mice it's ~100. Smooth proportion is better.
            const delta = -e.deltaY * (step / 100);
            setZoom((z) => clamp(z + delta));
        };

        el.addEventListener("touchstart", onTouchStart, { passive: true });
        el.addEventListener("touchmove", onTouchMove, { passive: false });
        el.addEventListener("touchend", onTouchEnd, { passive: true });
        el.addEventListener("wheel", onWheel, { passive: false });

        return () => {
            el.removeEventListener("touchstart", onTouchStart);
            el.removeEventListener("touchmove", onTouchMove);
            el.removeEventListener("touchend", onTouchEnd);
            el.removeEventListener("wheel", onWheel);
        };
    }, [targetRef, clamp, step]);

    // ── Keyboard shortcuts ───────────────────────────────────────────────────
    useEffect(() => {
        if (!handleKeyboard) return;

        const onKeyDown = (e: KeyboardEvent) => {
            if (!e.ctrlKey && !e.metaKey) return;
            if (e.key === "=" || e.key === "+") {
                e.preventDefault();
                zoomIn();
            } else if (e.key === "-") {
                e.preventDefault();
                zoomOut();
            } else if (e.key === "0") {
                e.preventDefault();
                resetZoom();
            }
        };

        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [handleKeyboard, zoomIn, zoomOut, resetZoom]);

    return { zoom, setZoom, zoomIn, zoomOut, resetZoom, origin };
}
