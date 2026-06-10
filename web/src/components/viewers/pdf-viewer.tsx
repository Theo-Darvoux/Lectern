"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ZoomIn, ZoomOut, BookOpen } from "lucide-react";
import { useTranslations } from "next-intl";
import type { ThreadData } from "@/hooks/use-annotations";
import { useMaterialFile } from "@/hooks/use-material-file";
import { usePdfjsDocument } from "@/hooks/use-pdfjs-document";
import { paintPageHighlights, type PageAnnotation } from "@/lib/pdf-annotations";
import { ViewerShell } from "./viewer-shell";
import { AnnotationInlinePopover } from "@/components/annotations/annotation-inline-popover";

interface PdfViewerProps {
    fileKey: string;
    materialId: string;
    annotations?: ThreadData[];
}

export function PdfViewer({ materialId, fileKey, annotations = [] }: PdfViewerProps) {
    const t = useTranslations("Viewers");
    const shellScrollRef = useRef<HTMLDivElement>(null);

    const { blobUrl, loading, error, reload } = useMaterialFile({ materialId, fileKey, mode: "blob" });

    const [twoPageView, setTwoPageView] = useState(false);
    const [activeAnnotation, setActiveAnnotation] = useState<{
        thread: ThreadData; clientX: number; clientY: number;
    } | null>(null);

    // ── Annotation data, kept in refs so the imperative paint callbacks below
    //    always read the latest without re-binding pdf.js event listeners. ──
    const annotationsKey = annotations
        .map((th) => {
            const occ = th.root.position_data?.occurrenceIndex;
            return `${th.root.id}:${th.root.selection_text ?? ""}:${th.root.page ?? "_"}:${typeof occ === "number" ? occ : "_"}`;
        })
        .join("|");

    const allAnnotations = useMemo<PageAnnotation[]>(
        () => annotations.map((th) => ({
            selection_text: th.root.selection_text,
            page: th.root.page,
            occurrenceIndex: typeof th.root.position_data?.occurrenceIndex === "number"
                ? th.root.position_data.occurrenceIndex
                : null,
            threadId: th.root.id,
        })),
        // annotationsKey captures the meaningful shape; `annotations` ref included for lint.
        [annotationsKey, annotations],
    );
    const allAnnotationsRef = useRef(allAnnotations);
    useEffect(() => { allAnnotationsRef.current = allAnnotations; });

    const annotationClickRef = useRef<(threadId: string, e: MouseEvent) => void>(() => {});
    useEffect(() => {
        annotationClickRef.current = (threadId, e) => {
            const thread = annotations.find((th) => th.root.id === threadId) ?? null;
            if (thread) setActiveAnnotation({ thread, clientX: e.clientX, clientY: e.clientY });
        };
    }, [annotations]);

    const onTextLayerRendered = useCallback((pageNumber: number, pageDiv: HTMLDivElement) => {
        paintPageHighlights(pageDiv, pageNumber, allAnnotationsRef.current, annotationClickRef.current);
    }, []);

    const {
        containerRef, viewerElRef, status, error: pdfError,
        numPages, currentPage, scalePercent,
        zoomIn, zoomOut, resetZoom, goToPage, setSpread,
    } = usePdfjsDocument({ url: blobUrl, onTextLayerRendered });

    // Two-page (spread) view toggle.
    useEffect(() => {
        setSpread(twoPageView ? "odd" : "none");
    }, [twoPageView, setSpread]);

    // Repaint overlays on every already-rendered page when annotations change.
    useEffect(() => {
        const root = viewerElRef.current;
        if (!root) return;
        root.querySelectorAll<HTMLDivElement>(".page").forEach((pageDiv) => {
            const n = parseInt(pageDiv.getAttribute("data-page-number") ?? "0", 10);
            if (n) paintPageHighlights(pageDiv, n, allAnnotationsRef.current, annotationClickRef.current);
        });
    }, [annotationsKey, viewerElRef]);

    // ── Keyboard: page nav (arrows / WASD) + zoom (ctrl/⌘ +/-/0) ──────────────
    const pageRef = useRef(currentPage);
    useEffect(() => { pageRef.current = currentPage; }, [currentPage]);

    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            const target = e.target as HTMLElement | null;
            if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA"
                || target.tagName === "SELECT" || target.isContentEditable)) {
                return;
            }

            if (e.ctrlKey || e.metaKey) {
                if (e.key === "=" || e.key === "+") { e.preventDefault(); zoomIn(); return; }
                if (e.key === "-") { e.preventDefault(); zoomOut(); return; }
                if (e.key === "0") { e.preventDefault(); resetZoom(); return; }
                return;
            }
            if (e.altKey || e.shiftKey) return;

            const step = twoPageView ? 2 : 1;
            if (e.key === "ArrowRight" || e.key === "d" || e.key === "D") {
                e.preventDefault();
                goToPage(Math.min(numPages, pageRef.current + step));
            } else if (e.key === "ArrowLeft" || e.key === "q" || e.key === "Q" || e.key === "a" || e.key === "A") {
                e.preventDefault();
                goToPage(Math.max(1, pageRef.current - step));
            }
        };
        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
    }, [numPages, twoPageView, zoomIn, zoomOut, resetZoom, goToPage]);

    return (
        <>
            <ViewerShell
                scrollRef={shellScrollRef}
                loading={loading}
                error={error || (status === "error" ? pdfError : null)}
                onRetry={reload}
                toolbarLeft={
                    <button
                        onClick={() => setTwoPageView((v) => !v)}
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
                            onClick={zoomOut}
                            disabled={loading}
                            className="rounded-md p-2 transition-colors text-muted-foreground hover:bg-zinc-200 dark:hover:bg-zinc-800 hover:text-foreground disabled:opacity-40"
                            title={t("pdf.zoomOut")}
                        >
                            <ZoomOut className="h-4 w-4" />
                        </button>
                        <button
                            onClick={resetZoom}
                            disabled={loading}
                            className="min-w-12 rounded-md px-2 py-1 text-center text-xs font-medium tabular-nums transition-colors hover:bg-zinc-200 dark:hover:bg-zinc-800 disabled:opacity-40"
                            title={t("pdf.resetZoom")}
                        >
                            {scalePercent}%
                        </button>
                        <button
                            onClick={zoomIn}
                            disabled={loading}
                            className="rounded-md p-2 transition-colors text-muted-foreground hover:bg-zinc-200 dark:hover:bg-zinc-800 hover:text-foreground disabled:opacity-40"
                            title={t("pdf.zoomIn")}
                        >
                            <ZoomIn className="h-4 w-4" />
                        </button>
                    </>
                }
            >
                {/* pdf.js requires its scroll container to be absolutely positioned;
                    the shell's `relative` scroll area is the offset parent. */}
                <div ref={containerRef} className="absolute inset-0 overflow-auto">
                    <div ref={viewerElRef} className="pdfViewer" />
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
