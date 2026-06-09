"use client";

// This file is intentionally separate so it can be loaded via next/dynamic({ ssr: false }).
// pdfjs-dist calls Promise.withResolvers() at module-evaluation time, which does not
// exist in the Node.js versions used by Next.js SSR — dynamic import keeps it browser-only.

import { useState, useEffect, useRef, useCallback } from "react";
import { Button } from "@/components/ui/button";
import {
    FileText,
    ZoomIn,
    ZoomOut,
    Loader2,
    ChevronLeft,
    ChevronRight,
} from "lucide-react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

pdfjs.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

const ZOOM_STEP = 25;
const MIN_ZOOM = 50;
const MAX_ZOOM = 300;

export function PdfPreview({ url }: { url: string }) {
    const scrollRef = useRef<HTMLDivElement>(null);
    const [pdfUrl, setPdfUrl] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [numPages, setNumPages] = useState(0);
    const [currentPage, setCurrentPage] = useState(1);
    const [zoom, setZoom] = useState(100);
    const [containerWidth, setContainerWidth] = useState(700);

    // Fetch blob so pdfjs doesn't make a cross-origin request itself
    useEffect(() => {
        let objectUrl: string | null = null;
        let cancelled = false;
        fetch(url)
            .then((r) => r.blob())
            .then((blob) => {
                if (cancelled) return;
                objectUrl = URL.createObjectURL(blob);
                setPdfUrl(objectUrl);
            })
            .catch((e) => {
                if (!cancelled) setError(e.message ?? "Failed to load PDF");
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });
        return () => {
            cancelled = true;
            if (objectUrl) URL.revokeObjectURL(objectUrl);
        };
    }, [url]);

    // Track container width for responsive page sizing
    useEffect(() => {
        const el = scrollRef.current;
        if (!el) return;
        let rafId: number;
        const ro = new ResizeObserver((entries) => {
            cancelAnimationFrame(rafId);
            rafId = requestAnimationFrame(() => {
                const w = entries[0]?.contentRect.width;
                if (w) setContainerWidth((prev) => (Math.abs(w - prev) > 1 ? w : prev));
            });
        });
        ro.observe(el);
        return () => {
            ro.disconnect();
            cancelAnimationFrame(rafId);
        };
    }, []);

    // Scroll-based page tracking
    useEffect(() => {
        const scrollEl = scrollRef.current;
        if (!scrollEl || numPages === 0) return;
        const io = new IntersectionObserver(
            (entries) => {
                let best: { page: number; top: number } | null = null;
                for (const entry of entries) {
                    const page = Number((entry.target as HTMLElement).dataset.page);
                    if (!page || !entry.isIntersecting) continue;
                    const top = entry.boundingClientRect.top;
                    if (!best || top < best.top) best = { page, top };
                }
                if (best) setCurrentPage(best.page);
            },
            { root: scrollEl, rootMargin: "0px 0px -80% 0px", threshold: 0 },
        );
        scrollEl.querySelectorAll("[data-page]").forEach((el) => io.observe(el));
        return () => io.disconnect();
    }, [numPages, zoom]);

    const targetPageRef = useRef(1);
    const lastNavRef = useRef(0);

    // Sync targetPageRef with currentPage only if we are not actively navigating
    useEffect(() => {
        const now = Date.now();
        if (now - lastNavRef.current > 500) {
            targetPageRef.current = currentPage;
        }
    }, [currentPage]);

    const navigatePage = useCallback((direction: "next" | "prev") => {
        if (numPages === 0) return;
        lastNavRef.current = Date.now();
        let nextTarget = targetPageRef.current;
        if (direction === "next") {
            nextTarget = Math.min(numPages, targetPageRef.current + 1);
        } else {
            nextTarget = Math.max(1, targetPageRef.current - 1);
        }
        targetPageRef.current = nextTarget;
        scrollRef.current?.querySelector(`[data-page="${nextTarget}"]`)?.scrollIntoView({ behavior: "smooth" });
    }, [numPages]);

    // Keyboard Navigation and Zoom
    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
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
                    setZoom((z) => Math.min(MAX_ZOOM, z + ZOOM_STEP));
                    return;
                }
                if (e.key === "-") {
                    e.preventDefault();
                    setZoom((z) => Math.max(MIN_ZOOM, z - ZOOM_STEP));
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
                navigatePage("next");
            } else if (
                e.key === "ArrowLeft" ||
                e.key === "q" || e.key === "Q" ||
                e.key === "a" || e.key === "A"
            ) {
                e.preventDefault();
                navigatePage("prev");
            }
        };
        window.addEventListener("keydown", handler);
        return () => window.removeEventListener("keydown", handler);
    }, [navigatePage, setZoom]);

    if (loading) return (
        <div className="flex h-full items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
    );
    if (error) return (
        <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-destructive">
            <FileText className="h-8 w-8 opacity-40" />
            {error}
        </div>
    );

    const pageWidth = Math.max(200, Math.floor(containerWidth * zoom / 100) - 32);

    return (
        <div className="flex h-full flex-col">
            <div className="flex shrink-0 items-center justify-between border-b bg-muted/30 px-4 py-1.5">
                <div className="flex items-center gap-1">
                    <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={() => setZoom((z) => Math.max(MIN_ZOOM, z - ZOOM_STEP))}
                        disabled={zoom <= MIN_ZOOM}
                    >
                        <ZoomOut className="h-3.5 w-3.5" />
                    </Button>
                    <span className="w-12 text-center text-xs tabular-nums">{zoom}%</span>
                    <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={() => setZoom((z) => Math.min(MAX_ZOOM, z + ZOOM_STEP))}
                        disabled={zoom >= MAX_ZOOM}
                    >
                        <ZoomIn className="h-3.5 w-3.5" />
                    </Button>
                </div>
                {numPages > 0 && (
                    <div className="flex items-center gap-1 text-xs text-muted-foreground">
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            disabled={currentPage <= 1}
                            onClick={() => navigatePage("prev")}
                        >
                            <ChevronLeft className="h-3.5 w-3.5" />
                        </Button>
                        <span className="tabular-nums">{currentPage} / {numPages}</span>
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            disabled={currentPage >= numPages}
                            onClick={() => navigatePage("next")}
                        >
                            <ChevronRight className="h-3.5 w-3.5" />
                        </Button>
                    </div>
                )}
            </div>
            <div ref={scrollRef} className="flex-1 overflow-y-auto bg-muted/10 px-4 py-4">
                <Document
                    file={pdfUrl}
                    onLoadSuccess={({ numPages: n }) => setNumPages(n)}
                    onLoadError={(e) => setError(e.message ?? "Erreur de lecture")}
                    loading={
                        <div className="flex justify-center py-8">
                            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                        </div>
                    }
                >
                    {Array.from({ length: numPages }, (_, i) => (
                        <div key={i} data-page={i + 1} className="mb-4 flex justify-center">
                            <Page
                                pageNumber={i + 1}
                                width={pageWidth}
                                renderTextLayer
                                renderAnnotationLayer={false}
                            />
                        </div>
                    ))}
                </Document>
            </div>
        </div>
    );
}
