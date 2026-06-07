"use client";

import { useEffect, useRef, useState } from "react";
import { useMaterialFile } from "@/hooks/use-material-file";
import { ViewerShell } from "./viewer-shell";

interface EpubViewerProps {
    fileKey: string;
    materialId: string;
}

export function EpubViewer({ materialId, fileKey }: EpubViewerProps) {
    const viewerRef = useRef<HTMLDivElement>(null);
    const [renderError, setRenderError] = useState<string | null>(null);

    const { arrayBuffer, loading, error, reload } = useMaterialFile({
        materialId,
        fileKey,
        mode: "arrayBuffer",
    });

    useEffect(() => {
        let isMounted = true;
        let book: { destroy: () => void; renderTo: (el: HTMLElement, opts: Record<string, string>) => { display: () => Promise<void> } } | null = null;

        const initEpub = async () => {
            if (!arrayBuffer || !viewerRef.current) return;

            try {
                // Ensure epub.js is loaded
                if (!(window as any).ePub) {
                    await new Promise<void>((resolve, reject) => {
                        const script = document.createElement("script");
                        script.src = "https://cdnjs.cloudflare.com/ajax/libs/epub.js/0.3.90/epub.min.js";
                        script.async = true;
                        script.onload = () => resolve();
                        script.onerror = () => reject(new Error("Failed to load generic Epub engine"));
                        document.head.appendChild(script);
                    });
                }

                if (!isMounted) return;

                // Load book from buffer
                book = (window as any).ePub(arrayBuffer);

                const rendition = book!.renderTo(viewerRef.current, {
                    width: "100%",
                    height: "100%",
                    spread: "none"
                });

                await rendition.display();
            } catch (err: any) {
                if (isMounted) {
                    setRenderError(err.message ?? "Failed to render EPUB");
                }
            }
        };

        initEpub();

        return () => {
            isMounted = false;
            if (book) book.destroy();
        };
    }, [arrayBuffer]);

    return (
        <ViewerShell loading={loading} error={error || renderError} onRetry={reload}>
            <div className="relative flex flex-1 w-full flex-col h-[800px]">
                <div ref={viewerRef} className="h-full w-full overflow-hidden" />
            </div>
        </ViewerShell>
    );
}
