"use client";

import { useCallback, useEffect, useState } from "react";
import { apiRequest, fetchMaterialFile, ApiError } from "@/lib/api-client";

interface UseMaterialFileOptions {
    materialId: string;
    fileKey: string;
    mode?: "text" | "blob" | "arrayBuffer" | "url";
    maxBytes?: number;
}

interface UseMaterialFileReturn {
    content: string;
    blobUrl: string | null;
    arrayBuffer: ArrayBuffer | null;
    loading: boolean;
    error: string | null;
    truncated: boolean;
    /** Re-run the fetch (e.g. from a "Retry" button) without a full page reload. */
    reload: () => void;
}

// On a flaky connection the body of an otherwise-successful response can arrive
// empty or truncated without throwing. Retrying transient failures automatically
// means the viewer self-heals instead of staying blank until a manual refresh.
const MAX_ATTEMPTS = 3;
const RETRY_BASE_DELAY_MS = 400;
// Per-attempt ceiling so a stalled body read can't hang the viewer forever.
// Generous enough not to abort legitimately slow downloads on bad wifi.
const REQUEST_TIMEOUT_MS = 60_000;

class IncompleteDownloadError extends Error {
    constructor(message: string) {
        super(message);
        this.name = "IncompleteDownloadError";
    }
}

/** A response whose body is gzip/br-compressed reports a content-length that is
 *  smaller than the decoded size the browser hands us, so skip the length check. */
function isContentEncoded(res: Response): boolean {
    const enc = res.headers.get("content-encoding");
    return !!enc && enc.toLowerCase() !== "identity";
}

/** Throw if the downloaded payload is empty or shorter than its declared length. */
function assertComplete(actualBytes: number, res: Response): void {
    if (actualBytes === 0) {
        throw new IncompleteDownloadError("Downloaded file was empty");
    }
    const declared = res.headers.get("content-length");
    if (declared && !isContentEncoded(res)) {
        const expected = Number(declared);
        if (Number.isFinite(expected) && expected > 0 && actualBytes < expected) {
            throw new IncompleteDownloadError(
                `Download truncated (${actualBytes}/${expected} bytes)`,
            );
        }
    }
}

/** Transient errors are worth retrying; deterministic ones (4xx) are not.
 *  Real cancellations (unmount / new request) are filtered out by the caller
 *  before this runs, so any abort/timeout reaching here is a per-attempt
 *  timeout — which browsers may surface as either TimeoutError or AbortError. */
function isTransient(err: unknown): boolean {
    if (err instanceof IncompleteDownloadError) return true;
    // fetch() network failures surface as TypeError.
    if (err instanceof TypeError) return true;
    if (err instanceof DOMException && (err.name === "TimeoutError" || err.name === "AbortError")) {
        return true;
    }
    if (err instanceof ApiError) return err.status >= 500 || err.status === 0;
    return false;
}

/** Combine the caller's cancel signal with a per-attempt timeout, when supported. */
function attemptSignal(cancelSignal: AbortSignal): AbortSignal {
    if (typeof AbortSignal !== "undefined" && "any" in AbortSignal && "timeout" in AbortSignal) {
        return AbortSignal.any([cancelSignal, AbortSignal.timeout(REQUEST_TIMEOUT_MS)]);
    }
    return cancelSignal;
}

export function useMaterialFile({
    materialId,
    fileKey,
    mode = "text",
    maxBytes,
}: UseMaterialFileOptions): UseMaterialFileReturn {
    const [content, setContent] = useState("");
    const [blobUrl, setBlobUrl] = useState<string | null>(null);
    const [arrayBuffer, setArrayBuffer] = useState<ArrayBuffer | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [truncated, setTruncated] = useState(false);
    const [reloadKey, setReloadKey] = useState(0);

    const reload = useCallback(() => setReloadKey((k) => k + 1), []);

    useEffect(() => {
        let cancelled = false;
        const controller = new AbortController();
        let objectUrl: string | null = null;

        // A single fetch attempt. Throws on failure (so the retry loop can react),
        // sets the relevant state on success.
        const runAttempt = async (): Promise<void> => {
            if (mode === "url") {
                const { getMaterialFileUrl } = await import("@/lib/api-client");
                const url = await getMaterialFileUrl(materialId);
                if (!cancelled) setBlobUrl(url);
                return;
            }

            if (mode === "text") {
                // Text files are gzip-compressed in R2. Fetching via the raw worker
                // URL can produce garbled output if Cloudflare's cache decompresses
                // the body transparently while keeping the content-encoding header,
                // causing double-decompression. The /text-content endpoint handles
                // decompression correctly server-side and returns clean UTF-8 text.
                const res = await apiRequest(`/materials/${materialId}/text-content`, {
                    signal: attemptSignal(controller.signal),
                });
                let text = await res.text();
                let didTruncate = false;
                if (maxBytes && text.length > maxBytes) {
                    text = text.slice(0, maxBytes);
                    didTruncate = true;
                }
                if (!cancelled) {
                    setContent(text);
                    setTruncated(didTruncate);
                }
                return;
            }

            const res = await fetchMaterialFile(materialId, attemptSignal(controller.signal));

            if (mode === "blob") {
                const blob = await res.blob();
                assertComplete(blob.size, res);
                if (!cancelled) {
                    objectUrl = URL.createObjectURL(blob);
                    setBlobUrl(objectUrl);
                }
            } else {
                const buffer = await res.arrayBuffer();
                assertComplete(buffer.byteLength, res);
                if (!cancelled) setArrayBuffer(buffer);
            }
        };

        const backoff = (attempt: number) =>
            new Promise<void>((resolve) => {
                const ms = RETRY_BASE_DELAY_MS * 2 ** (attempt - 1);
                const timer = setTimeout(resolve, ms);
                controller.signal.addEventListener(
                    "abort",
                    () => {
                        clearTimeout(timer);
                        resolve();
                    },
                    { once: true },
                );
            });

        const load = async () => {
            // Reset state on start (deferred slightly to avoid flicker if cache-busting)
            queueMicrotask(() => {
                if (cancelled) return;
                setLoading(true);
                setError(null);
                setTruncated(false);
                if (mode === "text") setContent("");
                else if (mode === "blob" || mode === "url") setBlobUrl(null);
                else setArrayBuffer(null);
            });

            for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
                try {
                    await runAttempt();
                    if (!cancelled) setLoading(false);
                    return;
                } catch (err) {
                    // The effect was torn down (unmount / new request) — stay silent.
                    if (cancelled || controller.signal.aborted) return;

                    if (attempt < MAX_ATTEMPTS && isTransient(err)) {
                        await backoff(attempt);
                        if (cancelled || controller.signal.aborted) return;
                        continue;
                    }

                    setError(err instanceof Error ? err.message : "Failed to load material file");
                    setLoading(false);
                    return;
                }
            }
        };

        load();

        return () => {
            cancelled = true;
            controller.abort();
            if (objectUrl) {
                URL.revokeObjectURL(objectUrl);
            }
        };
    }, [materialId, fileKey, mode, maxBytes, reloadKey]);

    return { content, blobUrl, arrayBuffer, loading, error, truncated, reload };
}
