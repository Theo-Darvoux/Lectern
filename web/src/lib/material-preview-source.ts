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
const cache = new Map<string, CacheEntry>();
const inFlight = new Map<string, Promise<MaterialThumbnail>>();
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
    const request = runBounded(() =>
        apiFetchRetry<ThumbnailResponse>(`/materials/${materialId}/thumbnail`),
    )
        .then((result) => {
            if (!result.url) throw new Error("Thumbnail response did not include a URL");
            const thumbnail = {
                url: result.url,
                thumbnailType: result.thumbnail_type ?? null,
            } satisfies MaterialThumbnail;
            if (generation === cacheGeneration) {
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
            return thumbnail;
        })
        .finally(() => {
            if (inFlight.get(materialId) === request) inFlight.delete(materialId);
        });
    inFlight.set(materialId, request);
    return request;
}

const listeners = new Set<(materialId: string, timestamp: number) => void>();

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
}

export function invalidateMaterialThumbnail(materialId: string, timestamp: number = Date.now()): void {
    cache.delete(materialId);
    inFlight.delete(materialId);
    listeners.forEach((listener) => {
        try {
            listener(materialId, timestamp);
        } catch {
            // ignore listener errors
        }
    });
}

export async function recalculateMaterialThumbnail(materialId: string): Promise<void> {
    const res = await apiFetch<{ status: string; timestamp?: number }>(
        `/materials/${materialId}/recalculate-thumbnail`,
        { method: "POST" },
    );
    const ts = res?.timestamp ?? Date.now();
    invalidateMaterialThumbnail(materialId, ts);
}


