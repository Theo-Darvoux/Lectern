"use client";

import { useRef, useState, useEffect } from "react";
import { useTranslations } from "next-intl";
import { usePinchZoom } from "@/hooks/use-pinch-zoom";
import { useMaterialFile } from "@/hooks/use-material-file";
import { ViewerShell } from "./viewer-shell";
import { ZoomControls } from "./zoom-controls";

const MIN_ZOOM = 25;
const MAX_ZOOM = 500;
const ZOOM_STEP = 25;

interface ImageViewerProps {
    fileKey: string;
    materialId: string;
    fileName: string;
}

export function ImageViewer({ materialId, fileKey, fileName }: ImageViewerProps) {
    const t = useTranslations("Viewers");
    const scrollRef = useRef<HTMLDivElement>(null);
    const imgRef = useRef<HTMLImageElement>(null);
    const [dimensions, setDimensions] = useState<{ width: number; height: number } | null>(null);

    const { blobUrl, loading, error, reload } = useMaterialFile({
        materialId,
        fileKey,
        mode: "blob",
    });
    const [decodeError, setDecodeError] = useState(false);

    // A non-empty but corrupt blob (e.g. a partial download that still passed the
    // size check) won't fail the fetch — catch it here so it surfaces as an error
    // with a Retry button instead of an invisible, broken <img>.
    useEffect(() => {
        setDecodeError(false);
    }, [blobUrl]);

    const { zoom, zoomIn, zoomOut, resetZoom } = usePinchZoom({
        initial: 100,
        min: MIN_ZOOM,
        max: MAX_ZOOM,
        step: ZOOM_STEP,
        targetRef: scrollRef,
        handleKeyboard: true,
    });

    // Track the unscaled dimensions of the image when zoom is 100%
    useEffect(() => {
        const img = imgRef.current;
        if (!img || zoom !== 100) return;

        const handleResize = () => {
            const rect = img.getBoundingClientRect();
            setDimensions({
                width: rect.width,
                height: rect.height,
            });
        };

        // Initialize size
        handleResize();

        const observer = new ResizeObserver(handleResize);
        observer.observe(img);

        return () => {
            observer.disconnect();
        };
    }, [zoom, blobUrl]);

    return (
        <ViewerShell
            scrollRef={scrollRef}
            loading={loading}
            error={error ?? (decodeError ? t("imageLoadFailed") : null)}
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
            className="flex-1"
        >
            <div className="flex min-h-full w-full p-4">
                {blobUrl && (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img
                        ref={imgRef}
                        src={blobUrl}
                        alt={fileName}
                        onError={() => setDecodeError(true)}
                        style={
                            zoom !== 100 && dimensions
                                ? {
                                      width: `${dimensions.width * (zoom / 100)}px`,
                                      height: `${dimensions.height * (zoom / 100)}px`,
                                      maxWidth: "none",
                                      maxHeight: "none",
                                      transition: "width 0.15s ease, height 0.15s ease",
                                  }
                                : {
                                      transition: "width 0.15s ease, height 0.15s ease",
                                  }
                        }
                        className="max-w-full max-h-[85vh] object-contain shadow-md rounded-sm m-auto"
                        draggable={false}
                    />
                )}
            </div>
        </ViewerShell>
    );
}
