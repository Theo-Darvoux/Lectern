import { apiFetch, apiFetchRetry } from "./api-client";

export type ThumbnailType = "webp" | "fallback" | null;

export interface MaterialThumbnail {
    url: string;
    thumbnailType: ThumbnailType;
}

interface ThumbnailResponse {
    url: string;
    thumbnail_type: ThumbnailType;
}

interface CacheEntry {
    value: MaterialThumbnail;
    expiresAt: number;
}

const CACHE_TTL_MS = 10 * 60 * 1000;
const MAX_CACHE_ENTRIES = 256;
const RECALCULATE_POLL_INTERVAL_MS = 1000;
const RECALCULATE_POLL_TIMEOUT_MS = 5 * 60 * 1000;
const cache = new Map<string, CacheEntry>();
const inFlight = new Map<string, Promise<MaterialThumbnail>>();
const materialGenerations = new Map<string, number>();
const MAX_CONCURRENT_REQUESTS = 4;
let activeRequests = 0;
const requestQueue: Array<() => void> = [];
let cacheGeneration = 0;

function runBounded<T>(task: () => Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
        const run = () => {
            activeRequests += 1;
            task()
                .then(resolve, reject)
                .finally(() => {
                    activeRequests -= 1;
                    requestQueue.shift()?.();
                });
        };
        if (activeRequests < MAX_CONCURRENT_REQUESTS) run();
        else requestQueue.push(run);
    });
}

function rememberMaterialThumbnail(materialId: string, thumbnail: MaterialThumbnail): void {
    cache.delete(materialId);
    cache.set(materialId, {
        value: thumbnail,
        expiresAt: Date.now() + CACHE_TTL_MS,
    });
    while (cache.size > MAX_CACHE_ENTRIES) {
        const oldestKey = cache.keys().next().value;
        if (oldestKey === undefined) break;
        cache.delete(oldestKey);
    }
}

async function fetchMaterialThumbnailFresh(materialId: string): Promise<MaterialThumbnail> {
    const result = await apiFetchRetry<ThumbnailResponse>(`/materials/${materialId}/thumbnail`);
    if (!result.url) throw new Error("Thumbnail response did not include a URL");
    return {
        url: result.url,
        thumbnailType: result.thumbnail_type ?? null,
    };
}

function materialGeneration(materialId: string): number {
    return materialGenerations.get(materialId) ?? 0;
}

function dropMaterialThumbnailCache(materialId: string): void {
    materialGenerations.set(materialId, materialGeneration(materialId) + 1);
    cache.delete(materialId);
    inFlight.delete(materialId);
}

function thumbnailObjectPath(url: string): string {
    try {
        return new URL(url, "http://wikint.local").pathname;
    } catch {
        return url.split("?", 1)[0].split("#", 1)[0];
    }
}

function isPublishedRegeneration(
    previous: MaterialThumbnail | null,
    current: MaterialThumbnail,
): boolean {
    if (current.thumbnailType !== "webp") return false;
    if (!previous || previous.thumbnailType !== "webp") return true;
    return thumbnailObjectPath(current.url) !== thumbnailObjectPath(previous.url);
}

function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

export function getMaterialThumbnail(materialId: string): Promise<MaterialThumbnail> {
    const cached = cache.get(materialId);
    if (cached && cached.expiresAt > Date.now()) {
        // Map insertion order provides a small LRU without another index.
        cache.delete(materialId);
        cache.set(materialId, cached);
        return Promise.resolve(cached.value);
    }
    cache.delete(materialId);

    const pending = inFlight.get(materialId);
    if (pending) return pending;

    const generation = cacheGeneration;
    const itemGeneration = materialGeneration(materialId);
    const request = runBounded(() => fetchMaterialThumbnailFresh(materialId))
        .then((thumbnail) => {
            if (
                generation === cacheGeneration &&
                itemGeneration === materialGeneration(materialId)
            ) {
                rememberMaterialThumbnail(materialId, thumbnail);
            }
            return thumbnail;
        })
        .finally(() => {
            if (inFlight.get(materialId) === request) inFlight.delete(materialId);
        });
    inFlight.set(materialId, request);
    return request;
}

const listeners = new Set<(materialId: string, timestamp: number) => void>();

function notifyMaterialThumbnail(materialId: string, timestamp: number): void {
    listeners.forEach((listener) => {
        try {
            listener(materialId, timestamp);
        } catch {
            // ignore listener errors
        }
    });
}

export function subscribeMaterialThumbnail(
    callback: (materialId: string, timestamp: number) => void,
): () => void {
    listeners.add(callback);
    return () => {
        listeners.delete(callback);
    };
}

export function clearMaterialPreviewCache(): void {
    cacheGeneration += 1;
    cache.clear();
    inFlight.clear();
    materialGenerations.clear();
}

export function invalidateMaterialThumbnail(materialId: string, timestamp: number = Date.now()): void {
    dropMaterialThumbnailCache(materialId);
    notifyMaterialThumbnail(materialId, timestamp);
}

async function waitForPublishedRegeneration(
    materialId: string,
    previous: MaterialThumbnail | null,
): Promise<MaterialThumbnail> {
    const deadline = Date.now() + RECALCULATE_POLL_TIMEOUT_MS;

    while (Date.now() < deadline) {
        await sleep(RECALCULATE_POLL_INTERVAL_MS);
        try {
            const current = await runBounded(() => fetchMaterialThumbnailFresh(materialId));
            if (isPublishedRegeneration(previous, current)) return current;
        } catch {
            // The worker may temporarily leave no dedicated thumbnail while the
            // regeneration is still running. Keep polling until the deadline.
        }
    }

    throw new Error("Timed out waiting for thumbnail regeneration");
}

export async function recalculateMaterialThumbnail(materialId: string): Promise<void> {
    let previous: MaterialThumbnail | null = null;
    try {
        previous = await getMaterialThumbnail(materialId);
    } catch {
        // A material with no existing thumbnail can still be regenerated.
    }

    const res = await apiFetch<{ status: string; timestamp?: number }>(
        `/materials/${materialId}/recalculate-thumbnail`,
        { method: "POST" },
    );

    // Prevent an older in-flight thumbnail request from repopulating the local
    // cache after the regeneration request has been accepted.
    dropMaterialThumbnailCache(materialId);

    if (res?.status === "ok") {
        notifyMaterialThumbnail(materialId, res.timestamp ?? Date.now());
        return;
    }
    if (res?.status !== "queued") {
        throw new Error("Thumbnail regeneration failed");
    }

    // The API only waits briefly for ARQ. If the job is still queued, do not
    // announce success yet: wait until the thumbnail endpoint points at the new
    // immutable object key. Query-string/token changes alone do not count.
    const regenerated = await waitForPublishedRegeneration(materialId, previous);
    rememberMaterialThumbnail(materialId, regenerated);
    notifyMaterialThumbnail(materialId, Date.now());
}
