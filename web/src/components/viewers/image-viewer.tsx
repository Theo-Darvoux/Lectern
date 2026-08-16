"use client";

import { useRef, useState, useEffect } from "react";
import { useTranslations } from "next-intl";
import { RotateCcw, RotateCw } from "lucide-react";
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
    const [rotation, setRotation] = useState(0);

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
        setRotation(0);
    }, [blobUrl]);

    const { zoom, zoomIn, zoomOut, resetZoom } = usePinchZoom({
        initial: 100,
        min: MIN_ZOOM,
        max: MAX_ZOOM,
        step: ZOOM_STEP,
        targetRef: scrollRef,
        handleKeyboard: true,
    });

    const rotateCcw = () => setRotation((r) => r - 90);
    const rotateCw = () => setRotation((r) => r + 90);

    // Track the unscaled dimensions of the image when zoom is 100% and unrotated
    useEffect(() => {
        const img = imgRef.current;
        if (!img || zoom !== 100 || rotation !== 0) return;

        const handleResize = () => {
            if (img.offsetWidth > 0 && img.offsetHeight > 0) {
                setDimensions({
                    width: img.offsetWidth,
                    height: img.offsetHeight,
                });
            }
        };

        handleResize();

        const observer = new ResizeObserver(handleResize);
        observer.observe(img);

        return () => {
            observer.disconnect();
        };
    }, [zoom, blobUrl, rotation]);

    const normalizedRotation = ((rotation % 360) + 360) % 360;
    const isSideways = normalizedRotation === 90 || normalizedRotation === 270;

    const baseWidth = dimensions?.width;
    const baseHeight = dimensions?.height;
    const currentWidth = baseWidth ? (zoom !== 100 ? baseWidth * (zoom / 100) : baseWidth) : undefined;
    const currentHeight = baseHeight ? (zoom !== 100 ? baseHeight * (zoom / 100) : baseHeight) : undefined;

    return (
        <ViewerShell
            scrollRef={scrollRef}
            loading={loading}
            error={error ?? (decodeError ? t("imageLoadFailed") : null)}
            onRetry={reload}
            toolbarLeft={
                <>
                    <button
                        onClick={rotateCcw}
                        disabled={loading || !!error}
                        className="rounded-md p-2 transition-colors text-muted-foreground hover:bg-zinc-200 dark:hover:bg-zinc-800 hover:text-foreground disabled:opacity-40"
                        title={t("rotateCcw")}
                        aria-label={t("rotateCcw")}
                    >
                        <RotateCcw className="h-4 w-4" />
                    </button>
                    <button
                        onClick={rotateCw}
                        disabled={loading || !!error}
                        className="rounded-md p-2 transition-colors text-muted-foreground hover:bg-zinc-200 dark:hover:bg-zinc-800 hover:text-foreground disabled:opacity-40"
                        title={t("rotateCw")}
                        aria-label={t("rotateCw")}
                    >
                        <RotateCw className="h-4 w-4" />
                    </button>
                </>
            }
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
            <div className="flex min-h-full w-full p-4 items-center justify-center">
                {blobUrl && (
                    <div
                        style={{
                            width: isSideways && currentHeight
                                ? `${currentHeight}px`
                                : currentWidth && (zoom !== 100 || rotation !== 0)
                                ? `${currentWidth}px`
                                : undefined,
                            height: isSideways && currentWidth
                                ? `${currentWidth}px`
                                : currentHeight && (zoom !== 100 || rotation !== 0)
                                ? `${currentHeight}px`
                                : undefined,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            margin: "auto",
                            flexShrink: 0,
                            transition: "width 0.15s ease, height 0.15s ease",
                        }}
                    >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                            ref={imgRef}
                            src={blobUrl}
                            alt={fileName}
                            onLoad={() => {
                                if (imgRef.current && imgRef.current.offsetWidth > 0 && imgRef.current.offsetHeight > 0) {
                                    setDimensions({
                                        width: imgRef.current.offsetWidth,
                                        height: imgRef.current.offsetHeight,
                                    });
                                }
                            }}
                            onError={() => setDecodeError(true)}
                            style={{
                                width: currentWidth && (zoom !== 100 || rotation !== 0) ? `${currentWidth}px` : undefined,
                                height: currentHeight && (zoom !== 100 || rotation !== 0) ? `${currentHeight}px` : undefined,
                                maxWidth: (zoom !== 100 || rotation !== 0) ? "none" : undefined,
                                maxHeight: (zoom !== 100 || rotation !== 0) ? "none" : undefined,
                                transform: rotation !== 0 ? `rotate(${rotation}deg)` : undefined,
                                transformOrigin: "center center",
                                transition: "transform 0.2s ease, width 0.15s ease, height 0.15s ease",
                            }}
                            className="max-w-full max-h-[85vh] object-contain shadow-md rounded-sm m-auto"
                            draggable={false}
                        />
                    </div>
                )}
            </div>
        </ViewerShell>
    );
}
