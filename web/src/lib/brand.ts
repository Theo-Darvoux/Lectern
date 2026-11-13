import { buildFontsUrlForNames, parseSegments } from "@/lib/fonts";

const DARK_TEXT = "oklch(0.205 0 0)";
const LIGHT_TEXT = "oklch(0.985 0 0)";

/** Text color that stays readable on the given brand color. */
function foregroundFor(color: string): string {
    const hex = color.replace("#", "");
    if (hex.length !== 6) return LIGHT_TEXT;
    const r = parseInt(hex.substring(0, 2), 16);
    const g = parseInt(hex.substring(2, 4), 16);
    const b = parseInt(hex.substring(4, 6), 16);
    const brightness = (r * 299 + g * 587 + b * 114) / 1000;
    return brightness > 165 ? DARK_TEXT : LIGHT_TEXT;
}

/** CSS custom properties that apply the brand color (see globals.css). */
export function brandColorVariables(primaryColor: string | null | undefined): Record<string, string> {
    if (!primaryColor) return {};
    return {
        "--primary-custom": primaryColor,
        "--primary-foreground-custom": foregroundFor(primaryColor),
    };
}

/** Google Fonts stylesheet for the fonts used by the SITE_NAME_STYLE wordmark. */
export function wordmarkFontsHref(siteNameStyle: string | null | undefined): string | null {
    const segments = parseSegments(siteNameStyle);
    if (!segments) return null;
    const fonts = [...new Set(segments.map((segment) => segment.font).filter(Boolean))];
    return buildFontsUrlForNames(fonts) || null;
}
