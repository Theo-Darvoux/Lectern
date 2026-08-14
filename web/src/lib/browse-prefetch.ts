"use client";

import { apiFetchRetry } from "./api-client";
import { ResourceCache } from "./resource-cache";

// A directory listing is a small JSON payload, so it should return quickly.
// Cap each attempt so a stalled connection can't leave the request pending
// forever — which would otherwise pin the in-flight entry below and hang the
// listing skeleton until a full page reload.
const BROWSE_TIMEOUT_MS = 15_000;

// Shared in-memory cache for browse API responses, keyed by browse path
// (the part after /browse/, e.g. "category/subcategory" or "").
// Typed as unknown so callers can cast to their own BrowseResponse type.
export const browseCache = new ResourceCache<string, unknown>({
    maxEntries: 64,
    ttlMs: 5 * 60 * 1000,
});
export let previousBrowsePath: string | null = null;
export function setPreviousBrowsePath(p: string) { previousBrowsePath = p; }

// In-flight requests keyed by browse path. Shared between hover-prefetch and
// the active navigation fetch so a hover-then-click only hits the network once.
const inflight = new Map<string, Promise<unknown>>();
let invalidationEpoch = 0;

function browseResourceTags(payload: unknown): string[] {
    if (!payload || typeof payload !== "object") return [];
    const data = payload as Record<string, unknown>;
    const tags = new Set<string>();
    const addEntity = (kind: "directory" | "material", value: unknown) => {
        if (!value || typeof value !== "object") return;
        const id = (value as Record<string, unknown>).id;
        if (typeof id === "string" || typeof id === "number") tags.add(`${kind}:${id}`);
    };

    addEntity("directory", data.directory);
    addEntity("material", data.material);
    if (Array.isArray(data.directories)) {
        for (const directory of data.directories) addEntity("directory", directory);
    }
    if (Array.isArray(data.materials)) {
        for (const material of data.materials) addEntity("material", material);
    }
    return [...tags];
}

export function invalidateBrowsePath(browsePath: string): void {
    invalidationEpoch += 1;
    browseCache.delete(browsePath);
    // Detach rather than cancel: callers of the old request still settle, but a
    // post-event revalidation starts immediately and owns the in-flight slot.
    inflight.delete(browsePath);
}

export function invalidateBrowseEntity(entityTag: string, currentPath?: string): void {
    invalidationEpoch += 1;
    const invalidated = browseCache.invalidateTag(entityTag);
    if (currentPath !== undefined && !invalidated.includes(currentPath)) {
        browseCache.delete(currentPath);
        invalidated.push(currentPath);
    }
    for (const browsePath of invalidated) inflight.delete(browsePath);
}

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
    const requestEpoch = invalidationEpoch;
    const request = apiFetchRetry<unknown>(endpoint, { timeoutMs: BROWSE_TIMEOUT_MS })
        .then((result) => {
            // An SSE event after this request began makes its snapshot unsafe to
            // retain. The initiating screen may still use it, but the cache will
            // only accept the post-event revalidation.
            if (requestEpoch !== invalidationEpoch) return result;
            // Preserve the previous object identity on an unchanged revalidation
            // so the cache-first render path doesn't trigger a second full
            // re-render of the listing with fresh (memo-defeating) identities.
            const prev = browseCache.get(browsePath);
            if (prev !== undefined && payloadsEqual(prev, result)) {
                return prev;
            }
            browseCache.set(browsePath, result, { tags: browseResourceTags(result) });
            return result;
        })
        .finally(() => {
            if (inflight.get(browsePath) === request) inflight.delete(browsePath);
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
