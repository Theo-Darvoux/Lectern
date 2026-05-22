"use client";

import { apiFetch } from "./api-client";

// Shared in-memory cache for browse API responses, keyed by browse path
// (the part after /browse/, e.g. "category/subcategory" or "").
// Typed as unknown so callers can cast to their own BrowseResponse type.
export const browseCache = new Map<string, unknown>();
export let previousBrowsePath: string | null = null;
export function setPreviousBrowsePath(p: string) { previousBrowsePath = p; }

const inflight = new Set<string>();

export async function prefetchBrowsePath(browsePath: string): Promise<void> {
    if (browseCache.has(browsePath) || inflight.has(browsePath)) return;
    inflight.add(browsePath);
    try {
        const endpoint = browsePath ? `/browse/${browsePath}` : "/browse";
        const result = await apiFetch<unknown>(endpoint);
        browseCache.set(browsePath, result);
    } catch {
        // silently ignore — if the prefetch fails the normal fetch will handle it
    } finally {
        inflight.delete(browsePath);
    }
}
