import { clearAccessToken, getAccessToken, setAccessToken, decodeToken } from "./auth-tokens";

export const API_BASE = (() => {
    // On the server, we use the internal URL to reach the API container directly
    if (typeof window === "undefined") {
        return process.env.API_INTERNAL_URL ?? "http://api:8000";
    }

    const base = process.env.NEXT_PUBLIC_API_URL ?? "/api";
    // Self-healing: Upgrade http to https if the page is HTTPS and it's the same host
    const isHttps = window.location.protocol === "https:";
    if (isHttps && base.startsWith("http://") && base.includes(window.location.host)) {
        return base.replace("http://", "https://");
    }
    return base;
})();


type FetchOptions = RequestInit & {
    skipAuth?: boolean;
};

async function refreshToken(): Promise<string | null> {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: {
            "X-Client-ID": getClientId(),
        },
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data.access_token as string;
}

let _onTokenRefreshed: ((token: string) => void) | null = null;
export function registerTokenRefreshCallback(cb: (token: string) => void) {
    _onTokenRefreshed = cb;
}

export async function lockedRefresh(): Promise<string | null> {
    if (typeof navigator !== "undefined" && navigator.locks) {
        return navigator.locks.request("wikint_refresh", async () => {
            // Another tab may have refreshed while we waited for the lock
            const existing = getAccessToken();
            if (existing) {
                const decoded = decodeToken(existing);
                if (decoded && decoded.exp > Date.now() / 1000 + 30) {
                    return existing;
                }
            }
            return refreshToken();
        });
    }
    // Fallback for browsers without Web Locks
    return refreshToken();
}

let refreshPromise: Promise<string | null> | null = null;

function refreshTokenOnce(): Promise<string | null> {
    if (!refreshPromise) {
        refreshPromise = lockedRefresh().finally(() => { refreshPromise = null; });
    }
    return refreshPromise;
}

export function getClientId(): string {
    if (typeof window === "undefined") return "server-client";
    let clientId = localStorage.getItem("wikint_client_id");
    if (!clientId) {
        clientId = crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).substring(2);
        localStorage.setItem("wikint_client_id", clientId);
    }
    return clientId;
}

export async function apiRequest(
    path: string,
    options: FetchOptions = {},
): Promise<Response> {
    const { skipAuth, ...fetchOptions } = options;
    const headers = new Headers(fetchOptions.headers);

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
        res = await fetch(url, { ...fetchOptions, headers, credentials: "include" });
        // If we got a response (any response), the API is reachable.
        if (typeof window !== "undefined") {
            window.dispatchEvent(new CustomEvent("wikint-api-reachable"));
        }
    } catch (err) {
        // Network error (not a 4xx/5xx response)
        if (typeof window !== "undefined") {
            window.dispatchEvent(new CustomEvent("wikint-api-unreachable"));
        }
        throw err;
    }

    if (res.status === 401 && !skipAuth) {
        console.debug("[api-client] 401 Unauthorized, attempting token refresh...");
        const newToken = await refreshTokenOnce();
        if (newToken) {
            console.debug("[api-client] Token refreshed successfully, retrying request.");
            setAccessToken(newToken);
            _onTokenRefreshed?.(newToken);
            headers.set("Authorization", `Bearer ${newToken}`);
            res = await fetch(url, { ...fetchOptions, headers, credentials: "include" });
            if (typeof window !== "undefined") {
                window.dispatchEvent(new CustomEvent("wikint-api-reachable"));
            }
        } else {
            console.warn("[api-client] Token refresh failed (no new token). Clearing session.");
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

/**
 * Fetch material file content via a two-step presigned URL flow.
 * Step 1: authenticated call to /inline to get a short-lived presigned URL.
 * Step 2: plain fetch (no Authorization header) to the presigned URL.
 * This avoids the S3 400 caused by forwarding auth headers on same-origin redirects.
 */
export async function getMaterialFileUrl(materialId: string): Promise<string> {
    const { url } = await apiFetch<{ url: string }>(`/materials/${materialId}/inline`);
    return url;
}

export async function fetchMaterialFile(materialId: string): Promise<Response> {
    const url = await getMaterialFileUrl(materialId);
    const res = await fetch(url);
    if (!res.ok) throw new ApiError(res.status, `Failed to fetch file: ${res.statusText}`);
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
