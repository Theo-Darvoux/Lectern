"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useIsMobile } from "@/hooks/use-media-query";
import { MessageSquarePlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

/**
 * Computes which occurrence (0-based) of `selText` within the text scope the
 * user selected. The scope matches exactly what buildHighlights walks so that
 * the same index finds the right match at render time.
 *
 * For PDF: scope = the page's .react-pdf__Page__textContent element.
 * For other viewers: scope = the nearest [data-annotation-scope] ancestor,
 * which must be the same element passed to buildHighlights as `container`.
 */
function computeOccurrenceIndex(
    container: HTMLElement,
    range: Range,
    selText: string,
    pageEl: Element | null,
): number {
    // Resolve the text scope
    let scopeEl: Element;
    if (pageEl) {
        scopeEl = pageEl.querySelector(".react-pdf__Page__textContent") ?? pageEl;
    } else {
        let node: Node | null = range.startContainer;
        let found: Element | null = null;
        while (node && node !== container) {
            if (node instanceof Element && node.hasAttribute("data-annotation-scope")) {
                found = node;
                break;
            }
            node = node.parentElement;
        }
        scopeEl = found ?? container;
    }

    // Walk text nodes in DOM order — same algorithm as buildHighlights
    const textNodes: Array<{ node: Text; start: number }> = [];
    let fullText = "";

    function walk(node: Node) {
        if (node.nodeType === Node.TEXT_NODE) {
            textNodes.push({ node: node as Text, start: fullText.length });
            fullText += node.textContent ?? "";
        } else if (node.nodeType === Node.ELEMENT_NODE) {
            const el = node as Element;
            if (el.tagName === "SCRIPT" || el.tagName === "STYLE") return;
            for (const child of el.childNodes) walk(child);
        }
    }
    walk(scopeEl);

    // Locate range.startContainer in the text node list
    let startNode: Node = range.startContainer;
    let startOffset = range.startOffset;
    if (startNode.nodeType !== Node.TEXT_NODE) {
        const child = (startNode as Element).childNodes[startOffset];
        if (child) { startNode = child; startOffset = 0; }
        if (startNode.nodeType !== Node.TEXT_NODE) return 0;
    }

    const entry = textNodes.find((p) => p.node === startNode);
    if (!entry) return 0;

    const absStart = entry.start + startOffset;

    // Count complete occurrences of selText that start before absStart
    let count = 0;
    let pos = 0;
    while ((pos = fullText.indexOf(selText, pos)) !== -1 && pos < absStart) {
        count++;
        pos += selText.length;
    }
    return count;
}

interface SelectionPosition {
    x: number;
    y: number;
    height: number;
    text: string;
    positionData: Record<string, unknown>;
}

interface TooltipStyle {
    top: string;
    left: string;
    transform: string;
    visibility: "visible" | "hidden";
}

interface AnnotationSelectionTooltipProps {
    containerRef: React.RefObject<HTMLElement | null>;
    onSubmit: (
        body: string,
        selectionText: string,
        positionData: Record<string, unknown>
    ) => Promise<void>;
    disabled?: boolean;
}

export function AnnotationSelectionTooltip({
    containerRef,
    onSubmit,
    disabled = false,
}: AnnotationSelectionTooltipProps) {
    const t = useTranslations("Annotations");
    const isMobile = useIsMobile();
    const [selection, setSelection] = useState<SelectionPosition | null>(null);
    const [showForm, setShowForm] = useState(false);
    const [body, setBody] = useState("");
    const [submitting, setSubmitting] = useState(false);
    const [tooltipStyle, setTooltipStyle] = useState<TooltipStyle>({
        top: "0",
        left: "0",
        transform: "none",
        visibility: "hidden",
    });
    const tooltipRef = useRef<HTMLDivElement>(null);

    const handleMouseUp = useCallback(() => {
        if (disabled) return;
        const sel = window.getSelection();
        if (!sel || sel.isCollapsed || !sel.rangeCount) {
            if (!showForm) setSelection(null);
            return;
        }

        const range = sel.getRangeAt(0);
        const text = sel.toString().trim();
        const container = containerRef.current;
        if (!text || !container || !container.contains(range.commonAncestorContainer)) {
            if (!showForm) setSelection(null);
            return;
        }

        const rect = range.getBoundingClientRect();
        const containerRect = container.getBoundingClientRect();

        let pageNum: number | undefined;
        let pageEl: Element | null = null;
        let n: Node | null = range.commonAncestorContainer;
        while (n && n !== container) {
            if (n instanceof Element && n.hasAttribute("data-page-number")) {
                pageNum = parseInt(n.getAttribute("data-page-number") || "0");
                pageEl = n;
                break;
            }
            n = n.parentElement;
        }

        const occurrenceIndex = computeOccurrenceIndex(container, range, text, pageEl);

        setSelection({
            x: rect.left - containerRect.left + container.scrollLeft + rect.width / 2,
            y: rect.top - containerRect.top + container.scrollTop,
            height: rect.height,
            text,
            positionData: { page: pageNum, textContent: text, occurrenceIndex },
        });
    }, [containerRef, showForm]);

    useEffect(() => {
        const container = containerRef.current;
        if (!container) return;
        container.addEventListener("mouseup", handleMouseUp);
        // touch devices fire touchend instead of mouseup after text selection
        container.addEventListener("touchend", handleMouseUp);
        return () => {
            container.removeEventListener("mouseup", handleMouseUp);
            container.removeEventListener("touchend", handleMouseUp);
        };
    }, [containerRef, handleMouseUp]);

    useEffect(() => {
        const handleClickOutside = (e: MouseEvent | TouchEvent) => {
            if (tooltipRef.current && !tooltipRef.current.contains(e.target as Node)) {
                setSelection(null);
                setShowForm(false);
                setBody("");
            }
        };
        document.addEventListener("mousedown", handleClickOutside);
        document.addEventListener("touchstart", handleClickOutside);
        return () => {
            document.removeEventListener("mousedown", handleClickOutside);
            document.removeEventListener("touchstart", handleClickOutside);
        };
    }, []);

    useLayoutEffect(() => {
        if (!selection || !tooltipRef.current || !containerRef.current) {
            setTooltipStyle((prev) => ({ ...prev, visibility: "hidden" }));
            return;
        }

        const tooltip = tooltipRef.current;
        const container = containerRef.current;
        const tooltipRect = tooltip.getBoundingClientRect();
        const containerRect = container.getBoundingClientRect();

        const tw = tooltipRect.width;
        const th = tooltipRect.height;
        const cw = containerRect.width;

        // selection.y is already content-relative (includes scrollTop)
        // relativeY is viewport-relative (within container)
        const relativeY = selection.y - container.scrollTop;
        const relativeX = selection.x - container.scrollLeft;

        let top = selection.y - 8;
        let left = selection.x;
        let transformY = "-100%";

        // Check if overflows top edge
        if (relativeY - 8 - th < 10) {
            // Flip to bottom
            top = selection.y + selection.height + 8;
            transformY = "0";
        }

        // Horizontal adjustment to keep within container bounds (with 10px padding)
        const translateX = "-50%";
        if (relativeX - tw / 2 < 10) {
            const overflow = 10 - (relativeX - tw / 2);
            left += overflow;
        } else if (relativeX + tw / 2 > cw - 10) {
            const overflow = (relativeX + tw / 2) - (cw - 10);
            left -= overflow;
        }

        setTooltipStyle({
            top: `${top}px`,
            left: `${left}px`,
            transform: `translate(${translateX}, ${transformY})`,
            visibility: "visible",
        });
    }, [selection, showForm, containerRef]);

    const handleSubmit = async () => {
        if (!selection || !body.trim() || submitting) return;
        setSubmitting(true);
        try {
            await onSubmit(body.trim(), selection.text, selection.positionData);
            setSelection(null);
            setShowForm(false);
            setBody("");
        } catch (err) {
            toast.error(err instanceof Error ? err.message : t("failedToSubmit"));
        } finally {
            setSubmitting(false);
        }
    };

    if (!selection) return null;

    return (
        <div
            ref={tooltipRef}
            className="absolute z-50 transition-[opacity,visibility] duration-200"
            style={{
                left: tooltipStyle.left,
                top: tooltipStyle.top,
                transform: tooltipStyle.transform,
                visibility: tooltipStyle.visibility,
                opacity: tooltipStyle.visibility === "visible" ? 1 : 0,
            }}
        >
            {showForm ? (
                <div className="w-64 rounded-lg border bg-popover p-3 shadow-lg ring-1 ring-black/5 dark:ring-white/10">
                    <p className="mb-2 border-l-2 border-yellow-400/60 pl-2 text-xs italic text-muted-foreground">
                        &ldquo;{selection.text.length > 80
                            ? selection.text.slice(0, 80) + "…"
                            : selection.text}&rdquo;
                    </p>
                    <Textarea
                        value={body}
                        onChange={(e) => setBody(e.target.value.slice(0, 1000))}
                        placeholder={t("addAnnotation")}
                        className="min-h-[60px] text-sm focus-visible:ring-1"
                        autoFocus
                        onKeyDown={(e) => {
                            if (e.key === "Enter" && !e.shiftKey && !isMobile) {
                                e.preventDefault();
                                handleSubmit();
                            }
                        }}
                    />
                        <div className="flex flex-col gap-0.5">
                            <span className="text-[10px] text-muted-foreground">
                                {body.length.toLocaleString()}/1,000
                            </span>
                            {!isMobile && (
                                <span className="text-[9px] text-muted-foreground italic opacity-70">
                                    {t("shiftEnterForNewLine")}
                                </span>
                            )}
                        </div>
                    <div className="mt-1 flex gap-2">
                        <Button
                            size="sm"
                            onClick={handleSubmit}
                            disabled={submitting || !body.trim()}
                        >
                            {t("annotate")}
                        </Button>
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                                setShowForm(false);
                                setBody("");
                            }}
                        >
                            {t("cancel")}
                        </Button>
                    </div>
                </div>
            ) : (
                <Button
                    size="sm"
                    variant="secondary"
                    className="flex items-center gap-1.5 shadow-md"
                    onClick={() => setShowForm(true)}
                >
                    <MessageSquarePlus className="h-3.5 w-3.5" />
                    {t("annotate")}
                </Button>
            )}
        </div>
    );
}

