"use client";

import type { CSSProperties } from "react";
import { useConfigStore } from "@/lib/stores";

const DEFAULT_OPACITY = 0.05;

export function BackgroundWatermark() {
    const config = useConfigStore((s) => s.config);

    if (!config?.bg_watermark_url) return null;

    // The opacity is chosen by CSS from the `.dark` class rather than from
    // `useTheme()`. The server cannot know the theme, so a JS choice renders
    // the light value into the HTML, and React keeps server attributes on
    // hydration: dark mode stayed at the light opacity.
    const style = {
        zIndex: -10,
        "--wm-opacity-light": config.bg_watermark_opacity_light ?? DEFAULT_OPACITY,
        "--wm-opacity-dark": config.bg_watermark_opacity_dark ?? DEFAULT_OPACITY,
    } as CSSProperties;

    return (
        <div
            aria-hidden
            className="pointer-events-none fixed inset-0 flex items-center justify-center overflow-hidden opacity-[var(--wm-opacity-light)] dark:opacity-[var(--wm-opacity-dark)]"
            style={style}
        >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
                src={config.bg_watermark_url}
                alt=""
                className="max-h-full max-w-full object-contain"
                style={{ filter: "drop-shadow(0 0 24px rgba(0,0,0,0.45))" }}
            />
        </div>
    );
}
