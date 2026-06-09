"use client";

import React, { useEffect, useLayoutEffect, useRef, useState, useCallback, useMemo } from "react";
import { ZoomIn, ZoomOut, BookOpen } from "lucide-react";
import { Document, Page, pdfjs } from "react-pdf";
import { List, useListRef } from "react-window";
import type { RowComponentProps } from "react-window";
import { Skeleton } from "@/components/ui/skeleton";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import type { ThreadData } from "@/hooks/use-annotations";
import { usePinchZoom } from "@/hooks/use-pinch-zoom";
import { useMaterialFile } from "@/hooks/use-material-file";
import { ViewerShell } from "./viewer-shell";
import { AnnotationInlinePopover } from "@/components/annotations/annotation-inline-popover";
import { useTranslations } from "next-intl";

pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.js";

// Suppress known pdfjs noise
if (typeof window !== "undefined") {
    const originalError = console.error;
    const originalWarn = console.warn;
    const filterArgs = (args: unknown[]) => {
        const msg = args[0];
        if (typeof msg === "string" && (msg.includes("AbortException") || msg.includes("InvalidPDFException"))) return true;
        if (msg instanceof Error && (msg.name === "AbortException" || msg.name === "InvalidPDFException")) return true;
        return false;
    };
    console.error = (...args) => { if (!filterArgs(args)) originalError(...args); };
    console.warn = (...args) => { if (!filterArgs(args)) originalWarn(...args); };
}

const ZOOM_STEP = 25;
const MIN_ZOOM = 50;
const MAX_ZOOM = 300;
const PAGE_GAP = 16;
const DEFAULT_ASPECT = 1.414; // A4
const EMPTY_ROW_PROPS = {};

// ─── Types ───────────────────────────────────────────────────────────────────

interface PdfViewerProps {
    fileKey: string;
    materialId: string;
    annotations?: ThreadData[];
}

interface HighlightRect { x: number; y: number; w: number; h: number; }

interface PageAnnotation {
    selection_text: string | null;
    page: number | null;
    occurrenceIndex: number | null;
    threadId: string;
}

// ─── Annotation highlight builder (preserved) ────────────────────────────────

function buildHighlightRanges(
    pageEl: HTMLElement,
    annotations: PageAnnotation[],
): Array<{ range: Range; threadId: string }> {
    const textLayer = pageEl.querySelector(".react-pdf__Page__textContent");
    if (!textLayer) return [];

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
                    if (!startEntry && entry.end > idx) { startEntry = entry; startOffset = idx - entry.start; }
                    if (entry.end >= matchEnd) { endEntry = entry; endOffset = matchEnd - entry.start; break; }
                }

                if (startEntry && endEntry) {
                    try {
                        const range = document.createRange();
                        range.setStart(startEntry.node, startOffset);
                        range.setEnd(endEntry.node, Math.min(endOffset, endEntry.node.length));
                        results.push({ range, threadId: ann.threadId });
                    } catch { /* ignore */ }
                }
                if (targetOcc !== null) break;
            }
            currentOcc++;
            searchFrom = matchEnd;
        }
    }
    return results;
}

const OVERLAY_CLASS = "pdf-annotation-overlay";

interface HighlightBounds extends HighlightRect {
    threadId: string;
}

const AnnotatedPage = React.memo(function AnnotatedPage({
    pageNumber, width, annotations, onAnnotationClick, onLoadSuccess, isInteracting
}: {
    pageNumber: number;
    width: number;
    annotations: PageAnnotation[];
    onAnnotationClick?: (threadId: string, e: React.MouseEvent) => void;
    onLoadSuccess?: (pageNum: number, page: any) => void;
    isInteracting: boolean;
}) {
    const pageRef = useRef<HTMLDivElement>(null);
    const timeoutRef = useRef<NodeJS.Timeout | null>(null);
    const [clickRects, setClickRects] = useState<HighlightBounds[]>([]);
    const [shouldRenderTextLayer, setShouldRenderTextLayer] = useState(false);
    const annotationsRef = useRef(annotations);
    useEffect(() => { annotationsRef.current = annotations; });

    useEffect(() => {
        if (isInteracting) {
            setShouldRenderTextLayer(false);
            return;
        }
        const timer = setTimeout(() => setShouldRenderTextLayer(true), 150);
        return () => clearTimeout(timer);
    }, [isInteracting]);

    const annotationsKey = annotations
        .map(a => `${a.selection_text ?? ""}:${a.page ?? "_"}:${a.occurrenceIndex ?? "_"}`)
        .join("|");

    const doRecalc = useCallback(() => {
        const el = pageRef.current;
        if (!el) return;

        // Ensure the text layer is rendered before measuring
        if (!el.querySelector(".react-pdf__Page__textContent")) return;

        const containerRect = el.getBoundingClientRect();
        const results = buildHighlightRanges(el, annotationsRef.current);
        const rects: HighlightBounds[] = [];

        for (const { range, threadId } of results) {
            for (const r of range.getClientRects()) {
                if (r.width <= 0 || r.height <= 0) continue;
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
        timeoutRef.current = setTimeout(doRecalc, 150);
    }, [doRecalc]);

    useEffect(() => {
        scheduleRecalc();
        return () => {
            if (timeoutRef.current) clearTimeout(timeoutRef.current);
        };
    }, [annotationsKey, scheduleRecalc]);

    // Annotation highlights are recalculated via scheduleRecalc on page render/text-layer success.
    // We intentionally do NOT add a window resize listener here: the parent PdfViewer already
    // observes container width changes and re-renders pages with a new `width` prop, which
    // triggers onRenderSuccess → scheduleRecalc automatically. Adding a per-page window resize
    // listener would fire expensive DOM measurements (getBoundingClientRect / getClientRects)
    // for every visible page on every resize event, causing a crash during rapid resizing.

    const dpr = useMemo(() => {
        if (typeof window === "undefined") return 1;
        const rawDpr = window.devicePixelRatio || 1;
        const targetPixelWidth = width * rawDpr;
        if (targetPixelWidth > 2048) {
            return Math.max(0.5, 2048 / width);
        }
        return Math.min(2, rawDpr);
    }, [width]);

    return (
        <div ref={pageRef} style={{ position: "relative" }}>
            <Page
                pageNumber={pageNumber}
                width={width}
                devicePixelRatio={dpr}
                renderTextLayer={shouldRenderTextLayer}
                renderAnnotationLayer={false}
                onRenderSuccess={scheduleRecalc}
                onRenderTextLayerSuccess={scheduleRecalc}
                onLoadSuccess={page => onLoadSuccess?.(pageNumber, page)}
            />
            {/* Visual Highlights */}
            {clickRects.map((h, i) => (
                <div
                    key={`hl-${i}`}
                    className={`${OVERLAY_CLASS} annotation-highlight rounded-sm`}
                    style={{
                        position: "absolute",
                        left: h.x,
                        top: h.y,
                        width: h.w,
                        height: h.h,
                        zIndex: 1,
                        pointerEvents: "none",
                    }}
                />
            ))}
            {/* Click Targets */}
            {onAnnotationClick && clickRects.map((h, i) => (
                <div
                    key={`click-${i}`}
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

// ─── Shared context for the row component ────────────────────────────────────
// react-window v2 requires a top-level `rowComponent`, so we pass dynamic data
// through a context to avoid re-creating the component on every render.

interface RowContext {
    twoPageView: boolean;
    numPages: number;
    pageWidthCommitted: number;
    containerWidth: number;
    cssScale: number;
    isScaling: boolean;
    allAnnotations: PageAnnotation[];
    handleAnnotationClick: (threadId: string, e: React.MouseEvent) => void;
    onPageLoadSuccess: (pageNum: number, page: any) => void;
    innerWidth: number;
    scrollContainerRef: React.RefObject<HTMLDivElement | null>;
}

const RowCtx = React.createContext<RowContext>(null!);

const CustomScrollContainer = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(({ children, style, ...rest }, ref) => {
    const { cssScale, isScaling, innerWidth, scrollContainerRef } = React.useContext(RowCtx);
    const [origin, setOrigin] = useState("50% 50%");
    
    useLayoutEffect(() => {
        if (isScaling && scrollContainerRef.current) {
            setOrigin(prev => {
                if (prev.includes("px")) return prev;
                const el = scrollContainerRef.current!;
                const y = el.scrollTop + el.clientHeight / 2;
                const x = el.scrollLeft + el.clientWidth / 2;
                return `${x}px ${y}px`;
            });
        } else {
            setOrigin("50% 50%");
        }
    }, [isScaling, scrollContainerRef]);

    return (
        <div
            ref={ref}
            style={style}
            {...rest}
        >
            <div
                style={{
                    position: "relative",
                    width: innerWidth,
                    minWidth: "100%",
                    transform: isScaling ? `scale(${cssScale})` : undefined,
                    transformOrigin: origin,
                    transition: isScaling ? "none" : "transform 0.15s ease-out",
                }}
            >
                {children}
            </div>
        </div>
    );
});
CustomScrollContainer.displayName = "CustomScrollContainer";

function PdfRow({ index, style, isScrolling }: RowComponentProps<object> & { isScrolling?: boolean }) {
    const { twoPageView, numPages, pageWidthCommitted, allAnnotations, handleAnnotationClick, onPageLoadSuccess } = React.useContext(RowCtx);
    const isInteracting = !!isScrolling;
    const padding = 16;
    
    // Unconditionally compute for single and two-page views to satisfy rules of hooks
    const pageNum = index + 1;
    const leftPage = index * 2 + 1;
    const rightPage = index * 2 + 2;
    
    const pageAnns = React.useMemo(() => allAnnotations.filter(a => a.page === pageNum || a.page == null), [allAnnotations, pageNum]);
    const leftAnns = React.useMemo(() => allAnnotations.filter(a => a.page === leftPage || a.page == null), [allAnnotations, leftPage]);
    const rightAnns = React.useMemo(() => allAnnotations.filter(a => a.page === rightPage || a.page == null), [allAnnotations, rightPage]);

    if (twoPageView) {
        return (
            <div style={style} data-page={leftPage}>
                <div style={{
                    display: "flex",
                    width: "max-content", margin: "0 auto", justifyContent: "center",
                    alignItems: "flex-start",
                    gap: PAGE_GAP, padding: `${PAGE_GAP / 2}px ${padding}px`,
                }}>
                    <AnnotatedPage pageNumber={leftPage} width={pageWidthCommitted} annotations={leftAnns} onAnnotationClick={handleAnnotationClick} onLoadSuccess={onPageLoadSuccess} isInteracting={isInteracting} />
                    {rightPage <= numPages && (
                        <AnnotatedPage pageNumber={rightPage} width={pageWidthCommitted} annotations={rightAnns} onAnnotationClick={handleAnnotationClick} onLoadSuccess={onPageLoadSuccess} isInteracting={isInteracting} />
                    )}
                </div>
            </div>
        );
    }

    return (
        <div style={style} data-page={pageNum}>
            <div style={{
                display: "flex",
                width: "max-content", margin: "0 auto", justifyContent: "center",
                padding: `${PAGE_GAP / 2}px ${padding}px`,
            }}>
                <AnnotatedPage pageNumber={pageNum} width={pageWidthCommitted} annotations={pageAnns} onAnnotationClick={handleAnnotationClick} onLoadSuccess={onPageLoadSuccess} isInteracting={isInteracting} />
            </div>
        </div>
    );
}

// ─── Main PdfViewer ──────────────────────────────────────────────────────────

export function PdfViewer({ materialId, fileKey, annotations = [] }: PdfViewerProps) {
    const t = useTranslations("Viewers");
    const shellScrollRef = useRef<HTMLDivElement>(null);
    const listRef = useListRef(null);

    const { blobUrl, loading, error, reload } = useMaterialFile({ materialId, fileKey, mode: "blob" });

    // The list's outer element is the actual scroll container for pinch-zoom
    const listOuterRef = useRef<HTMLDivElement>(null);

    // We need a stable ref for usePinchZoom that updates once the list mounts
    const pinchTargetRef = useRef<HTMLDivElement>(null);
    useEffect(() => {
        // After list mounts, grab its outer element
        const el = listRef.current?.element ?? null;
        if (el) {
            pinchTargetRef.current = el;
            listOuterRef.current = el;
        }
    });

    const { zoom, setZoom } = usePinchZoom({
        initial: 100, min: MIN_ZOOM, max: MAX_ZOOM, step: ZOOM_STEP,
        targetRef: shellScrollRef, // pinch zoom targets the shell scroll container
        handleKeyboard: false,
    });

    const [numPages, setNumPages] = useState(0);
    const [currentPage, setCurrentPage] = useState(1);
    const [containerWidth, setContainerWidth] = useState(800);
    const [twoPageView, setTwoPageView] = useState(false);
    const [parseError, setParseError] = useState<string | null>(null);
    const [activeAnnotation, setActiveAnnotation] = useState<{
        thread: ThreadData; clientX: number; clientY: number;
    } | null>(null);

    // ── Page dimensions / Dynamic aspect ratio updates ───────────────────────
    const pageAspectsRef = useRef<Map<number, number>>(new Map());
    const [aspectsReady, setAspectsReady] = useState(false);
    const [aspectsVersion, setAspectsVersion] = useState(0);

    // ── Committed zoom (debounced) ───────────────────────────────────────────
    const [committedZoom, setCommittedZoom] = useState(100);
    const scrollAnchorRef = useRef<{ ratio: number } | null>(null);

    useEffect(() => {
        const timer = setTimeout(() => {
            const el = listRef.current?.element ?? shellScrollRef.current;
            if (el && el.scrollHeight > el.clientHeight) {
                scrollAnchorRef.current = { ratio: el.scrollTop / (el.scrollHeight - el.clientHeight) };
            }
            setCommittedZoom(zoom);
        }, 350);
        return () => clearTimeout(timer);
    }, [zoom]);

    useLayoutEffect(() => {
        const el = listRef.current?.element ?? shellScrollRef.current;
        const anchor = scrollAnchorRef.current;
        if (!el || !anchor) return;
        const newMax = el.scrollHeight - el.clientHeight;
        // eslint-disable-next-line react-hooks/immutability
        if (newMax > 0) el.scrollTop = anchor.ratio * newMax;
        scrollAnchorRef.current = null;
    }, [committedZoom, twoPageView, containerWidth]);

    // ── Container resize ─────────────────────────────────────────────────────
    // Debounce the ResizeObserver so that rapid/continuous resizing (e.g. dragging
    // Firefox responsive-mode handles) only triggers one re-render after the resize
    // settles, instead of firing on every animation frame and crashing the tab.
    useEffect(() => {
        const el = shellScrollRef.current;
        if (!el) return;
        let rafId: number | null = null;
        let pendingWidth: number | null = null;
        const ro = new ResizeObserver(entries => {
            const w = entries[0]?.contentRect.width;
            if (!w || w <= 0) return;
            pendingWidth = w;
            if (rafId !== null) return; // already scheduled
            rafId = requestAnimationFrame(() => {
                rafId = null;
                if (pendingWidth !== null) {
                    const scrollEl = listRef.current?.element ?? shellScrollRef.current;
                    if (scrollEl && scrollEl.scrollHeight > scrollEl.clientHeight) {
                        scrollAnchorRef.current = { ratio: scrollEl.scrollTop / (scrollEl.scrollHeight - scrollEl.clientHeight) };
                    }
                    setContainerWidth(pendingWidth);
                    pendingWidth = null;
                }
            });
        });
        ro.observe(el);
        return () => {
            ro.disconnect();
            if (rafId !== null) cancelAnimationFrame(rafId);
        };
    }, []);



    const onDocumentLoadSuccess = useCallback((pdf: { numPages: number }) => {
        setParseError(null);
        setNumPages(pdf.numPages);
        // Pre-fill all pages with the default A4 aspect ratio immediately so the
        // list can render right away. Individual pages will update their true aspect
        // ratio lazily via onPageLoadSuccess as they are rendered into view.
        const aspects = new Map<number, number>();
        for (let i = 1; i <= pdf.numPages; i++) {
            aspects.set(i, DEFAULT_ASPECT);
        }
        pageAspectsRef.current = aspects;
        setAspectsReady(true);
    }, []);

    const onPageLoadSuccess = useCallback((pageNum: number, page: any) => {
        const vp = page.getViewport({ scale: 1 });
        const aspect = vp.height / vp.width;
        const currentAspect = pageAspectsRef.current.get(pageNum);
        if (currentAspect !== aspect) {
            pageAspectsRef.current.set(pageNum, aspect);
            setAspectsVersion(v => v + 1);
        }
    }, []);

    const onDocumentLoadError = useCallback((err: Error) => {
        setParseError(err.message ?? t("pdf.failedToParse"));
    }, [t]);

    // ── Keyboard Navigation and Zoom ────────────────────────────────────────
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            const target = e.target as HTMLElement | null;
            if (
                target &&
                (target.tagName === "INPUT" ||
                    target.tagName === "TEXTAREA" ||
                    target.tagName === "SELECT" ||
                    target.isContentEditable)
            ) {
                return;
            }

            // Zoom shortcuts (Ctrl/Cmd + keys)
            if (e.ctrlKey || e.metaKey) {
                if (e.key === "=" || e.key === "+") {
                    e.preventDefault();
                    setZoom(z => Math.min(MAX_ZOOM, z + ZOOM_STEP));
                    return;
                }
                if (e.key === "-") {
                    e.preventDefault();
                    setZoom(z => Math.max(MIN_ZOOM, z - ZOOM_STEP));
                    return;
                }
                if (e.key === "0") {
                    e.preventDefault();
                    setZoom(100);
                    return;
                }
            }

            // Page navigation shortcuts (no modifier keys allowed)
            if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) {
                return;
            }

            if (e.key === "ArrowRight" || e.key === "d" || e.key === "D") {
                e.preventDefault();
                if (twoPageView) {
                    const currentRow = Math.floor((currentPage - 1) / 2);
                    const totalRows = Math.ceil(numPages / 2);
                    if (currentRow + 1 < totalRows) {
                        listRef.current?.scrollToRow({ align: "start", index: currentRow + 1 });
                    }
                } else {
                    if (currentPage < numPages) {
                        listRef.current?.scrollToRow({ align: "start", index: currentPage });
                    }
                }
            } else if (e.key === "ArrowLeft" || e.key === "q" || e.key === "Q") {
                e.preventDefault();
                if (twoPageView) {
                    const currentRow = Math.floor((currentPage - 1) / 2);
                    if (currentRow > 0) {
                        listRef.current?.scrollToRow({ align: "start", index: currentRow - 1 });
                    }
                } else {
                    if (currentPage > 1) {
                        listRef.current?.scrollToRow({ align: "start", index: currentPage - 2 });
                    }
                }
            }
        };
        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
    }, [setZoom, currentPage, numPages, twoPageView, listRef]);

    // ── Annotations ──────────────────────────────────────────────────────────
    const handleAnnotationClick = useCallback((threadId: string, e: React.MouseEvent) => {
        const thread = annotations.find(t => t.root.id === threadId) ?? null;
        if (thread) setActiveAnnotation({ thread, clientX: e.clientX, clientY: e.clientY });
    }, [annotations]);

    const annotationsKey = annotations
        .map(t => {
            const occ = t.root.position_data?.occurrenceIndex;
            return `${t.root.id}:${t.root.selection_text ?? ""}:${t.root.page ?? "_"}:${typeof occ === "number" ? occ : "_"}`;
        })
        .join("|");

    const allAnnotations = useMemo(
        () => annotations.map(t => ({
            selection_text: t.root.selection_text,
            page: t.root.page,
            occurrenceIndex: typeof t.root.position_data?.occurrenceIndex === "number" ? t.root.position_data.occurrenceIndex : null,
            threadId: t.root.id,
        })),
        [annotationsKey, annotations],
    );

    // ── Computed dimensions ──────────────────────────────────────────────────
    const padding = 16;
    const baseWidth = twoPageView ? (containerWidth - padding * 2 - PAGE_GAP) / 2 : containerWidth - padding * 2;
    const pageWidthCommitted = Math.max(100, (baseWidth * committedZoom) / 100);
    const cssScale = committedZoom > 0 ? zoom / committedZoom : 1;
    const isScaling = Math.abs(cssScale - 1) > 0.001;

    const contentWidth = twoPageView 
        ? (pageWidthCommitted * 2) + PAGE_GAP + padding * 2
        : pageWidthCommitted + padding * 2;
    const innerWidth = Math.max(containerWidth, contentWidth);

    // ── react-window config ──────────────────────────────────────────────────
    const itemCount = twoPageView ? Math.ceil(numPages / 2) : numPages;

    const getRowHeight = useCallback((index: number): number => {
        if (twoPageView) {
            const l = index * 2 + 1;
            const r = index * 2 + 2;
            const la = pageAspectsRef.current.get(l) ?? DEFAULT_ASPECT;
            const ra = r <= numPages ? (pageAspectsRef.current.get(r) ?? DEFAULT_ASPECT) : la;
            return pageWidthCommitted * Math.max(la, ra) + PAGE_GAP;
        }
        const aspect = pageAspectsRef.current.get(index + 1) ?? DEFAULT_ASPECT;
        return pageWidthCommitted * aspect + PAGE_GAP;
    }, [numPages, pageWidthCommitted, twoPageView, aspectsVersion]);


    // ── Page tracking ────────────────────────────────────────────────────────
    const handleRowsRendered = useCallback((visible: { startIndex: number }) => {
        const page = twoPageView ? visible.startIndex * 2 + 1 : visible.startIndex + 1;
        setCurrentPage(page);
    }, [twoPageView]);

    // ── Row context ──────────────────────────────────────────────────────────
    const rowCtxValue = useMemo<RowContext>(() => ({
        twoPageView, numPages, pageWidthCommitted, containerWidth, cssScale, isScaling, allAnnotations, handleAnnotationClick, onPageLoadSuccess, innerWidth, scrollContainerRef: listOuterRef,
    }), [twoPageView, numPages, pageWidthCommitted, containerWidth, cssScale, isScaling, allAnnotations, handleAnnotationClick, onPageLoadSuccess, innerWidth, listOuterRef]);

    // ── Loading skeleton ─────────────────────────────────────────────────────
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
            scrollRef={shellScrollRef}
            loading={loading}
            error={error || parseError}
            onRetry={() => {
                setParseError(null);
                reload();
            }}
            toolbarLeft={
                <button
                    onClick={() => {
                        const el = listRef.current?.element ?? shellScrollRef.current;
                        if (el && el.scrollHeight > el.clientHeight) {
                            scrollAnchorRef.current = { ratio: el.scrollTop / (el.scrollHeight - el.clientHeight) };
                        }
                        setTwoPageView(!twoPageView);
                    }}
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
                <Document
                    file={blobUrl}
                    onLoadSuccess={onDocumentLoadSuccess}
                    onLoadError={onDocumentLoadError}
                    loading={loadingSkeleton}
                    className="h-full w-full"
                >
                    {numPages > 0 && aspectsReady && (
                        <RowCtx.Provider value={rowCtxValue}>
                            <List
                                listRef={listRef}
                                rowCount={itemCount}
                                rowHeight={getRowHeight}
                                rowComponent={PdfRow}
                                rowProps={EMPTY_ROW_PROPS}
                                tagName={CustomScrollContainer as any}
                                overscanCount={1}
                                onRowsRendered={handleRowsRendered}
                                className="h-full w-full bg-zinc-200 dark:bg-zinc-800/50"
                            />
                        </RowCtx.Provider>
                    )}
                </Document>
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
