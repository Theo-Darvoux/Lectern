"use client";

import React, { useEffect, useState, useMemo, useRef, useCallback } from "react";
import { usePinchZoom } from "@/hooks/use-pinch-zoom";
import { useMaterialFile } from "@/hooks/use-material-file";
import type { ThreadData } from "@/hooks/use-annotations";
import { MarkdownRenderer } from "./markdown-renderer";
import { registerViewerPrint, unregisterViewerPrint } from "@/lib/viewer-print-registry";
import { ViewerShell } from "./viewer-shell";
import { ZoomControls } from "./zoom-controls";
import { AnnotationInlinePopover } from "@/components/annotations/annotation-inline-popover";

const MIN_ZOOM = 50;
const MAX_ZOOM = 200;
const ZOOM_STEP = 10;

interface MarkdownViewerProps {
    fileKey: string;
    materialId: string;
    material?: Record<string, unknown>;
    annotations?: ThreadData[];
    targetAnnotationId?: string | null;
}

interface HighlightRect {
    x: number;
    y: number;
    w: number;
    h: number;
    threadId: string;
}

function buildHighlights(container: HTMLElement, annotations: ThreadData[]): HighlightRect[] {
    const textNodes: { node: Text; start: number; end: number }[] = [];
    let fullText = "";

    function walk(node: Node) {
        if (node.nodeType === Node.TEXT_NODE) {
            const text = node.textContent || "";
            textNodes.push({
                node: node as Text,
                start: fullText.length,
                end: fullText.length + text.length,
            });
            fullText += text;
        } else if (node.nodeType === Node.ELEMENT_NODE) {
            const el = node as HTMLElement;
            if (el.tagName === "SCRIPT" || el.tagName === "STYLE") return;
            for (let i = 0; i < node.childNodes.length; i++) {
                walk(node.childNodes[i]);
            }
        }
    }

    walk(container);

    const highlights: HighlightRect[] = [];
    const containerRect = container.getBoundingClientRect();

    for (const thread of annotations) {
        const searchText = thread.root.selection_text;
        if (!searchText) continue;

        const pd = thread.root.position_data;
        const targetOcc = typeof pd?.occurrenceIndex === "number" ? pd.occurrenceIndex : null;
        let currentOcc = 0;
        let searchFrom = 0;
        let idx: number;

        while ((idx = fullText.indexOf(searchText, searchFrom)) !== -1) {
            const matchEnd = idx + searchText.length;

            // If occurrenceIndex is stored, render only that occurrence; otherwise render all (legacy annotations)
            if (targetOcc === null || currentOcc === targetOcc) {
                const range = document.createRange();

                let startNode: Text | null = null;
                let startOffset = 0;
                let endNode: Text | null = null;
                let endOffset = 0;

                for (const { node, start, end } of textNodes) {
                    if (!startNode && end > idx) {
                        startNode = node;
                        startOffset = idx - start;
                    }
                    if (end >= matchEnd) {
                        endNode = node;
                        endOffset = matchEnd - start;
                        break;
                    }
                }

                if (startNode && endNode) {
                    try {
                        range.setStart(startNode, startOffset);
                        range.setEnd(endNode, endOffset);

                        const rects = range.getClientRects();
                        for (let i = 0; i < rects.length; i++) {
                            const r = rects[i];
                            // skip zero-size and full-width rects produced when a
                            // match crosses a block boundary (e.g. <li> siblings)
                            if (r.width <= 0 || r.height <= 0 || r.width > containerRect.width * 1.1) continue;
                            highlights.push({
                                x: r.left - containerRect.left + container.scrollLeft,
                                y: r.top - containerRect.top + container.scrollTop,
                                w: r.width,
                                h: r.height,
                                threadId: thread.root.id,
                            });
                        }
                    } catch (e) {
                        console.error("Failed to create range for highlight", e);
                    }
                }

                if (targetOcc !== null) break; // found the target occurrence, stop
            }

            currentOcc++;
            searchFrom = matchEnd;
        }
    }

    return highlights;
}

export function MarkdownViewer({
    materialId,
    fileKey,
    material,
    annotations = [],
    targetAnnotationId,
}: MarkdownViewerProps) {
    const proseRef = useRef<HTMLDivElement>(null);
    const scrollRef = useRef<HTMLDivElement>(null);
    const resizeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const scrolledMarkdownTargetRef = useRef<string | null>(null);
    const [highlights, setHighlights] = useState<HighlightRect[]>([]);
    const [activeAnnotation, setActiveAnnotation] = useState<{
        thread: ThreadData;
        clientX: number;
        clientY: number;
    } | null>(null);

    const { content, loading, error, reload } = useMaterialFile({
        materialId,
        fileKey,
        mode: "text",
    });

    const parsedContent = useMemo(() => {
        if (!content) return "";
        return content.replace(/!\[\[(.*?)\]\]/g, (_, p1) => `![${p1}](${encodeURIComponent(p1)})`);
    }, [content]);

    const { zoom, zoomIn, zoomOut, resetZoom } = usePinchZoom({
        initial: 100,
        min: MIN_ZOOM,
        max: MAX_ZOOM,
        step: ZOOM_STEP,
        targetRef: scrollRef,
        handleKeyboard: true,
    });

    useEffect(() => {
        registerViewerPrint(materialId, {
            getContent: () => proseRef.current?.innerHTML ?? null
        });
        return () => unregisterViewerPrint(materialId);
    }, [materialId]);

    // `content-visibility: auto` lets the browser skip layout/paint for the
    // markdown blocks scrolled out of view — the whole document stays in the DOM
    // (so print and in-page search still see everything), but only the visible
    // blocks cost rendering time. The `auto` keyword in `contain-intrinsic-size`
    // makes the browser remember each block's real height after its first paint,
    // so the scrollbar settles once a block has been seen.
    //
    // It is disabled while annotations are present: `buildHighlights` measures
    // text ranges across the entire document, and offscreen blocks skipped by
    // content-visibility report no client rects, which would misplace highlights.
    const enableContentVis = annotations.length === 0;
    const rendered = useMemo(
        () =>
            parsedContent ? (
                <MarkdownRenderer
                    content={parsedContent}
                    materialId={materialId}
                    material={material}
                    className={
                        enableContentVis
                            ? "[&>*]:[content-visibility:auto] [&>*]:[contain-intrinsic-size:auto_4rem]"
                            : undefined
                    }
                />
            ) : null,
        [parsedContent, materialId, material, enableContentVis],
    );

    const updateHighlights = useCallback(() => {
        if (!proseRef.current || !annotations.length) {
            setHighlights([]);
            return;
        }
        const next = buildHighlights(proseRef.current, annotations);
        setHighlights(next);
    }, [annotations]);

    useEffect(() => {
        if (!loading && !error && parsedContent) {
            const raf = requestAnimationFrame(updateHighlights);
            return () => cancelAnimationFrame(raf);
        }
    }, [loading, error, parsedContent, updateHighlights]);

    useEffect(() => {
        if (!proseRef.current) return;
        // Recomputing highlights walks the whole document, so coalesce the bursts
        // of resize callbacks (fonts loading, zoom, container reflow) into one run.
        const ro = new ResizeObserver(() => {
            if (resizeTimerRef.current) clearTimeout(resizeTimerRef.current);
            resizeTimerRef.current = setTimeout(updateHighlights, 100);
        });
        ro.observe(proseRef.current);
        return () => {
            ro.disconnect();
            if (resizeTimerRef.current) clearTimeout(resizeTimerRef.current);
        };
    }, [updateHighlights]);

    const handleHighlightClick = useCallback((threadId: string, e: React.MouseEvent) => {
        const thread = annotations.find((t) => t.root.id === threadId) ?? null;
        if (thread) {
            setActiveAnnotation({ thread, clientX: e.clientX, clientY: e.clientY });
        }
    }, [annotations]);

    useEffect(() => {
        if (!targetAnnotationId || highlights.length === 0) return;
        if (scrolledMarkdownTargetRef.current === targetAnnotationId) return;

        const targetThread = annotations.find(
            (t) => t.root.id === targetAnnotationId || t.replies.some((r) => r.id === targetAnnotationId),
        );
        if (!targetThread) return;

        const tryScroll = () => {
            const el = proseRef.current?.querySelector<HTMLElement>(`[data-thread-id="${targetThread.root.id}"]`);
            if (el) {
                scrolledMarkdownTargetRef.current = targetAnnotationId;
                el.scrollIntoView({ behavior: "smooth", block: "center" });
                el.classList.add("ring-2", "ring-primary", "ring-offset-2");
                return true;
            }
            return false;
        };

        if (!tryScroll()) {
            let attempts = 0;
            const interval = setInterval(() => {
                attempts++;
                if (tryScroll() || attempts >= 10) {
                    clearInterval(interval);
                }
            }, 100);
            return () => clearInterval(interval);
        }
    }, [targetAnnotationId, highlights, annotations]);

    return (
        <>
        <ViewerShell
            scrollRef={scrollRef}
            loading={loading}
            error={error ? "Failed to load markdown content." : null}
            onRetry={reload}
            toolbarRight={
                <ZoomControls
                    zoom={zoom}
                    onZoomIn={zoomIn}
                    onZoomOut={zoomOut}
                    onReset={resetZoom}
                    min={MIN_ZOOM}
                    max={MAX_ZOOM}
                    disabled={loading || !!error}
                />
            }
        >
            <div
                ref={proseRef}
                data-annotation-scope="true"
                className={`prose prose-sm max-w-none p-6 dark:prose-invert
                    prose-img:rounded-lg prose-img:shadow-sm
                    prose-a:text-primary prose-a:no-underline hover:prose-a:underline
                    prose-pre:bg-muted prose-pre:border prose-pre:border-border prose-pre:text-foreground
                    prose-code:before:content-none prose-code:after:content-none prose-code:text-foreground
                    prose-table:border-collapse
                    prose-th:border prose-th:border-border prose-th:px-3 prose-th:py-2
                    prose-td:border prose-td:border-border prose-td:px-3 prose-td:py-2
                    prose-headings:scroll-mt-20
                    relative
                    [&_mark]:bg-yellow-200 [&_mark]:text-yellow-900 dark:[&_mark]:bg-yellow-500/20 dark:[&_mark]:text-yellow-200`}
                style={{ fontSize: `${zoom}%` }}
            >
                {rendered}

                {/* Highlight Overlays */}
                {highlights.map((h, i) => (
                    <div
                        key={i}
                        data-thread-id={h.threadId}
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={(e) => handleHighlightClick(h.threadId, e)}
                        className="annotation-highlight rounded-sm"
                        style={{
                            position: "absolute",
                            left: h.x,
                            top: h.y,
                            width: h.w,
                            height: h.h,
                            zIndex: 4,
                            cursor: "pointer",
                        }}
                    />
                ))}
            </div>
        </ViewerShell>
        {activeAnnotation && (
            <AnnotationInlinePopover
                thread={activeAnnotation.thread}
                clientX={activeAnnotation.clientX}
                clientY={activeAnnotation.clientY}
                onClose={() => setActiveAnnotation(null)}
            />
        )}
        </>
    );
}
