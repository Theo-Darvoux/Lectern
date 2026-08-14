import { clearAccessToken, getAccessToken, setAccessToken, decodeToken } from "./auth-tokens";
import type { UserBrief } from "./guest";
import { ResourceCache } from "./resource-cache";

/** Result of a token refresh. `user` is present when the server folds the
 *  caller's profile into the refresh response (reload bootstrap fast-path),
 *  letting the client skip a follow-up `/users/me`. */
export type RefreshResult = { accessToken: string; user: UserBrief | null };

const AUTH_REQUEST_TIMEOUT_MS = 10_000;

export const API_BASE = (() => {
    // On the server, we use the internal URL to reach the API container directly
    if (typeof window === "undefined") {
        return process.env.API_INTERNAL_URL ?? "http://api:8000";
    }

    // Default to the same-origin "/api" path that Nginx proxies. Use `||` (not
    // `??`) so an empty-string env var — e.g. an unset build-arg inlined as "" —
    // still falls back to the default instead of becoming an invalid base.
    const base = process.env.NEXT_PUBLIC_API_URL || "/api";
    // Self-healing: Upgrade http to https if the page is HTTPS and it's the same host
    const isHttps = window.location.protocol === "https:";
    if (isHttps && base.startsWith("http://") && base.includes(window.location.host)) {
        return base.replace("http://", "https://");
    }
    return base;
})();


type FetchOptions = RequestInit & {
    skipAuth?: boolean;
    /** Abort the request after this many ms. Prevents a stalled connection
     *  (e.g. bad wifi: socket alive but no response) from hanging forever. */
    timeoutMs?: number;
};

/** Combine an optional caller signal with an optional per-request timeout.
 * Uses an AbortController rather than AbortSignal.any/timeout so the timeout is
 * not silently lost on browsers that support only part of the newer API. */
function withTimeout(signal: AbortSignal | null | undefined, timeoutMs?: number): AbortSignal | undefined {
    if (!timeoutMs || typeof AbortController === "undefined") {
        return signal ?? undefined;
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const abortFromCaller = () => controller.abort(signal?.reason);

    controller.signal.addEventListener("abort", () => {
        clearTimeout(timer);
        signal?.removeEventListener("abort", abortFromCaller);
    }, { once: true });

    if (signal) {
        if (signal.aborted) {
            abortFromCaller();
        } else {
            signal.addEventListener("abort", abortFromCaller, { once: true });
        }
    }

    return controller.signal;
}

/** Whether a failed request is worth retrying (transient infra/network), as
 *  opposed to a deterministic 4xx that will fail again. Real cancellations are
 *  the caller's responsibility to filter out before calling this. */
export function isRetriableError(err: unknown): boolean {
    if (err instanceof TypeError) return true; // fetch network failure
    if (err instanceof DOMException && (err.name === "TimeoutError" || err.name === "AbortError")) {
        return true;
    }
    if (err instanceof ApiError) return err.status >= 500 || err.status === 0;
    return false;
}

/** apiFetch with a bounded, backed-off retry for transient failures. Use only
 *  for idempotent (GET) requests. */
export async function apiFetchRetry<T>(
    path: string,
    options: FetchOptions & { retries?: number; retryBaseDelayMs?: number } = {},
): Promise<T> {
    const { retries = 2, retryBaseDelayMs = 400, ...fetchOptions } = options;
    let lastErr: unknown;
    for (let attempt = 0; attempt <= retries; attempt++) {
        try {
            return await apiFetch<T>(path, fetchOptions);
        } catch (err) {
            lastErr = err;
            // Stop early if the caller's own signal aborted (navigation/unmount).
            if (fetchOptions.signal?.aborted) throw err;
            if (attempt < retries && isRetriableError(err)) {
                await new Promise((r) => setTimeout(r, retryBaseDelayMs * 2 ** attempt));
                continue;
            }
            throw err;
        }
    }
    throw lastErr;
}

async function refreshToken(): Promise<RefreshResult | null> {
    let res: Response;
    try {
        res = await fetch(`${API_BASE}/auth/refresh`, {
            method: "POST",
            credentials: "include",
            headers: {
                "X-Client-ID": getClientId(),
            },
            signal: withTimeout(undefined, AUTH_REQUEST_TIMEOUT_MS),
        });
    } catch (err) {
        // Network connection error
        throw err;
    }

    if (!res.ok) {
        // Treat 5xx and 429 as transient/retriable errors. Do not clear the session.
        if (res.status >= 500 || res.status === 429) {
            throw new ApiError(res.status, res.statusText);
        }
        return null;
    }
    const data = await res.json();
    return { accessToken: data.access_token as string, user: (data.user ?? null) as UserBrief | null };
}

let _onTokenRefreshed: ((token: string) => void) | null = null;
export function registerTokenRefreshCallback(cb: (token: string) => void) {
    _onTokenRefreshed = cb;
}

export async function lockedRefresh(): Promise<RefreshResult | null> {
    if (typeof navigator !== "undefined" && navigator.locks) {
        return navigator.locks.request("lectern_refresh", async () => {
            // Another tab may have refreshed while we waited for the lock
            const existing = getAccessToken();
            if (existing) {
                const decoded = decodeToken(existing);
                if (decoded && decoded.exp > Date.now() / 1000 + 30) {
                    return { accessToken: existing, user: null };
                }
            }
            return refreshToken();
        });
    }
    // Fallback for browsers without Web Locks
    return refreshToken();
}

let refreshPromise: Promise<RefreshResult | null> | null = null;

function refreshTokenOnce(): Promise<RefreshResult | null> {
    if (!refreshPromise) {
        refreshPromise = lockedRefresh().finally(() => { refreshPromise = null; });
    }
    return refreshPromise;
}

export function getClientId(): string {
    if (typeof window === "undefined") return "server-client";
    let clientId = localStorage.getItem("lectern_client_id");
    if (!clientId) {
        clientId = crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).substring(2);
        localStorage.setItem("lectern_client_id", clientId);
    }
    return clientId;
}

export async function apiRequest(
    path: string,
    options: FetchOptions = {},
): Promise<Response> {
    const { skipAuth, timeoutMs, ...fetchOptions } = options;
    const headers = new Headers(fetchOptions.headers);
    // A 401 refresh/retry is a new network attempt and must receive a fresh
    // timeout budget. The caller's cancellation signal is still shared.
    const createAttemptSignal = () => withTimeout(fetchOptions.signal, timeoutMs);

    headers.set("X-Client-ID", getClientId());

    if (!skipAuth) {
        const token = getAccessToken();
        if (token) {
            headers.set("Authorization", `Bearer ${token}`);
        }
    }

    if (!headers.has("Content-Type") && fetchOptions.body && typeof fetchOptions.body === "string") {
        headers.set("Content-Type", "application/json");
    }

    const url = path.startsWith("http") ? path : `${API_BASE}${path}`;
    let res: Response;
    try {
        res = await fetch(url, {
            ...fetchOptions,
            headers,
            credentials: "include",
            signal: createAttemptSignal(),
        });
        // If we got a response (any response), the API is reachable.
        if (typeof window !== "undefined") {
            window.dispatchEvent(new CustomEvent("lectern-api-reachable"));
        }
    } catch (err) {
        // Network error (not a 4xx/5xx response)
        if (typeof window !== "undefined") {
            window.dispatchEvent(new CustomEvent("lectern-api-unreachable"));
        }
        throw err;
    }

    if (res.status === 401 && !skipAuth) {
        console.debug("[api-client] 401 Unauthorized, attempting token refresh...");
        try {
            const refreshed = await refreshTokenOnce();
            if (refreshed) {
                console.debug("[api-client] Token refreshed successfully, retrying request.");
                const newToken = refreshed.accessToken;
                setAccessToken(newToken);
                _onTokenRefreshed?.(newToken);
                headers.set("Authorization", `Bearer ${newToken}`);
                res = await fetch(url, {
                    ...fetchOptions,
                    headers,
                    credentials: "include",
                    signal: createAttemptSignal(),
                });
                if (typeof window !== "undefined") {
                    window.dispatchEvent(new CustomEvent("lectern-api-reachable"));
                }
            } else {
                console.warn("[api-client] Token refresh failed (no new token). Clearing session.");
                clearAccessToken();
                throw new ApiError(401, "Session expired");
            }
        } catch (err) {
            console.error("[api-client] Token refresh encountered an error:", err);
            if (isRetriableError(err)) {
                // Transient error: propagate it without clearing the session
                throw err;
            }
            console.warn("[api-client] Token refresh failed (non-retriable error). Clearing session.");
            clearAccessToken();
            throw new ApiError(401, "Session expired");
        }
    }

    if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }));
        let message = body.detail ?? "Unknown error";
        if (Array.isArray(body.detail)) {
            message = body.detail.map((err: Record<string, unknown>) => err.msg || err.detail || JSON.stringify(err)).join(", ");
        }
        throw new ApiError(res.status, message, body.error_code);
    }

    return res;
}

export async function apiFetch<T>(
    path: string,
    options: FetchOptions = {},
): Promise<T> {
    const res = await apiRequest(path, options);
    if (res.status === 204) return undefined as T;
    return res.json() as Promise<T>;
}

export async function apiFetchWithResponse<T>(
    path: string,
    options: FetchOptions = {},
): Promise<{ data: T; response: Response }> {
    const response = await apiRequest(path, options);
    if (response.status === 204) return { data: undefined as T, response };
    const data = await response.json() as T;
    return { data, response };
}

export async function apiFetchBlob(
    path: string,
    options: FetchOptions = {},
): Promise<Blob> {
    const res = await apiRequest(path, options);
    return res.blob();
}

// ─── Presigned URL cache ─────────────────────────────────────────────────────
// Cloudflare CDN caches by full URL. Generating a new presigned URL on every
// file open always produces a cache miss → R2 storage backend (~15 Mbps cap).
// Reusing the same URL within its TTL hits the CDN edge → full speed.
// The server issues URLs with a 15-min TTL; we cache for 12 min to stay safe.
const _URL_CACHE_TTL_MS = 12 * 60 * 1000;
const _urlCache = new ResourceCache<string, string>({
    maxEntries: 256,
    ttlMs: _URL_CACHE_TTL_MS,
});

export async function getMaterialFileUrl(
    materialId: string,
    signal?: AbortSignal,
): Promise<string> {
    const cached = _urlCache.get(materialId);
    if (cached) return cached;
    const { url } = await apiFetch<{ url: string }>(`/materials/${materialId}/inline`, {
        signal,
        timeoutMs: 15_000,
    });
    _urlCache.set(materialId, url, { tags: [`material:${materialId}`] });
    return url;
}

/** Drop the cached presigned URL for a material so the next read re-issues a
 *  fresh one. Call after a material's content changes (e.g. a new version) —
 *  otherwise the stale URL keeps pointing at the previous version's file_key. */
export function invalidateMaterialFileUrl(materialId: string): void {
    _urlCache.delete(materialId);
}

export async function fetchMaterialFile(materialId: string, signal?: AbortSignal): Promise<Response> {
    const url = await getMaterialFileUrl(materialId, signal);
    const res = await fetch(url, signal ? { signal } : undefined);
    if (!res.ok) {
        // A failed signed-URL fetch is usually a stale/expired token or an edge
        // error; drop the cached URL so the next attempt re-issues a fresh one.
        _urlCache.delete(materialId);
        throw new ApiError(res.status, `Failed to fetch file: ${res.statusText}`);
    }
    return res;
}


export async function fetchMaterialBlob(materialId: string): Promise<Blob> {
    const res = await fetchMaterialFile(materialId);
    return res.blob();
}

export class ApiError extends Error {
    constructor(
        public status: number,
        message: string,
        public error_code?: string,
    ) {
        super(message);
        this.name = "ApiError";
    }
}
