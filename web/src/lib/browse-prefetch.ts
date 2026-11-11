"use client";

import { queryOptions } from "@tanstack/react-query";
import { apiFetch } from "./api-client";
import { getQueryClient } from "./query-client";

// A directory listing is a small JSON payload, so it should return quickly.
// Cap each attempt so a stalled connection cannot leave the listing skeleton
// pending forever.
const BROWSE_TIMEOUT_MS = 15_000;
// Hover prefetches and link resolution reuse a recent copy instead of refetching.
const PREFETCH_FRESH_MS = 5 * 60 * 1000;

/** Browse API response for a path (the part after /browse/, "" for the root). */
export function browseQuery(browsePath: string) {
    return queryOptions({
        queryKey: ["browse", browsePath],
        queryFn: ({ signal }) =>
            apiFetch<unknown>(browsePath ? `/browse/${browsePath}` : "/browse", {
                signal,
                timeoutMs: BROWSE_TIMEOUT_MS,
            }),
    });
}

/** The directory and material entities a browse payload shows, as `kind:id` tags. */
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
    void getQueryClient().invalidateQueries({ queryKey: ["browse", browsePath], exact: true });
}

/**
 * Invalidate every cached browse path showing an entity, plus the current
 * path. Paths on screen refetch at once; a fetch already in flight restarts,
 * so a snapshot taken before the change is never kept.
 */
export function invalidateBrowseEntity(entityTag: string, currentPath?: string): void {
    void getQueryClient().invalidateQueries({
        predicate: (query) =>
            query.queryKey[0] === "browse" &&
            (query.queryKey[1] === currentPath ||
                browseResourceTags(query.state.data).includes(entityTag)),
    });
}

/** Fetch a browse path, reusing a recent cached copy. */
export function fetchBrowsePath(browsePath: string): Promise<unknown> {
    return getQueryClient().fetchQuery({ ...browseQuery(browsePath), staleTime: PREFETCH_FRESH_MS });
}

export async function prefetchBrowsePath(browsePath: string): Promise<void> {
    // prefetchQuery never throws: a failed prefetch leaves navigation to fetch again.
    await getQueryClient().prefetchQuery({ ...browseQuery(browsePath), staleTime: PREFETCH_FRESH_MS });
}
