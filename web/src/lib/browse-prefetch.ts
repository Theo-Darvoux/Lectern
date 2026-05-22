"use client";

import { apiFetch } from "./api-client";

// Shared in-memory cache for browse API responses, keyed by browse path
// (the part after /browse/, e.g. "category/subcategory" or "").
// Typed as unknown so callers can cast to their own BrowseResponse type.
export const browseCache = new Map<string, unknown>();
export let previousBrowsePath: string | null = null;
export function setPreviousBrowsePath(p: string) { previousBrowsePath = p; }

// In-flight requests keyed by browse path. Shared between hover-prefetch and
// the active navigation fetch so a hover-then-click only hits the network once.
const inflight = new Map<string, Promise<unknown>>();

/**
 * Fetch a browse path, populating browseCache and de-duplicating concurrent
 * requests for the same path.
 *
 * - `force: false` (default) returns the cached value if present, otherwise
 *   joins any in-flight request or starts a new one. Used by navigation so a
 *   prefetch already in flight is reused instead of duplicated.
 * - `force: true` bypasses the cache (but still joins an in-flight request) to
 *   revalidate a path whose cached copy is being shown optimistically.
 */
export function fetchBrowsePath(
    browsePath: string,
    { force = false }: { force?: boolean } = {},
): Promise<unknown> {
    if (!force && browseCache.has(browsePath)) {
        return Promise.resolve(browseCache.get(browsePath));
    }
    const existing = inflight.get(browsePath);
    if (existing) return existing;

    const endpoint = browsePath ? `/browse/${browsePath}` : "/browse";
    const request = apiFetch<unknown>(endpoint)
        .then((result) => {
            browseCache.set(browsePath, result);
            return result;
        })
        .finally(() => {
            inflight.delete(browsePath);
        });
    inflight.set(browsePath, request);
    return request;
}

export async function prefetchBrowsePath(browsePath: string): Promise<void> {
    if (browseCache.has(browsePath) || inflight.has(browsePath)) return;
    try {
        await fetchBrowsePath(browsePath);
    } catch {
        // silently ignore — if the prefetch fails the normal fetch will handle it
    }
}
