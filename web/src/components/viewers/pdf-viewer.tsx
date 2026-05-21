"use client";

import React, { useEffect, useRef, useState, useCallback } from "react";
import { ZoomIn, ZoomOut, BookOpen } from "lucide-react";
import { Document, Page, pdfjs } from "react-pdf";
import { Skeleton } from "@/components/ui/skeleton";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import type { ThreadData } from "@/hooks/use-annotations";
import { usePinchZoom } from "@/hooks/use-pinch-zoom";
import { useMaterialFile } from "@/hooks/use-material-file";
import { ViewerShell } from "./viewer-shell";
import { AnnotationInlinePopover } from "@/components/annotations/annotation-inline-popover";
import { useTranslations } from "next-intl";

pdfjs.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

// Suppress known "AbortException: TextLayer task cancelled" logs
if (typeof window !== "undefined") {
    const originalError = console.error;
    const originalWarn = console.warn;
    const filterArgs = (args: unknown[]) => {
        const msg = args[0];
        if (typeof msg === "string" && (msg.includes("AbortException") || msg.includes("InvalidPDFException"))) return true;
        if (msg instanceof Error && (msg.name === "AbortException" || msg.name === "InvalidPDFException")) return true;
        return false;
    };
    console.error = (...args) => {
        if (filterArgs(args)) return;
        originalError(...args);
    };
    console.warn = (...args) => {
        if (filterArgs(args)) return;
        originalWarn(...args);
    };
}

const ZOOM_STEP = 25;
const MIN_ZOOM = 50;
const MAX_ZOOM = 300;

interface PdfViewerProps {
    fileKey: string;
    materialId: string;
    annotations?: ThreadData[];
}

interface HighlightRect {
    x: number;
    y: number;
    w: number;
    h: number;
}

interface PageAnnotation {
    selection_text: string | null;
    page: number | null;
    occurrenceIndex: number | null;
    threadId: string;
}

function buildHighlightRanges(
    pageEl: HTMLElement,
    annotations: PageAnnotation[],
): Array<{ range: Range; threadId: string }> {
    const textLayer = pageEl.querySelector(".react-pdf__Page__textContent");
    if (!textLayer) return [];

    // Walk raw text nodes in DOM order — matches computeOccurrenceIndex exactly,
    // avoiding double-counting from pdfjs markedContent wrapper spans.
    const textNodes: { node: Text; start: number; end: number }[] = [];
    let fullText = "";

    function walk(node: Node) {
        if (node.nodeType === Node.TEXT_NODE) {
            const t = node.textContent ?? "";
            if (t.length > 0) {
                textNodes.push({ node: node as Text, start: fullText.length, end: fullText.length + t.length });
                fullText += t;
            }
        } else if (node.nodeType === Node.ELEMENT_NODE) {
            const el = node as Element;
            if (el.tagName === "SCRIPT" || el.tagName === "STYLE") return;
            for (const child of el.childNodes) walk(child);
        }
    }
    walk(textLayer);

    if (textNodes.length === 0) return [];

    const results: Array<{ range: Range; threadId: string }> = [];

    for (const ann of annotations) {
        if (!ann.selection_text) continue;
        const targetOcc = ann.occurrenceIndex;
        let currentOcc = 0;
        let searchFrom = 0;
        let idx: number;

        while ((idx = fullText.indexOf(ann.selection_text, searchFrom)) !== -1) {
            const matchEnd = idx + ann.selection_text.length;

            if (targetOcc === null || currentOcc === targetOcc) {
                let startEntry: typeof textNodes[0] | null = null;
                let startOffset = 0;
                let endEntry: typeof textNodes[0] | null = null;
                let endOffset = 0;

                for (const entry of textNodes) {
                    if (!startEntry && entry.end > idx) {
                        startEntry = entry;
                        startOffset = idx - entry.start;
                    }
                    if (entry.end >= matchEnd) {
                        endEntry = entry;
                        endOffset = matchEnd - entry.start;
                        break;
                    }
                }

                if (startEntry && endEntry) {
                    try {
                        const range = document.createRange();
                        range.setStart(startEntry.node, startOffset);
                        range.setEnd(endEntry.node, Math.min(endOffset, endEntry.node.length));
                        results.push({ range, threadId: ann.threadId });
                    } catch {
                        // ignore invalid ranges (e.g. offset out of bounds after DOM mutation)
                    }
                }

                if (targetOcc !== null) break;
            }

            currentOcc++;
            searchFrom = matchEnd;
        }
    }

    return results;
}

interface AnnotatedPageProps {
    pageNumber: number;
    width: number;
    annotations: PageAnnotation[];
    onAnnotationClick?: (threadId: string, e: React.MouseEvent) => void;
}

const OVERLAY_CLASS = "pdf-annotation-overlay";

const AnnotatedPage = React.memo(function AnnotatedPage({ pageNumber, width, annotations, onAnnotationClick }: AnnotatedPageProps) {
    const pageRef = useRef<HTMLDivElement>(null);
    const timeoutRef = useRef<NodeJS.Timeout | null>(null);
    // Click-overlay rects (transparent divs, high z-index, pointer-events: auto)
    const [clickRects, setClickRects] = useState<Array<HighlightRect & { threadId: string }>>([]);

    const annotationsRef = useRef(annotations);
    annotationsRef.current = annotations;

    const annotationsKey = annotations
        .map(a => `${a.selection_text ?? ""}:${a.page ?? "_"}:${a.occurrenceIndex ?? "_"}`)
        .join("|");

    const doRecalc = useCallback(() => {
        const el = pageRef.current;
        if (!el || !el.querySelector(".react-pdf__Page__textContent")) return;

        // Remove previously injected visual highlights
        el.querySelectorAll(`.${OVERLAY_CLASS}`).forEach(n => n.remove());

        const pageInnerDiv = el.querySelector(".react-pdf__Page") as HTMLElement | null;
        const results = buildHighlightRanges(el, annotationsRef.current);
        const rects: Array<HighlightRect & { threadId: string }> = [];

        for (const { range, threadId } of results) {
            for (const r of range.getClientRects()) {
                if (r.width <= 0 || r.height <= 0) continue;

                // Visual highlight: injected inside .react-pdf__Page, before .textLayer
                // (z-index: 1 < textLayer z-index: 2) so the canvas text renders on top —
                // no colour interference with anti-aliased PDF glyphs.
                if (pageInnerDiv) {
                    const innerRect = pageInnerDiv.getBoundingClientRect();
                    const div = document.createElement("div");
                    div.className = `${OVERLAY_CLASS} annotation-highlight rounded-sm`;
                    div.style.cssText = `position:absolute;left:${r.left - innerRect.left}px;top:${r.top - innerRect.top}px;width:${r.width}px;height:${r.height}px;z-index:1;pointer-events:none;`;
                    const textLayer = pageInnerDiv.querySelector(".textLayer");
                    if (textLayer) pageInnerDiv.insertBefore(div, textLayer);
                    else pageInnerDiv.appendChild(div);
                }

                // Collect rects relative to pageRef for transparent click overlays
                const containerRect = el.getBoundingClientRect();
                rects.push({
                    x: r.left - containerRect.left,
                    y: r.top - containerRect.top,
                    w: r.width,
                    h: r.height,
                    threadId,
                });
            }
        }

        setClickRects(rects);
    }, []);

    const scheduleRecalc = useCallback(() => {
        if (timeoutRef.current) clearTimeout(timeoutRef.current);
        timeoutRef.current = setTimeout(doRecalc, 300);
    }, [doRecalc]);

    // eslint-disable-next-line react-hooks/exhaustive-deps
    useEffect(() => { scheduleRecalc(); }, [annotationsKey]);

    useEffect(() => {
        const el = pageRef.current;
        if (!el) return;
        const observer = new MutationObserver((mutations) => {
            // Ignore mutations caused by our own injected overlay elements
            const hasExternalChange = mutations.some(m =>
                Array.from(m.addedNodes).some(
                    n => !(n instanceof Element && n.classList.contains(OVERLAY_CLASS))
                )
            );
            if (hasExternalChange) scheduleRecalc();
        });
        observer.observe(el, { childList: true, subtree: true });
        scheduleRecalc();
        return () => {
            observer.disconnect();
            if (timeoutRef.current) clearTimeout(timeoutRef.current);
            el.querySelectorAll(`.${OVERLAY_CLASS}`).forEach(n => n.remove());
        };
    }, [scheduleRecalc]);

    return (
        <div ref={pageRef} style={{ position: "relative" }}>
            <Page
                pageNumber={pageNumber}
                width={width}
                renderTextLayer
                renderAnnotationLayer={false}
            />
            {/* Transparent click overlays — above everything, pointer-events: auto.
                onMouseDown preventDefault stops the browser from resetting the text
                selection state, which would otherwise cause the highlight to flash. */}
            {onAnnotationClick && clickRects.map((h, i) => (
                <div
                    key={i}
                    onMouseDown={e => e.preventDefault()}
                    onClick={e => onAnnotationClick(h.threadId, e)}
                    style={{
                        position: "absolute",
                        left: h.x,
                        top: h.y,
                        width: h.w,
                        height: h.h,
                        zIndex: 10,
                        cursor: "pointer",
                    }}
                />
            ))}
        </div>
    );
});

function LazyBlock({ estimatedHeight, scrollRootRef, children }: {
    estimatedHeight: number;
    scrollRootRef?: React.RefObject<HTMLElement | null>;
    children: React.ReactNode;
}) {
    const sentinelRef = useRef<HTMLDivElement>(null);
    const [isNear, setIsNear] = useState(false);

    useEffect(() => {
        const el = sentinelRef.current;
        if (!el) return;
        const rootAttr = scrollRootRef?.current ?? null;
        const io = new IntersectionObserver(
            ([entry]) => setIsNear(entry.isIntersecting),
            { root: rootAttr, rootMargin: "1200px 0px" }
        );
        io.observe(el);
        return () => io.disconnect();
    }, [scrollRootRef]);

    return (
        <div ref={sentinelRef} style={isNear ? undefined : { height: estimatedHeight }}>
            {isNear ? children : null}
        </div>
    );
}

export function PdfViewer({ materialId, fileKey, annotations = [] }: PdfViewerProps) {
    const t = useTranslations("Viewers");
    const scrollRef = useRef<HTMLDivElement>(null);

    const { blobUrl, loading, error } = useMaterialFile({
        materialId,
        fileKey,
        mode: "url",
    });

    const { zoom, setZoom } = usePinchZoom({
        initial: 100,
        min: MIN_ZOOM,
        max: MAX_ZOOM,
        step: ZOOM_STEP,
        targetRef: scrollRef,
        handleKeyboard: false,
    });

    const [numPages, setNumPages] = useState<number>(0);
    const [currentPage, setCurrentPage] = useState(1);
    const [containerWidth, setContainerWidth] = useState<number>(800);
    const [stableWidth, setStableWidth] = useState<number>(800);
    const [twoPageView, setTwoPageView] = useState(false);
    const [parseError, setParseError] = useState<string | null>(null);
    const [activeAnnotation, setActiveAnnotation] = useState<{
        thread: ThreadData;
        clientX: number;
        clientY: number;
    } | null>(null);

    const handleAnnotationClick = useCallback((threadId: string, e: React.MouseEvent) => {
        const thread = annotations.find((t) => t.root.id === threadId) ?? null;
        if (thread) {
            setActiveAnnotation({ thread, clientX: e.clientX, clientY: e.clientY });
        }
    }, [annotations]);

    // Debounce containerWidth into stableWidth
    useEffect(() => {
        const timer = setTimeout(() => {
            setStableWidth(containerWidth);
        }, 150);
        return () => clearTimeout(timer);
    }, [containerWidth]);

    useEffect(() => {
        const el = scrollRef.current;
        if (!el) return;
        let rafId: number;
        const ro = new ResizeObserver(entries => {
            cancelAnimationFrame(rafId);
            rafId = requestAnimationFrame(() => {
                const width = entries[0]?.contentRect.width;
                if (width && Math.abs(width - containerWidth) > 1) {
                    setContainerWidth(width);
                }
            });
        });
        ro.observe(el);
        return () => {
            ro.disconnect();
            cancelAnimationFrame(rafId);
        };
    }, [containerWidth]);

    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if ((e.ctrlKey || e.metaKey) && (e.key === "=" || e.key === "+")) {
                e.preventDefault();
                setZoom(z => Math.min(MAX_ZOOM, z + ZOOM_STEP));
            }
            if ((e.ctrlKey || e.metaKey) && e.key === "-") {
                e.preventDefault();
                setZoom(z => Math.max(MIN_ZOOM, z - ZOOM_STEP));
            }
            if ((e.ctrlKey || e.metaKey) && e.key === "0") {
                e.preventDefault();
                setZoom(100);
            }
        };
        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
    }, [setZoom]);

    const onDocumentLoadSuccess = useCallback(({ numPages }: { numPages: number }) => {
        setNumPages(numPages);
    }, []);

    const onDocumentLoadError = useCallback((err: Error) => {
        setParseError(err.message ?? t("pdf.failedToParse"));
    }, [t]);

    useEffect(() => {
        const scrollEl = scrollRef.current;
        if (!scrollEl || numPages === 0) return;

        const io = new IntersectionObserver(
            (entries) => {
                let best: { page: number; ratio: number; top: number } | null = null;
                for (const entry of entries) {
                    const page = Number((entry.target as HTMLElement).dataset.page);
                    if (!page) continue;
                    if (entry.isIntersecting) {
                        const top = entry.boundingClientRect.top;
                        if (!best || top < best.top) {
                            best = { page, ratio: entry.intersectionRatio, top };
                        }
                    }
                }
                if (best) setCurrentPage(best.page);
            },
            {
                root: scrollEl,
                rootMargin: "0px 0px -80% 0px",
                threshold: 0,
            }
        );

        const sentinels = scrollEl.querySelectorAll("[data-page]");
        sentinels.forEach((el) => io.observe(el));

        return () => io.disconnect();
    }, [numPages, zoom, twoPageView]);

    // We use stableWidth for the actual PDF rendering to avoid heavy rerenders during transitions.
    // If stableWidth !== containerWidth, we apply a CSS transform to scale the pages visually.
    const baseWidthStable = twoPageView ? (stableWidth - 32 - 16) / 2 : stableWidth - 32;
    const pageWidthStable = (baseWidthStable * zoom) / 100;

    const baseWidthActual = twoPageView ? (containerWidth - 32 - 16) / 2 : containerWidth - 32;
    const pageWidthActual = (baseWidthActual * zoom) / 100;

    const scale = pageWidthStable > 0 ? pageWidthActual / pageWidthStable : 1;
    const isResizing = Math.abs(scale - 1) > 0.001;

    // key on stable fields so memo doesn't invalidate every time the threads
    // array reference changes due to SSE/mutation state updates
    const annotationsKey = annotations
        .map((t) => {
            const occ = t.root.position_data?.occurrenceIndex;
            return `${t.root.id}:${t.root.selection_text ?? ""}:${t.root.page ?? "_"}:${typeof occ === "number" ? occ : "_"}`;
        })
        .join("|");
    const allAnnotations = React.useMemo(
        () => annotations.map((t) => ({
            selection_text: t.root.selection_text,
            page: t.root.page,
            occurrenceIndex: typeof t.root.position_data?.occurrenceIndex === "number"
                ? t.root.position_data.occurrenceIndex
                : null,
            threadId: t.root.id,
        })),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [annotationsKey]
    );

    const loadingSkeleton = (
        <div className="flex w-full flex-col items-center justify-start p-4 md:py-8">
            <div className="flex w-full max-w-4xl aspect-[1/1.414] flex-col rounded bg-white p-8 shadow-sm dark:bg-zinc-950/50">
                <Skeleton className="mb-12 h-10 w-3/4 rounded-md" />
                <div className="space-y-4">
                    <Skeleton className="h-4 w-full" />
                    <Skeleton className="h-4 w-[90%]" />
                    <Skeleton className="h-4 w-[95%]" />
                    <Skeleton className="h-4 w-full" />
                    <Skeleton className="h-4 w-[85%]" />
                </div>
            </div>
        </div>
    );

    return (
        <>
        <ViewerShell
            scrollRef={scrollRef}
            loading={loading}
            error={error || parseError}
            toolbarLeft={
                <button
                    onClick={() => setTwoPageView(!twoPageView)}
                    disabled={loading}
                    className={`rounded-md p-2 transition-colors disabled:opacity-40 ${twoPageView ? "bg-zinc-200 dark:bg-zinc-800 text-foreground" : "text-muted-foreground hover:bg-zinc-200 dark:hover:bg-zinc-800 hover:text-foreground"}`}
                    title={t("pdf.toggleTwoPage")}
                >
                    <BookOpen className="h-4 w-4" />
                </button>
            }
            toolbarCenter={
                numPages > 0 && (
                    <span className="text-xs tabular-nums text-muted-foreground">
                        {t("pdf.page", { current: currentPage, total: numPages })}
                    </span>
                )
            }
            toolbarRight={
                <>
                    <button
                        onClick={() => setZoom(z => Math.max(MIN_ZOOM, z - ZOOM_STEP))}
                        disabled={zoom <= MIN_ZOOM || loading}
                        className="rounded-md p-2 transition-colors text-muted-foreground hover:bg-zinc-200 dark:hover:bg-zinc-800 hover:text-foreground disabled:opacity-40"
                        title={t("pdf.zoomOut")}
                    >
                        <ZoomOut className="h-4 w-4" />
                    </button>
                    <button
                        onClick={() => setZoom(100)}
                        disabled={loading}
                        className="min-w-12 rounded-md px-2 py-1 text-center text-xs font-medium tabular-nums transition-colors hover:bg-zinc-200 dark:hover:bg-zinc-800 disabled:opacity-40"
                        title={t("pdf.resetZoom")}
                    >
                        {zoom}%
                    </button>
                    <button
                        onClick={() => setZoom(z => Math.min(MAX_ZOOM, z + ZOOM_STEP))}
                        disabled={zoom >= MAX_ZOOM || loading}
                        className="rounded-md p-2 transition-colors text-muted-foreground hover:bg-zinc-200 dark:hover:bg-zinc-800 hover:text-foreground disabled:opacity-40"
                        title={t("pdf.zoomIn")}
                    >
                        <ZoomIn className="h-4 w-4" />
                    </button>
                </>
            }
        >
            {!loading && !error && blobUrl && (
                <div
                    style={{
                        transform: isResizing ? `scale(${scale})` : undefined,
                        transformOrigin: "top center",
                        transition: isResizing ? "none" : "transform 0.2s ease-out",
                    }}
                >
                    <Document
                        file={blobUrl}
                        onLoadSuccess={onDocumentLoadSuccess}
                        onLoadError={onDocumentLoadError}
                        loading={loadingSkeleton}
                        className="py-4 px-4 w-max mx-auto flex flex-col items-center gap-4"
                    >
                    {twoPageView
                        ? Array.from({ length: Math.ceil(numPages / 2) }, (_, rowIdx) => {
                            const left = rowIdx * 2 + 1;
                            const right = rowIdx * 2 + 2;
                            const leftAnns = allAnnotations.filter(a => a.page === left || a.page == null);
                            const rightAnns = allAnnotations.filter(a => a.page === right || a.page == null);
                            return (
                                <LazyBlock key={rowIdx} estimatedHeight={pageWidthStable * 1.414} scrollRootRef={scrollRef}>
                                    <div className="grid grid-cols-2 gap-4 place-items-center">
                                        <div data-page={left}>
                                            <AnnotatedPage pageNumber={left} width={pageWidthStable} annotations={leftAnns} onAnnotationClick={handleAnnotationClick} />
                                        </div>
                                        {right <= numPages && (
                                            <div data-page={right}>
                                                <AnnotatedPage pageNumber={right} width={pageWidthStable} annotations={rightAnns} onAnnotationClick={handleAnnotationClick} />
                                            </div>
                                        )}
                                    </div>
                                </LazyBlock>
                            );
                        })
                        : Array.from({ length: numPages }, (_, i) => {
                            const pageNum = i + 1;
                            const pageAnnotations = allAnnotations.filter(a => a.page === pageNum || a.page == null);
                            return (
                                <div key={pageNum} data-page={pageNum}>
                                    <LazyBlock estimatedHeight={pageWidthStable * 1.414} scrollRootRef={scrollRef}>
                                        <AnnotatedPage pageNumber={pageNum} width={pageWidthStable} annotations={pageAnnotations} onAnnotationClick={handleAnnotationClick} />
                                    </LazyBlock>
                                </div>
                            );
                        })
                    }
                </Document>
                </div>
            )}
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
