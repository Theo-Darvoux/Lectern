"use client";

// This file is intentionally separate so it can be loaded via next/dynamic({ ssr: false }).
// The pdf.js engine is imported lazily inside usePdfjsDocument, keeping it browser-only.

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
import { usePdfjsDocument } from "@/hooks/use-pdfjs-document";

export function PdfPreview({ url }: { url: string }) {
    const [blobUrl, setBlobUrl] = useState<string | null>(null);
    const [fetchError, setFetchError] = useState<string | null>(null);

    // Fetch the blob so pdf.js doesn't make a cross-origin request itself.
    useEffect(() => {
        let objectUrl: string | null = null;
        let cancelled = false;
        setBlobUrl(null);
        setFetchError(null);
        fetch(url)
            .then((r) => r.blob())
            .then((blob) => {
                if (cancelled) return;
                objectUrl = URL.createObjectURL(blob);
                setBlobUrl(objectUrl);
            })
            .catch((e) => {
                if (!cancelled) setFetchError(e.message ?? "Failed to load PDF");
            });
        return () => {
            cancelled = true;
            if (objectUrl) URL.revokeObjectURL(objectUrl);
        };
    }, [url]);

    const {
        containerRef, viewerElRef, status, error: pdfError,
        numPages, currentPage, scalePercent,
        zoomIn, zoomOut, goToPage,
    } = usePdfjsDocument({ url: blobUrl });

    const pageRef = useRef(currentPage);
    useEffect(() => { pageRef.current = currentPage; }, [currentPage]);

    const navigate = useCallback((dir: "next" | "prev") => {
        const next = dir === "next" ? pageRef.current + 1 : pageRef.current - 1;
        goToPage(Math.max(1, Math.min(numPages || 1, next)));
    }, [goToPage, numPages]);

    // Keyboard navigation + zoom.
    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            const target = e.target as HTMLElement | null;
            if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA"
                || target.tagName === "SELECT" || target.isContentEditable)) {
                return;
            }
            if (e.ctrlKey || e.metaKey) {
                if (e.key === "=" || e.key === "+") { e.preventDefault(); zoomIn(); }
                else if (e.key === "-") { e.preventDefault(); zoomOut(); }
                return;
            }
            if (e.altKey || e.shiftKey) return;
            if (e.key === "ArrowRight" || e.key === "d" || e.key === "D") { e.preventDefault(); navigate("next"); }
            else if (e.key === "ArrowLeft" || e.key === "q" || e.key === "Q" || e.key === "a" || e.key === "A") { e.preventDefault(); navigate("prev"); }
        };
        window.addEventListener("keydown", handler);
        return () => window.removeEventListener("keydown", handler);
    }, [navigate, zoomIn, zoomOut]);

    const error = fetchError || (status === "error" ? pdfError : null);
    if (error) {
        return (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-destructive">
                <FileText className="h-8 w-8 opacity-40" />
                {error}
            </div>
        );
    }

    const showSpinner = !blobUrl || status === "loading";

    return (
        <div className="flex h-full flex-col">
            <div className="flex shrink-0 items-center justify-between border-b bg-muted/30 px-4 py-1.5">
                <div className="flex items-center gap-1">
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={zoomOut} disabled={showSpinner}>
                        <ZoomOut className="h-3.5 w-3.5" />
                    </Button>
                    <span className="w-12 text-center text-xs tabular-nums">{scalePercent}%</span>
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={zoomIn} disabled={showSpinner}>
                        <ZoomIn className="h-3.5 w-3.5" />
                    </Button>
                </div>
                {numPages > 0 && (
                    <div className="flex items-center gap-1 text-xs text-muted-foreground">
                        <Button variant="ghost" size="icon" className="h-7 w-7" disabled={currentPage <= 1} onClick={() => navigate("prev")}>
                            <ChevronLeft className="h-3.5 w-3.5" />
                        </Button>
                        <span className="tabular-nums">{currentPage} / {numPages}</span>
                        <Button variant="ghost" size="icon" className="h-7 w-7" disabled={currentPage >= numPages} onClick={() => navigate("next")}>
                            <ChevronRight className="h-3.5 w-3.5" />
                        </Button>
                    </div>
                )}
            </div>
            <div className="relative flex-1 overflow-hidden bg-muted/10">
                {/* pdf.js requires its scroll container to be absolutely positioned. */}
                <div ref={containerRef} className="absolute inset-0 overflow-auto">
                    <div ref={viewerElRef} className="pdfViewer" />
                </div>
                {showSpinner && (
                    <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                    </div>
                )}
            </div>
        </div>
    );
}
