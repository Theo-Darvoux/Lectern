/**
 * Utility functions for URL parsing, external vs internal domain checking,
 * and display formatting.
 */

/**
 * Checks if a given URL points to an external site rather than the current
 * application domain/origin.
 *
 * Rules:
 * - Empty/null/undefined -> false
 * - Relative paths (starting with '/', './', '../', '#') -> false (internal)
 * - URLs matching current window.location.origin / host -> false (internal)
 * - Absolute HTTP/HTTPS URLs with a different origin -> true (external)
 * - Non-HTTP/HTTPS protocols (e.g. mailto:, tel:) -> false
 */
export function isExternalUrl(targetUrl?: string | null): boolean {
    if (!targetUrl || typeof targetUrl !== "string") return false;
    const trimmed = targetUrl.trim();
    if (!trimmed) return false;

    // Relative paths and fragment identifiers are always internal
    if (
        (trimmed.startsWith("/") && !trimmed.startsWith("//")) ||
        trimmed.startsWith("#") ||
        trimmed.startsWith("./") ||
        trimmed.startsWith("../") ||
        trimmed.startsWith("?")
    ) {
        return false;
    }

    // Explicit non-web schemes
    if (/^(mailto:|tel:|sms:|javascript:)/i.test(trimmed)) {
        return false;
    }

    try {
        const currentOrigin =
            typeof window !== "undefined" && window.location?.origin
                ? window.location.origin
                : "http://localhost";
        const currentHost =
            typeof window !== "undefined" && window.location?.host
                ? window.location.host
                : "localhost";

        // Support protocol-relative URLs (e.g. //example.com)
        const normalized = trimmed.startsWith("//") ? `https:${trimmed}` : trimmed;
        const parsed = new URL(normalized, currentOrigin);

        // Only HTTP/HTTPS URLs are treated as external links
        if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
            return false;
        }

        // If origin matches, it's internal
        if (typeof window !== "undefined" && window.location?.origin) {
            if (parsed.origin === currentOrigin) return false;
            if (parsed.host === currentHost) return false;
        }

        return true;
    } catch {
        return false;
    }
}

/**
 * Safely extracts the domain/hostname from a URL string (e.g., "github.com").
 * Returns an empty string if invalid or relative.
 */
export function getDomainFromUrl(targetUrl?: string | null): string {
    if (!targetUrl || typeof targetUrl !== "string") return "";
    const trimmed = targetUrl.trim();
    if (!trimmed) return "";

    try {
        const normalized = trimmed.startsWith("//")
            ? `https:${trimmed}`
            : !trimmed.includes("://") && !trimmed.startsWith("/")
              ? `https://${trimmed}`
              : trimmed;
        const parsed = new URL(normalized, "http://localhost");
        return parsed.hostname.replace(/^www\./, "");
    } catch {
        return "";
    }
}

/**
 * Ensures a valid web URL scheme (prefixes https:// if omitted and not a relative path).
 */
export function normalizeTargetUrl(rawUrl: string): string {
    const trimmed = rawUrl.trim();
    if (!trimmed) return "";
    if (
        trimmed.startsWith("/") ||
        trimmed.startsWith("#") ||
        trimmed.startsWith("./") ||
        trimmed.startsWith("../")
    ) {
        return trimmed;
    }
    if (/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(trimmed) || trimmed.startsWith("//")) {
        return trimmed;
    }
    return `https://${trimmed}`;
}

/**
 * Extracts the internal browse path (the segment after /browse/) from a URL if it
 * points to an internal browse route.
 *
 * Returns null if the URL is external or not a /browse route.
 * Examples:
 * - "/browse/math/analysis" -> "math/analysis"
 * - "https://current-domain/browse/math/analysis" -> "math/analysis"
 * - "/browse" -> ""
 * - "/browse/" -> ""
 * - "https://external.com/browse/math" -> null
 * - "/settings" -> null
 */
export function getInternalBrowsePath(targetUrl?: string | null): string | null {
    if (!targetUrl || typeof targetUrl !== "string") return null;
    const trimmed = targetUrl.trim();
    if (!trimmed) return null;
    if (isExternalUrl(trimmed)) return null;

    try {
        const currentOrigin =
            typeof window !== "undefined" && window.location?.origin
                ? window.location.origin
                : "http://localhost";

        const normalized = trimmed.startsWith("//") ? `https:${trimmed}` : trimmed;
        const parsed = new URL(normalized, currentOrigin);

        const pathname = parsed.pathname;
        if (pathname === "/browse" || pathname === "/browse/") {
            return "";
        }
        if (pathname.startsWith("/browse/")) {
            const rawPath = pathname.slice("/browse/".length).replace(/\/+$/, "");
            return decodeURIComponent(rawPath);
        }

        return null;
    } catch {
        return null;
    }
}

/**
 * Checks if a URL is an internal link that points to a /browse route.
 */
export function isInternalBrowseUrl(targetUrl?: string | null): boolean {
    return getInternalBrowsePath(targetUrl) !== null;
}

