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

// Cheap structural equality used to decide whether a background revalidation
// actually changed anything. When it didn't, we keep the previous object's
// identity so React (and every memo'd row) can bail out of re-rendering.
function payloadsEqual(a: unknown, b: unknown): boolean {
    if (a === b) return true;
    if (a === undefined || b === undefined) return false;
    try {
        return JSON.stringify(a) === JSON.stringify(b);
    } catch {
        return false;
    }
}

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
            // Preserve the previous object identity on an unchanged revalidation
            // so the cache-first render path doesn't trigger a second full
            // re-render of the listing with fresh (memo-defeating) identities.
            const prev = browseCache.get(browsePath);
            if (prev !== undefined && payloadsEqual(prev, result)) {
                return prev;
            }
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
