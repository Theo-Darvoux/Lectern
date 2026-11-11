"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { useMaterialFile } from "@/hooks/use-material-file";
import { ViewerShell } from "./viewer-shell";

interface EpubBook {
    destroy: () => void;
    renderTo: (el: HTMLElement, opts: Record<string, string>) => { display: () => Promise<void> };
}

declare global {
    interface Window {
        /** Global set by the epub.js script loaded below. */
        ePub?: (data: ArrayBuffer) => EpubBook;
    }
}

interface EpubViewerProps {
    fileKey: string;
    materialId: string;
}

export function EpubViewer({ materialId, fileKey }: EpubViewerProps) {
    const t = useTranslations("Viewers.epub");
    const viewerRef = useRef<HTMLDivElement>(null);
    const [renderError, setRenderError] = useState<string | null>(null);

    const { arrayBuffer, loading, error, reload } = useMaterialFile({
        materialId,
        fileKey,
        mode: "arrayBuffer",
    });

    useEffect(() => {
        let isMounted = true;
        let book: EpubBook | null = null;

        const initEpub = async () => {
            if (!arrayBuffer || !viewerRef.current) return;

            try {
                // Ensure epub.js is loaded
                if (!window.ePub) {
                    await new Promise<void>((resolve, reject) => {
                        const script = document.createElement("script");
                        script.src = "https://cdnjs.cloudflare.com/ajax/libs/epub.js/0.3.90/epub.min.js";
                        script.async = true;
                        script.onload = () => resolve();
                        script.onerror = () => reject(new Error("epub.js failed to load"));
                        document.head.appendChild(script);
                    });
                }

                if (!isMounted || !window.ePub) return;

                book = window.ePub(arrayBuffer);
                const rendition = book.renderTo(viewerRef.current, {
                    width: "100%",
                    height: "100%",
                    spread: "none"
                });

                await rendition.display();
            } catch (err) {
                console.error("EPUB render failed:", err);
                if (isMounted) setRenderError(t("renderFailed"));
            }
        };

        initEpub();

        return () => {
            isMounted = false;
            if (book) book.destroy();
        };
    }, [arrayBuffer, t]);

    return (
        <ViewerShell loading={loading} error={error || renderError} onRetry={reload}>
            <div className="relative flex flex-1 w-full flex-col h-[800px]">
                <div ref={viewerRef} className="h-full w-full overflow-hidden" />
            </div>
        </ViewerShell>
    );
}
