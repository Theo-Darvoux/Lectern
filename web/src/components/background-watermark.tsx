"use client";

import { useTheme } from "next-themes";
import { useConfigStore } from "@/lib/stores";

export function BackgroundWatermark() {
    const config = useConfigStore((s) => s.config);
    const { resolvedTheme } = useTheme();

    if (!config?.bg_watermark_url) return null;

    const opacity =
        resolvedTheme === "dark"
            ? (config.bg_watermark_opacity_dark ?? 0.05)
            : (config.bg_watermark_opacity_light ?? 0.05);

    return (
        <div
            aria-hidden
            className="pointer-events-none fixed inset-0 flex items-center justify-center overflow-hidden"
            style={{ zIndex: -10, opacity }}
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
