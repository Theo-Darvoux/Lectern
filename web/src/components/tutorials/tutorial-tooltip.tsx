"use client";

import { createElement, useLayoutEffect, useRef, useState } from "react";
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

/** Position the card around the target using a single anchor + transform. */
function cardStyle(rect: TargetRect | null, placement: string): React.CSSProperties {
    if (typeof window === "undefined") {
        return { top: "50%", left: "50%", transform: "translate(-50%, -50%)" };
    }
    if (!rect || placement === "center") {
        // Pixel-based center so transitions to/from anchored steps interpolate
        // smoothly (CSS won't animate between px and %).
        return {
            top: window.innerHeight / 2,
            left: window.innerWidth / 2,
            transform: "translate(-50%, -50%)",
        };
    }
    const vw = window.innerWidth;
    const cardW = Math.min(CARD_WIDTH, vw - 2 * MARGIN);
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    // Clamp the horizontal centre so a centered card stays on-screen.
    const clampedCx = Math.max(MARGIN + cardW / 2, Math.min(cx, vw - MARGIN - cardW / 2));

    switch (placement) {
        case "top":
            return { top: rect.top - GAP, left: clampedCx, transform: "translate(-50%, -100%)" };
        case "left":
            return { top: cy, left: rect.left - GAP, transform: "translate(-100%, -50%)" };
        case "right":
            return { top: cy, left: rect.left + rect.width + GAP, transform: "translate(0, -50%)" };
        case "bottom":
        default:
            return { top: rect.top + rect.height + GAP, left: clampedCx, transform: "translate(-50%, 0)" };
    }
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
    // Corrective translate that pulls the card fully back into the viewport
    // after layout — anchor + placement transform alone can overflow any edge
    // when the target sits near it.
    const [correction, setCorrection] = useState({ dx: 0, dy: 0 });

    const isFirst = stepIndex === 0;
    const isLast = stepIndex === total - 1;

    const title = t(`${tutorial.id}.steps.${step.id}.title`);
    const body = t(`${tutorial.id}.steps.${step.id}.body`);

    const mobile = isMobile;
    const placement = step.placement ?? "bottom";

    // Reset the correction whenever the anchor or step changes, so the next
    // measurement starts from the raw placement.
    useLayoutEffect(() => {
        setCorrection((c) => (c.dx === 0 && c.dy === 0 ? c : { dx: 0, dy: 0 }));
    }, [rect, step, mobile]);

    // Measure the rendered card and nudge it on-screen. Runs again after each
    // correction (converges to a no-op once it fits).
    useLayoutEffect(() => {
        const el = cardRef.current;
        if (!el || mobile) return;
        const r = el.getBoundingClientRect();
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        let dx = 0;
        let dy = 0;
        if (r.left < MARGIN) dx = MARGIN - r.left;
        else if (r.right > vw - MARGIN) dx = vw - MARGIN - r.right;
        if (r.top < MARGIN) dy = MARGIN - r.top;
        else if (r.bottom > vh - MARGIN) dy = vh - MARGIN - r.bottom;
        if (dx !== 0 || dy !== 0) {
            setCorrection((c) => ({ dx: c.dx + dx, dy: c.dy + dy }));
        }
    }, [rect, step, mobile, correction]);

    const base = cardStyle(rect, placement);
    const style: React.CSSProperties = mobile
        ? {}
        : {
              ...base,
              transform: `${base.transform ?? ""} translate(${correction.dx}px, ${correction.dy}px)`,
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
                "motion-safe:transition-[top,left,transform] motion-safe:duration-300 motion-safe:ease-out",
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
