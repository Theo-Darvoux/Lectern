"use client";

import React, { useRef, useCallback } from "react";
import { Loader2, AlertCircle } from "lucide-react";
import { useFullscreen } from "@/hooks/use-fullscreen";
import { useScrollHide } from "@/hooks/use-scroll-hide";
import { ViewerToolbar } from "./viewer-toolbar";
import { FullscreenToggle } from "./fullscreen-toggle";
import { cn } from "@/lib/utils";

interface ViewerShellProps {
    children: React.ReactNode;
    loading?: boolean;
    error?: string | null;
    truncatedMessage?: string | null;
    toolbarLeft?: React.ReactNode;
    toolbarCenter?: React.ReactNode;
    toolbarRight?: React.ReactNode;
    className?: string;
    /** Ref to the scrollable container for pinch-zoom etc. */
    scrollRef?: React.RefObject<HTMLDivElement | null>;
}

export function ViewerShell({
    children,
    loading,
    error,
    truncatedMessage,
    toolbarLeft,
    toolbarCenter,
    toolbarRight,
    className,
    scrollRef,
}: ViewerShellProps) {
    const containerRef = useRef<HTMLDivElement>(null);
    const { isFullscreen, toggleFullscreen } = useFullscreen(containerRef);
    const internalScrollRef = useRef<HTMLDivElement>(null);
    useScrollHide(internalScrollRef);

    // Merge the external scrollRef (used by pinch-zoom etc.) with our internal one
    const setScrollEl = useCallback(
      (el: HTMLDivElement | null) => {
        internalScrollRef.current = el;
        if (scrollRef) scrollRef.current = el;
      },
      [scrollRef],
    );

    return (
        <div
            ref={containerRef}
            className={cn(
                "relative flex flex-col bg-background min-w-0 w-full overflow-hidden",
                isFullscreen ? "h-screen" : "h-full",
                className
            )}
        >
            <ViewerToolbar
                isFullscreen={isFullscreen}
                left={toolbarLeft}
                center={toolbarCenter}
                right={
                    <>
                        {toolbarRight}
                        <FullscreenToggle
                            isFullscreen={isFullscreen}
                            onToggle={toggleFullscreen}
                            disabled={loading || !!error}
                        />
                    </>
                }
            />

            <div
                ref={setScrollEl}
                className="flex-1 relative overflow-auto bg-zinc-200 dark:bg-zinc-800/50"
                style={{ touchAction: "pan-x pan-y" }}
            >
                {truncatedMessage && (
                    <div className="sticky top-0 z-20 flex items-center gap-2 border-b bg-amber-50 px-4 py-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
                        <AlertCircle className="h-3 w-3" />
                        <span>{truncatedMessage}</span>
                    </div>
                )}

                {loading ? (
                    <div className="flex h-full items-center justify-center p-8">
                        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                    </div>
                ) : error ? (
                    <div className="flex h-full items-center justify-center p-8 text-center text-sm text-destructive">
                        {error}
                    </div>
                ) : (
                    children
                )}
            </div>
        </div>
    );
}
