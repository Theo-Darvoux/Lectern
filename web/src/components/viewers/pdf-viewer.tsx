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
    onAnnotationClick?: () => void;
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
}

function buildHighlights(pageEl: HTMLElement, annotations: PageAnnotation[]): HighlightRect[] {
    const textLayer = pageEl.querySelector(".react-pdf__Page__textContent");
    if (!textLayer) return [];

    const spans = Array.from(textLayer.querySelectorAll("span")).filter(
        s => (s.textContent || "").length > 0
    );
    if (spans.length === 0) return [];

    const pageRect = pageEl.getBoundingClientRect();
    if (pageRect.width === 0) return [];

    // Pre-calculate text content and offsets to avoid multiple DOM reads in the loop
    let fullText = "";
    const spanRanges: { start: number; end: number; el: HTMLElement }[] = [];
    for (const span of spans) {
        const t = span.textContent || "";
        spanRanges.push({ start: fullText.length, end: fullText.length + t.length, el: span });
        fullText += t;
    }

    const highlights: HighlightRect[] = [];
    for (const ann of annotations) {
        if (!ann.selection_text) continue;
        let searchFrom = 0;
        let idx: number;
        while ((idx = fullText.indexOf(ann.selection_text, searchFrom)) !== -1) {
            const matchEnd = idx + ann.selection_text.length;
            for (const { start, end, el } of spanRanges) {
                if (end <= idx || start >= matchEnd) continue;
                
                // This call still triggers a reflow, but we've minimized the number of calls 
                // and we're doing them on a debounced schedule.
                const r = el.getBoundingClientRect();
                if (r.width === 0) continue;
                highlights.push({
                    x: r.left - pageRect.left,
                    y: r.top - pageRect.top,
                    w: r.width,
                    h: r.height,
                });
            }
            searchFrom = matchEnd;
        }
    }
    return highlights;
}

function highlightsEqual(a: HighlightRect[], b: HighlightRect[]): boolean {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) {
        if (
            Math.abs(a[i].x - b[i].x) > 0.5 ||
            Math.abs(a[i].y - b[i].y) > 0.5 ||
            Math.abs(a[i].w - b[i].w) > 0.5 ||
            Math.abs(a[i].h - b[i].h) > 0.5
        ) {
            return false;
        }
    }
    return true;
}

interface AnnotatedPageProps {
    pageNumber: number;
    width: number;
    annotations: PageAnnotation[];
    onAnnotationClick?: () => void;
}

const AnnotatedPage = React.memo(function AnnotatedPage({ pageNumber, width, annotations, onAnnotationClick }: AnnotatedPageProps) {
    const pageRef = useRef<HTMLDivElement>(null);
    const [highlights, setHighlights] = useState<HighlightRect[]>([]);
    const timeoutRef = useRef<NodeJS.Timeout | null>(null);

    const scheduleRecalc = useCallback(() => {
        if (timeoutRef.current) clearTimeout(timeoutRef.current);
        
        // Use a longer debounce (300ms) to avoid fighting with pdf.js during the heavy rendering phase.
        timeoutRef.current = setTimeout(() => {
            const el = pageRef.current;
            if (!el) return;
            
            // Check if the text layer is actually there before doing heavy work
            if (!el.querySelector(".react-pdf__Page__textContent")) return;

            const next = buildHighlights(el, annotations);
            setHighlights(prev => highlightsEqual(prev, next) ? prev : next);
        }, 300);
    }, [annotations]);

    useEffect(() => {
        const el = pageRef.current;
        if (!el) return;
        
        // Only observe child additions to the text layer, which is less frequent than style/attribute changes.
        const observer = new MutationObserver((mutations) => {
            if (mutations.some(m => m.addedNodes.length > 0)) {
                scheduleRecalc();
            }
        });

        observer.observe(el, { childList: true, subtree: true });
        scheduleRecalc();

        return () => {
            observer.disconnect();
            if (timeoutRef.current) clearTimeout(timeoutRef.current);
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
            {highlights.map((h, i) => (
                <div
                    key={i}
                    onClick={onAnnotationClick}
                    style={{
                        position: "absolute",
                        left: h.x,
                        top: h.y,
                        width: h.w,
                        height: h.h,
                        backgroundColor: "rgba(255, 213, 0, 0.4)",
                        mixBlendMode: "multiply",
                        zIndex: 4,
                        cursor: onAnnotationClick ? "pointer" : "default",
                        pointerEvents: onAnnotationClick ? "auto" : "none",
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

export function PdfViewer({ materialId, fileKey, annotations = [], onAnnotationClick }: PdfViewerProps) {
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

    const allAnnotations = React.useMemo(() => annotations.map(t => ({
        selection_text: t.root.selection_text,
        page: t.root.page,
    })), [annotations]);

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
                                            <AnnotatedPage pageNumber={left} width={pageWidthStable} annotations={leftAnns} onAnnotationClick={onAnnotationClick} />
                                        </div>
                                        {right <= numPages && (
                                            <div data-page={right}>
                                                <AnnotatedPage pageNumber={right} width={pageWidthStable} annotations={rightAnns} onAnnotationClick={onAnnotationClick} />
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
                                        <AnnotatedPage pageNumber={pageNum} width={pageWidthStable} annotations={pageAnnotations} onAnnotationClick={onAnnotationClick} />
                                    </LazyBlock>
                                </div>
                            );
                        })
                    }
                </Document>
                </div>
            )}
        </ViewerShell>
    );
}
