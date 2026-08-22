"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy, PDFDocumentLoadingTask } from "pdfjs-dist";
import type {
    PDFViewer as PdfJsViewer,
    EventBus as PdfJsEventBus,
    PDFLinkService as PdfJsLinkService,
} from "pdfjs-dist/web/pdf_viewer.mjs";

// Static CSS import is SSR-safe (no JS evaluation). The pdf.js *JavaScript* must
// stay out of the module graph until runtime — `pdfjs-dist` calls
// `Promise.withResolvers()` at evaluation time, which throws under Next SSR — so
// it is imported dynamically inside the effect below.
import "pdfjs-dist/web/pdf_viewer.css";
import { createPdfWorker } from "@/lib/pdf-worker";
import { useConfigStore } from "@/lib/stores";
import type { PDFWorker as PdfJsWorker } from "pdfjs-dist";

// Suppress known pdf.js console noise (cancelled renders / aborted loads). These
// are handled in the hook's error path; pdf.js still logs them unprompted.
if (typeof window !== "undefined") {
    const orig = { error: console.error, warn: console.warn };
    const isNoise = (args: unknown[]) => {
        const msg = args[0];
        if (typeof msg === "string" && (msg.includes("AbortException") || msg.includes("InvalidPDFException"))) return true;
        if (msg instanceof Error && (msg.name === "AbortException" || msg.name === "InvalidPDFException")) return true;
        return false;
    };
    console.error = (...args) => { if (!isNoise(args)) orig.error(...args); };
    console.warn = (...args) => { if (!isNoise(args)) orig.warn(...args); };
}

// pdf.js TextLayerMode.ENABLE / AnnotationMode values.
const TEXT_LAYER_ENABLE = 1;
const ANNOTATION_DISABLE = 0;
const ANNOTATION_ENABLE = 1;

const PRESET_SCALES = new Set(["auto", "page-actual", "page-width", "page-fit", "page-height"]);
/** Delay (ms) before pdf.js re-rasterises during a continuous gesture. While the
 *  timer runs pdf.js keeps the current canvas and applies a CSS transform, so the
 *  zoom is smooth and never flickers; the crisp re-render swaps in afterwards. */
const GESTURE_DRAWING_DELAY = 400;
const PDF_ENGINE_TIMEOUT_MS = 45_000;
const PDF_DOCUMENT_TIMEOUT_MS = 60_000;

export type PdfSpread = "none" | "odd" | "even";
const SPREAD_VALUE: Record<PdfSpread, number> = { none: 0, odd: 1, even: 2 };

export type PdfStatus = "loading" | "ready" | "error";

interface UsePdfjsDocumentOptions {
    /** Object/blob URL of the PDF. `null` while the upstream fetch is in flight. */
    url: string | null;
    /** Optional initial page number to navigate to upon loading */
    initialPage?: number;
    /** Enable ctrl/⌘+wheel and two-finger pinch zoom on the container. Default true. */
    enableGestures?: boolean;
    /** Called whenever a page's text layer (re)renders — used to (re)paint annotation overlays. */
    onTextLayerRendered?: (pageNumber: number, pageDiv: HTMLDivElement) => void;
}

interface UsePdfjsDocumentReturn {
    /** Attach to the scroll container. MUST be `position: absolute` (pdf.js throws otherwise). */
    containerRef: React.RefObject<HTMLDivElement | null>;
    /** Attach to the inner `.pdfViewer` element. */
    viewerElRef: React.RefObject<HTMLDivElement | null>;
    status: PdfStatus;
    error: string | null;
    numPages: number;
    currentPage: number;
    /** Current zoom as a whole percentage (currentScale * 100). */
    scalePercent: number;
    zoomIn: () => void;
    zoomOut: () => void;
    /** Re-fit the page to the container width. */
    resetZoom: () => void;
    goToPage: (pageNumber: number) => void;
    setSpread: (spread: PdfSpread) => void;
    reload: () => void;
}

interface TextLayerRenderedEvent {
    pageNumber: number;
    source: { div: HTMLDivElement };
}

/**
 * Headless wrapper around pdf.js's own `PDFViewer` (the engine behind the
 * Firefox PDF reader). The viewer handles virtualisation, text selection, and —
 * crucially — zoom: it applies an instant CSS transform and double-buffers the
 * re-render, so zooming never flickers or jumps. This replaces the previous
 * `react-pdf` + `react-window` stack and its debounced re-render machinery.
 */
export function usePdfjsDocument({
    url,
    initialPage,
    enableGestures = true,
    onTextLayerRendered,
}: UsePdfjsDocumentOptions): UsePdfjsDocumentReturn {
    const allowExternalLinks = useConfigStore(
        (state) => state.config?.allow_external_document_links !== false,
    );
    const containerRef = useRef<HTMLDivElement>(null);
    const viewerElRef = useRef<HTMLDivElement>(null);

    const pdfjsRef = useRef<typeof import("pdfjs-dist") | null>(null);
    const viewerRef = useRef<PdfJsViewer | null>(null);
    const linkServiceRef = useRef<PdfJsLinkService | null>(null);
    const eventBusRef = useRef<PdfJsEventBus | null>(null);
    const docRef = useRef<PDFDocumentProxy | null>(null);
    const loadingTaskRef = useRef<PDFDocumentLoadingTask | null>(null);
    // Dedicated module worker for this viewer, created once alongside the engine
    // and reused across reloads. We own its lifecycle (see cleanup below); since
    // it's passed explicitly to getDocument, pdf.js never destroys it for us.
    const workerRef = useRef<Worker | null>(null);
    const pdfWorkerRef = useRef<PdfJsWorker | null>(null);

    // Keep the latest callbacks and initialPage without re-running the init effect.
    const onTextLayerRef = useRef(onTextLayerRendered);
    useEffect(() => { onTextLayerRef.current = onTextLayerRendered; });
    const initialPageRef = useRef(initialPage);
    useEffect(() => { initialPageRef.current = initialPage; });

    const [modulesReady, setModulesReady] = useState(false);
    const [status, setStatus] = useState<PdfStatus>("loading");
    const [error, setError] = useState<string | null>(null);
    const [numPages, setNumPages] = useState(0);
    const [currentPage, setCurrentPage] = useState(1);
    const [scalePercent, setScalePercent] = useState(100);
    const [reloadNonce, setReloadNonce] = useState(0);
    const [engineNonce, setEngineNonce] = useState(0);

    // ── Initialise the imperative viewer once ────────────────────────────────
    useEffect(() => {
        let destroyed = false;
        const container = containerRef.current;
        const viewerEl = viewerElRef.current;
        if (!container || !viewerEl) return;

        setStatus("loading");
        setError(null);
        const initTimeout = window.setTimeout(() => {
            if (destroyed) return;
            setError("PDF viewer initialization timed out. Please retry.");
            setStatus("error");
        }, PDF_ENGINE_TIMEOUT_MS);

        (async () => {
            const pdfjs = await import("pdfjs-dist");
            if (destroyed) return;

            // pdf.js v4 components (like PDFViewer) and its "fake worker" fallback
            // expect `pdfjsLib` to be available on the global scope in many
            // environments. If a worker fails to start, the fallback engine
            // crashes immediately without this.
            (globalThis as any).pdfjsLib = pdfjs;

            const viewerMod = await import("pdfjs-dist/web/pdf_viewer.mjs");
            if (destroyed) return;

            const worker = createPdfWorker();
            workerRef.current = worker;
            pdfWorkerRef.current = pdfjs.PDFWorker.fromPort({ port: worker });

            const eventBus = new viewerMod.EventBus();
            const linkService = new viewerMod.PDFLinkService({
                eventBus,
                externalLinkTarget: viewerMod.LinkTarget.BLANK,
                externalLinkRel: "noopener noreferrer nofollow",
            });
            const viewer = new viewerMod.PDFViewer({
                container,
                viewer: viewerEl,
                eventBus,
                linkService,
                textLayerMode: TEXT_LAYER_ENABLE,
                annotationMode: allowExternalLinks ? ANNOTATION_ENABLE : ANNOTATION_DISABLE,
            });
            linkService.setViewer(viewer);

            eventBus.on("pagesinit", () => {
                // Fit to width by default; pdf.js keeps re-fitting on resize while
                // the scale value stays a preset (see the resize observer below).
                viewer.currentScaleValue = "page-width";
                if (typeof initialPageRef.current === "number" && initialPageRef.current > 1) {
                    viewer.currentPageNumber = initialPageRef.current;
                }
            });
            eventBus.on("pagechanging", (e: { pageNumber: number }) => {
                setCurrentPage(e.pageNumber);
            });
            eventBus.on("scalechanging", (e: { scale: number }) => {
                setScalePercent(Math.round(e.scale * 100));
            });
            eventBus.on("textlayerrendered", (e: TextLayerRenderedEvent) => {
                onTextLayerRef.current?.(e.pageNumber, e.source.div);
            });

            pdfjsRef.current = pdfjs;
            eventBusRef.current = eventBus;
            linkServiceRef.current = linkService;
            viewerRef.current = viewer;
            window.clearTimeout(initTimeout);
            setModulesReady(true);
        })().catch((err) => {
            window.clearTimeout(initTimeout);
            if (destroyed) return;
            setModulesReady(false);
            setError(err instanceof Error ? err.message : "Failed to initialize PDF viewer");
            setStatus("error");
        });

        return () => {
            destroyed = true;
            window.clearTimeout(initTimeout);
            loadingTaskRef.current?.destroy();
            loadingTaskRef.current = null;
            docRef.current?.destroy();
            docRef.current = null;
            viewerRef.current?.cleanup();
            viewerRef.current = null;
            linkServiceRef.current = null;
            eventBusRef.current = null;
            pdfjsRef.current = null;
            // We own the worker (getDocument got it explicitly, so pdf.js won't
            // destroy it): tear down the PDFWorker wrapper then the worker.
            pdfWorkerRef.current?.destroy();
            pdfWorkerRef.current = null;
            workerRef.current?.terminate();
            workerRef.current = null;
            setModulesReady(false);
        };
    }, [engineNonce, allowExternalLinks]);

    // ── Load (or reload) the document ────────────────────────────────────────
    useEffect(() => {
        const pdfjs = pdfjsRef.current;
        const viewer = viewerRef.current;
        const linkService = linkServiceRef.current;
        if (!modulesReady || !pdfjs || !viewer || !linkService || !url) return;

        let cancelled = false;
        setStatus("loading");
        setError(null);

        const task = pdfjs.getDocument({ url, worker: pdfWorkerRef.current ?? undefined });
        loadingTaskRef.current = task;
        let timedOut = false;
        const loadTimeout = window.setTimeout(() => {
            if (cancelled) return;
            timedOut = true;
            setError("PDF document loading timed out. Please retry.");
            setStatus("error");
            void task.destroy();
        }, PDF_DOCUMENT_TIMEOUT_MS);

        task.promise.then(
            (doc) => {
                window.clearTimeout(loadTimeout);
                if (cancelled || timedOut) { doc.destroy(); return; }
                docRef.current?.destroy();
                docRef.current = doc;
                viewer.setDocument(doc);
                linkService.setDocument(doc, null);
                setNumPages(doc.numPages);
                setStatus("ready");
            },
            (err: Error) => {
                window.clearTimeout(loadTimeout);
                if (cancelled || timedOut || err?.name === "AbortException") return;
                setError(err?.message ?? "Failed to load PDF");
                setStatus("error");
            },
        );

        return () => {
            cancelled = true;
            window.clearTimeout(loadTimeout);
            task.destroy();
        };
    }, [url, modulesReady, reloadNonce]);

    // ── Re-fit to width on container resize (only while not manually zoomed) ──
    useEffect(() => {
        const container = containerRef.current;
        if (!container) return;
        let raf: number | null = null;
        const ro = new ResizeObserver(() => {
            if (raf !== null) return;
            raf = requestAnimationFrame(() => {
                raf = null;
                const viewer = viewerRef.current;
                if (!viewer) return;
                // Re-applying a preset scale value recomputes it against the new
                // width. Once the user manually zooms, the value becomes numeric
                // and their zoom level is preserved across resizes.
                const scaleValue = viewer.currentScaleValue;
                if (PRESET_SCALES.has(scaleValue)) {
                    viewer.currentScaleValue = scaleValue;
                }
            });
        });
        ro.observe(container);
        return () => {
            ro.disconnect();
            if (raf !== null) cancelAnimationFrame(raf);
        };
    }, [modulesReady]);

    // ── Pinch + ctrl/⌘-wheel zoom-to-point ───────────────────────────────────
    useEffect(() => {
        const container = containerRef.current;
        if (!container || !enableGestures) return;

        // pdf.js scrolls so that `(origin - [offsetLeft, offsetTop]) * scaleDiff`
        // keeps the cursor point fixed. With the container absolutely positioned
        // at inset-0 its offsets are 0, but we add them back to stay correct
        // regardless of layout.
        const toOrigin = (clientX: number, clientY: number): [number, number] => {
            const rect = container.getBoundingClientRect();
            return [
                clientX - rect.left + container.offsetLeft,
                clientY - rect.top + container.offsetTop,
            ];
        };

        const onWheel = (e: WheelEvent) => {
            if (!e.ctrlKey && !e.metaKey) return;
            const viewer = viewerRef.current;
            if (!viewer) return;
            e.preventDefault();
            const scaleFactor = Math.exp(-e.deltaY / 100);
            viewer.updateScale({
                scaleFactor,
                drawingDelay: GESTURE_DRAWING_DELAY,
                origin: toOrigin(e.clientX, e.clientY),
            });
        };

        let pinchDist: number | null = null;
        const onTouchStart = (e: TouchEvent) => {
            if (e.touches.length === 2) {
                pinchDist = Math.hypot(
                    e.touches[0].clientX - e.touches[1].clientX,
                    e.touches[0].clientY - e.touches[1].clientY,
                );
            }
        };
        const onTouchMove = (e: TouchEvent) => {
            if (e.touches.length !== 2 || pinchDist === null) return;
            const viewer = viewerRef.current;
            if (!viewer) return;
            e.preventDefault();
            const dist = Math.hypot(
                e.touches[0].clientX - e.touches[1].clientX,
                e.touches[0].clientY - e.touches[1].clientY,
            );
            const scaleFactor = dist / pinchDist;
            pinchDist = dist;
            const cx = (e.touches[0].clientX + e.touches[1].clientX) / 2;
            const cy = (e.touches[0].clientY + e.touches[1].clientY) / 2;
            viewer.updateScale({
                scaleFactor,
                drawingDelay: GESTURE_DRAWING_DELAY,
                origin: toOrigin(cx, cy),
            });
        };
        const onTouchEnd = () => { pinchDist = null; };

        container.addEventListener("wheel", onWheel, { passive: false });
        container.addEventListener("touchstart", onTouchStart, { passive: true });
        container.addEventListener("touchmove", onTouchMove, { passive: false });
        container.addEventListener("touchend", onTouchEnd, { passive: true });
        return () => {
            container.removeEventListener("wheel", onWheel);
            container.removeEventListener("touchstart", onTouchStart);
            container.removeEventListener("touchmove", onTouchMove);
            container.removeEventListener("touchend", onTouchEnd);
        };
    }, [enableGestures, modulesReady]);

    // ── Actions ──────────────────────────────────────────────────────────────
    const zoomIn = useCallback(() => { viewerRef.current?.updateScale({ steps: 1 }); }, []);
    const zoomOut = useCallback(() => { viewerRef.current?.updateScale({ steps: -1 }); }, []);
    const resetZoom = useCallback(() => {
        const viewer = viewerRef.current;
        if (viewer) viewer.currentScaleValue = "page-width";
    }, []);
    const goToPage = useCallback((pageNumber: number) => {
        const viewer = viewerRef.current;
        if (!viewer) return;
        try {
            viewer.currentPageNumber = pageNumber;
            const pageEl = viewerElRef.current?.querySelector<HTMLElement>(`.page[data-page-number="${pageNumber}"]`);
            if (pageEl) {
                pageEl.scrollIntoView({ behavior: "smooth", block: "start" });
            }
        } catch {
            // ignore
        }
    }, []);
    const setSpread = useCallback((spread: PdfSpread) => {
        const viewer = viewerRef.current;
        if (viewer) viewer.spreadMode = SPREAD_VALUE[spread];
    }, []);
    const reload = useCallback(() => {
        setError(null);
        setStatus("loading");
        if (modulesReady) {
            setReloadNonce((n) => n + 1);
        } else {
            setEngineNonce((n) => n + 1);
        }
    }, [modulesReady]);

    return {
        containerRef,
        viewerElRef,
        status,
        error,
        numPages,
        currentPage,
        scalePercent,
        zoomIn,
        zoomOut,
        resetZoom,
        goToPage,
        setSpread,
        reload,
    };
}
