"use client";

import { createElement, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { ArrowLeft, ArrowRight, Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useIsMobile } from "@/hooks/use-media-query";
import { tutorialIcon } from "./tutorial-icons";
import type { TargetRect, Tutorial, TutorialStep } from "@/lib/tutorials/types";

interface TutorialTooltipProps {
    tutorial: Tutorial;
    step: TutorialStep;
    stepIndex: number;
    total: number;
    rect: TargetRect | null;
    onNext: () => void;
    onPrev: () => void;
    onSkip: () => void;
    onFinish: () => void;
}

const CARD_WIDTH = 360;
const GAP = 14;
const MARGIN = 16;

interface Size {
    width: number;
    height: number;
}

/**
 * Compute the card's top-left corner from the target rect, the card's measured
 * size and the viewport. Position is pure math (no read-back of the rendered
 * card), so it can't feed back into a render loop, and clamping keeps the whole
 * card on-screen for every placement.
 */
function cornerFor(
    rect: TargetRect | null,
    placement: string,
    size: Size,
    vw: number,
    vh: number,
): { top: number; left: number } {
    const { width: w, height: h } = size;
    let top: number;
    let left: number;

    if (!rect || placement === "center") {
        left = (vw - w) / 2;
        top = (vh - h) / 2;
    } else {
        const cx = rect.left + rect.width / 2;
        const cy = rect.top + rect.height / 2;
        switch (placement) {
            case "top":
                left = cx - w / 2;
                top = rect.top - GAP - h;
                break;
            case "left":
                left = rect.left - GAP - w;
                top = cy - h / 2;
                break;
            case "right":
                left = rect.left + rect.width + GAP;
                top = cy - h / 2;
                break;
            case "bottom":
            default:
                left = cx - w / 2;
                top = rect.top + rect.height + GAP;
                break;
        }
    }

    // Clamp so the whole card stays within the viewport margins.
    left = Math.max(MARGIN, Math.min(left, vw - MARGIN - w));
    top = Math.max(MARGIN, Math.min(top, vh - MARGIN - h));
    return { top, left };
}

export function TutorialTooltip({
    tutorial,
    step,
    stepIndex,
    total,
    rect,
    onNext,
    onPrev,
    onSkip,
    onFinish,
}: TutorialTooltipProps) {
    const t = useTranslations("Tutorials");
    const tc = useTranslations("Tutorials.controls");
    const isMobile = useIsMobile();
    const cardRef = useRef<HTMLDivElement>(null);
    // Measured card size (only the size — never the rendered position — so this
    // can't feed back into a layout loop) and the live viewport dimensions.
    const [size, setSize] = useState<Size>({ width: CARD_WIDTH, height: 160 });
    const [viewport, setViewport] = useState(() => ({
        w: typeof window === "undefined" ? 1024 : window.innerWidth,
        h: typeof window === "undefined" ? 768 : window.innerHeight,
    }));

    const isFirst = stepIndex === 0;
    const isLast = stepIndex === total - 1;

    const title = t(`${tutorial.id}.steps.${step.id}.title`);
    const body = t(`${tutorial.id}.steps.${step.id}.body`);

    const mobile = isMobile;
    const placement = step.placement ?? "bottom";

    // Track the card's intrinsic size (changes only with content, not position).
    useLayoutEffect(() => {
        const el = cardRef.current;
        if (!el) return;
        const apply = () => {
            const r = el.getBoundingClientRect();
            setSize((prev) =>
                prev.width === r.width && prev.height === r.height
                    ? prev
                    : { width: r.width, height: r.height },
            );
        };
        apply();
        const ro = new ResizeObserver(apply);
        ro.observe(el);
        return () => ro.disconnect();
    }, [step, mobile]);

    // Keep positioning in sync with the viewport.
    useEffect(() => {
        const onResize = () => setViewport({ w: window.innerWidth, h: window.innerHeight });
        window.addEventListener("resize", onResize);
        return () => window.removeEventListener("resize", onResize);
    }, []);

    const corner = cornerFor(rect, placement, size, viewport.w, viewport.h);
    const style: React.CSSProperties = mobile
        ? {}
        : {
              top: corner.top,
              left: corner.left,
              width: CARD_WIDTH,
              maxWidth: `calc(100vw - ${2 * MARGIN}px)`,
          };

    return (
        <div
            ref={cardRef}
            role="dialog"
            aria-modal="true"
            aria-label={title}
            className={cn(
                "fixed z-[1002] flex flex-col gap-3 rounded-2xl border bg-popover p-5 text-popover-foreground shadow-2xl",
                "animate-in fade-in zoom-in-95 duration-200",
                "motion-safe:transition-[top,left] motion-safe:duration-300 motion-safe:ease-out",
                mobile && "inset-x-0 bottom-0 rounded-b-none border-x-0 border-b-0 pb-[calc(env(safe-area-inset-bottom)+1.25rem)]",
            )}
            style={style}
        >
            <div className="flex items-start gap-3">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                    {createElement(tutorialIcon(tutorial.icon), { className: "size-5" })}
                </span>
                <div className="min-w-0 flex-1">
                    <h2 className="text-base font-semibold leading-tight">{title}</h2>
                    <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{body}</p>
                </div>
                <button
                    type="button"
                    onClick={onSkip}
                    aria-label={tc("skip")}
                    className="shrink-0 rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                    <X className="size-4" />
                </button>
            </div>

            <div className="flex items-center justify-between gap-3 pt-1">
                <div className="flex items-center gap-1.5" aria-hidden>
                    {Array.from({ length: total }).map((_, i) => (
                        <span
                            key={i}
                            className={cn(
                                "h-1.5 rounded-full transition-all",
                                i === stepIndex ? "w-5 bg-primary" : "w-1.5 bg-muted-foreground/30",
                            )}
                        />
                    ))}
                </div>
                <div className="flex items-center gap-2">
                    {!isFirst && (
                        <Button variant="ghost" size="sm" onClick={onPrev}>
                            <ArrowLeft className="size-4" />
                            {tc("back")}
                        </Button>
                    )}
                    {isLast ? (
                        <Button size="sm" onClick={onFinish}>
                            <Check className="size-4" />
                            {tc("done")}
                        </Button>
                    ) : (
                        <Button size="sm" onClick={onNext}>
                            {tc("next")}
                            <ArrowRight className="size-4" />
                        </Button>
                    )}
                </div>
            </div>
            <p className="text-center text-[11px] text-muted-foreground">
                {tc("progress", { current: stepIndex + 1, total })}
            </p>
        </div>
    );
}
