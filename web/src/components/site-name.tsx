"use client";

import { parseSegments, segmentStyle } from "@/lib/fonts";

interface SiteNameProps {
    name: string;
    style?: string | null;
    /** Extra className applied to the wrapper span when no custom segment colors are set */
    gradientClassName?: string;
}

/**
 * Renders the site name either as plain text (with optional gradient) or
 * as rich styled segments when site_name_style JSON is present.
 */
export function SiteName({ name, style, gradientClassName }: SiteNameProps) {
    const segments = parseSegments(style);

    if (!segments) {
        return (
            <span className={gradientClassName}>
                {name || "WikINT"}
            </span>
        );
    }

    const hasCustomColor = segments.some((s) => s.color);

    return (
        <span className={hasCustomColor ? undefined : gradientClassName}>
            {segments.map((seg, i) => (
                <span key={i} style={segmentStyle(seg) as React.CSSProperties}>
                    {seg.text}
                </span>
            ))}
        </span>
    );
}
