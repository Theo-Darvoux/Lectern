import * as tus from "tus-js-client";
import { API_BASE, ApiError, apiRequest, ensureFreshAccessToken, getClientId } from "@/lib/api-client";
import { getAccessToken } from "@/lib/auth-tokens";
import { compressImageIfNeeded } from "@/lib/file-utils";
import { withMalwareErrorPrefix } from "@/lib/upload-errors";

type UploadStatus = "pending" | "processing" | "clean" | "malicious" | "failed" | "applied";

interface UploadFileOptions {
    /**
     * Progress callback. Values:
     *   0-80    Transfer to storage
     *   80-99   Server-side processing (via SSE overall_percent)
     *   100     Complete
     */
    onProgress?: (pct: number) => void;
    /** Called with granular status messages (e.g. "Scanning for malware…"). */
    onStatusUpdate?: (status: string, stageIndex?: number, stageTotal?: number) => void;
    /** Called with raw byte progress. */
    onBytesProgress?: (uploaded: number, total: number) => void;
    /** Called once the bytes are stored and server-side processing has been requested. */
    onTransferred?: (quarantineKey: string) => void;
    /** Called when a tus Location URL is available (for resuming). */
    onTusUrlAvailable?: (url: string) => void;
    /** Called when a multipart session exists (for resuming). */
    onMultipartStarted?: (uploadId: string) => void;
    signal?: AbortSignal;
    /** Stable UUID for this upload attempt — enables server-side idempotency on retry. */
    uploadId?: string;
    /** Server-issued bounded admission capability for one batch. */
    uploadGroupId?: string;
    /** tus URL of an earlier attempt to continue. */
    tusUrl?: string;
    /** Multipart session of an earlier attempt to continue. */
    multipartUploadId?: string;
    /** Keep the original image bytes instead of recompressing large images. */
    skipCompression?: boolean;
    /** Translation function for status messages (keys from Upload namespace). */
    t?: (key: string) => string;
}

/**
 * Returns the logical file size — i.e. what the user will see when they download the file.
 * For gzip-encoded objects S3 decompresses transparently, so the download size equals
 * the pre-compression original, not the stored (compressed) bytes.
 */
export function logicalFileSize(result: Pick<UploadResult, "size" | "original_size" | "content_encoding">): number {
    return result.content_encoding === "gzip" ? result.original_size : result.size;
}

interface UploadResult {
    file_key: string;
    size: number;
    /** Size before server-side compression/optimisation. Equals size if unchanged. */
    original_size: number;
    mime_type: string;
    /** Content-Encoding set on the S3 object (e.g. "gzip"). Null for binary formats. */
    content_encoding: string | null;
    /** Server-corrected filename (e.g. misnamed .wav → .flac). */
    correctedName: string;
    /** True when client-side image compression was applied before upload. */
    wasCompressed: boolean;
}

interface DirectUploadResponse {
    upload_id: string;
    file_key: string;
    status: UploadStatus;
    size: number;
    mime_type: string;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const UPLOAD_CANCELLED = "Upload cancelled";

/** Smallest file the multipart endpoint accepts. */
const MULTIPART_MIN_BYTES = 5 * 1024 * 1024;

/** Parallel part transfers per multipart upload. */
const MULTIPART_CONCURRENCY = 4;

/** tus chunk size — must satisfy S3 minimum part size (5 MiB) for non-final parts. */
const TUS_CHUNK_SIZE = 8 * 1024 * 1024;

const TUS_RETRY_DELAYS = [0, 1_000, 3_000, 5_000, 10_000, 20_000, 30_000];

/** A transfer that makes no progress for this long is treated as a lost connection. */
const STALL_TIMEOUT_MS = 60_000;

/** Time the server may take to answer once the request body has been sent. */
const RESPONSE_TIMEOUT_MS = 120_000;

/** Concurrent processing watchers; each one holds an SSE connection. */
const MAX_PROCESSING_WATCHERS = 4;

const MAX_TRANSFER_ATTEMPTS = 5;

// ── Internal types ────────────────────────────────────────────────────────────

interface MultipartSession {
    quarantine_key: string;
    upload_id: string;
    s3_multipart_id: string;
    parts: Array<{
        part_number: number;
        size: number;
        url?: string | null;
        etag?: string | null;
    }>;
}

interface UploadEventPayload {
    file_key: string;
    status: UploadStatus;
    detail?: string | null;
    error_code?: string | null;
    result?: {
        file_key: string;
        file_name?: string;
        size: number;
        original_size: number;
        mime_type: string;
        content_encoding?: string | null;
    } | null;
    stage?: string | null;
    stage_index?: number | null;
    stage_total?: number | null;
    stage_percent?: number | null;
    overall_percent?: number | null;
}

export interface UploadConfig {
    allowed_extensions: string[];
    allowed_mimetypes: string[];
    max_file_size_mb: number;
    max_size_mb_by_mime: Record<string, number>;
    recommended_path: "direct" | "tus";
    direct_threshold_mb: number;
    batch_max_zip_size_bytes?: number;
    batch_max_total_extracted_bytes?: number;
    batch_max_files?: number;
    batch_max_files_privileged?: number;
    batch_max_path_depth?: number;
}

/** Resolve the same MIME-specific upload cap advertised by the API. */
export function uploadLimitMbForMime(
    config: UploadConfig | null,
    mimeType: string,
    fallbackMb: number,
): number {
    return config?.max_size_mb_by_mime[mimeType] ?? config?.max_file_size_mb ?? fallbackMb;
}

let _configCache: UploadConfig | null = null;
let _configCacheTime = 0;
const CONFIG_CACHE_TTL = 5 * 60 * 1000; // 5 minutes
const UPLOAD_CONTROL_TIMEOUT_MS = 30_000;

/** Fetch upload configuration (allowed types, size limits) from the backend. */
export async function getUploadConfig(): Promise<UploadConfig> {
    const now = Date.now();
    if (_configCache && (now - _configCacheTime < CONFIG_CACHE_TTL)) {
        return _configCache;
    }
    const resp = await apiRequest("/upload/config", { timeoutMs: UPLOAD_CONTROL_TIMEOUT_MS });
    const config = await resp.json() as UploadConfig;
    _configCache = config;
    _configCacheTime = now;
    return config;
}

export interface UploadGroup {
    group_id: string;
    max_files: number;
    expires_in: number;
}

/** Admit a folder once; contained file IDs consume its bounded slots. */
export async function beginUploadGroup(
    fileCount: number,
    signal?: AbortSignal,
): Promise<UploadGroup> {
    const response = await apiRequest("/upload/groups", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_count: fileCount }),
        signal,
        timeoutMs: UPLOAD_CONTROL_TIMEOUT_MS,
    });
    return response.json() as Promise<UploadGroup>;
}

function addUploadAdmissionHeaders(
    headers: Record<string, string>,
    options: Pick<UploadFileOptions, "uploadId" | "uploadGroupId">,
): void {
    if (options.uploadId) headers["X-Upload-ID"] = options.uploadId;
    if (options.uploadGroupId) headers["X-Upload-Group-ID"] = options.uploadGroupId;
}

// ── Retry helpers ─────────────────────────────────────────────────────────────

/** The connection dropped or stalled; the same request may be sent again. */
class TransferInterruptedError extends Error {
    override readonly name = "TransferInterruptedError";
}

/** The browser cannot use object storage directly, e.g. bucket CORS hides ETag. */
class StorageUnreachableError extends Error {
    override readonly name = "StorageUnreachableError";
    constructor(message: string, readonly multipartUploadId?: string) {
        super(message);
    }
}

function _isRetryable(err: unknown): boolean {
    if (err instanceof ApiError) return err.status === 408 || err.status === 429 || err.status >= 500;
    return (
        err instanceof TransferInterruptedError ||
        err instanceof TypeError ||
        (err instanceof DOMException && err.name === "AbortError")
    );
}

function _sleep(ms: number, signal?: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
        if (signal?.aborted) {
            reject(new Error(UPLOAD_CANCELLED));
            return;
        }
        const onAbort = () => {
            clearTimeout(timer);
            reject(new Error(UPLOAD_CANCELLED));
        };
        const timer = setTimeout(() => {
            signal?.removeEventListener("abort", onAbort);
            resolve();
        }, ms);
        signal?.addEventListener("abort", onAbort, { once: true });
    });
}

/** Run an idempotent request, backing off on transient failures. */
async function _withRetry<T>(attempt: () => Promise<T>, signal?: AbortSignal): Promise<T> {
    for (let attempts = 1; ; attempts++) {
        try {
            return await attempt();
        } catch (err) {
            if (signal?.aborted) throw new Error(UPLOAD_CANCELLED);
            if (attempts >= MAX_TRANSFER_ATTEMPTS || !_isRetryable(err)) throw err;
            await _sleep(Math.min(1_000 * 2 ** (attempts - 1), 15_000) + Math.random() * 250, signal);
        }
    }
}

// ── XHR transport (fetch cannot report upload progress) ───────────────────────

interface XhrRequest {
    method: "POST" | "PUT";
    url: string;
    body: XMLHttpRequestBodyInit;
    headers?: Record<string, string>;
    withCredentials?: boolean;
    signal?: AbortSignal;
    onProgress?: (loaded: number, total: number) => void;
}

function _xhr(request: XhrRequest): Promise<XMLHttpRequest> {
    return new Promise((resolve, reject) => {
        if (request.signal?.aborted) {
            reject(new Error(UPLOAD_CANCELLED));
            return;
        }

        const xhr = new XMLHttpRequest();
        let stalled = false;
        let watchdog: ReturnType<typeof setTimeout> | undefined;
        const armWatchdog = (ms: number) => {
            clearTimeout(watchdog);
            watchdog = setTimeout(() => {
                stalled = true;
                xhr.abort();
            }, ms);
        };
        const onAbort = () => xhr.abort();
        const settle = () => {
            clearTimeout(watchdog);
            request.signal?.removeEventListener("abort", onAbort);
        };

        xhr.open(request.method, request.url);
        xhr.withCredentials = request.withCredentials ?? false;
        for (const [name, value] of Object.entries(request.headers ?? {})) {
            xhr.setRequestHeader(name, value);
        }
        xhr.upload.onprogress = (event) => {
            armWatchdog(STALL_TIMEOUT_MS);
            if (event.lengthComputable) request.onProgress?.(event.loaded, event.total);
        };
        xhr.upload.onload = () => armWatchdog(RESPONSE_TIMEOUT_MS);
        xhr.onload = () => {
            settle();
            resolve(xhr);
        };
        xhr.onerror = () => {
            settle();
            reject(new TransferInterruptedError("Network error during upload"));
        };
        xhr.onabort = () => {
            settle();
            reject(stalled ? new TransferInterruptedError("Upload stalled") : new Error(UPLOAD_CANCELLED));
        };

        request.signal?.addEventListener("abort", onAbort, { once: true });
        armWatchdog(STALL_TIMEOUT_MS);
        xhr.send(request.body);
    });
}

function _apiErrorFromBody(status: number, body: string, fallback: string): ApiError {
    try {
        const parsed = JSON.parse(body) as { detail?: unknown; error_code?: string };
        const detail = typeof parsed.detail === "string" ? parsed.detail : fallback;
        return new ApiError(status, detail, parsed.error_code);
    } catch {
        return new ApiError(status, fallback);
    }
}

function _authHeaders(options: Pick<UploadFileOptions, "uploadId" | "uploadGroupId">): Record<string, string> {
    const headers: Record<string, string> = { "X-Client-ID": getClientId() };
    const token = getAccessToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    addUploadAdmissionHeaders(headers, options);
    return headers;
}

// ── Direct path (small files, sent through the API) ───────────────────────────

async function _directUpload(file: File, options: UploadFileOptions): Promise<string> {
    const { onProgress, onBytesProgress, signal } = options;
    const form = new FormData();
    form.append("file", file, file.name);

    // Every attempt carries the same X-Upload-ID, so a retry after a lost response
    // returns the original upload instead of creating a second one.
    const result = await _withRetry(async () => {
        const xhr = await _xhr({
            method: "POST",
            url: `${API_BASE}/upload`,
            body: form,
            headers: _authHeaders(options),
            withCredentials: true,
            signal,
            onProgress: (loaded, total) => {
                onProgress?.(Math.round((loaded / total) * 80));
                onBytesProgress?.(Math.round((loaded / total) * file.size), file.size);
            },
        });
        if (xhr.status < 200 || xhr.status >= 300) {
            throw _apiErrorFromBody(xhr.status, xhr.responseText, `Upload failed (${xhr.status})`);
        }
        return JSON.parse(xhr.responseText) as DirectUploadResponse;
    }, signal);

    return result.file_key;
}

// ── Multipart path (large files, sent straight to object storage) ─────────────

async function _sendPart(url: string, blob: Blob, partNumber: number, onPartProgress: (bytes: number) => void, signal: AbortSignal): Promise<string> {
    return _withRetry(async () => {
        onPartProgress(0);
        const xhr = await _xhr({
            method: "PUT",
            url,
            body: blob,
            signal,
            onProgress: (loaded) => onPartProgress(loaded),
        });
        if (xhr.status < 200 || xhr.status >= 300) {
            throw new ApiError(xhr.status, `Part ${partNumber} failed: ${xhr.status}`);
        }
        const etag = xhr.getResponseHeader("ETag");
        if (!etag) throw new StorageUnreachableError(`Part ${partNumber} response has no readable ETag`);
        return etag;
    }, signal);
}

async function _beginMultipart(file: File, options: UploadFileOptions): Promise<MultipartSession> {
    const totalsMatch = (session: MultipartSession) =>
        session.parts.reduce((sum, part) => sum + part.size, 0) === file.size;

    if (options.multipartUploadId) {
        try {
            const resumed = await apiRequest(
                `/upload/presigned-multipart/${encodeURIComponent(options.multipartUploadId)}/resume`,
                { method: "POST", signal: options.signal, timeoutMs: UPLOAD_CONTROL_TIMEOUT_MS },
            ).then((response) => response.json() as Promise<MultipartSession>);
            if (totalsMatch(resumed)) return resumed;
        } catch (err) {
            // Expired, failed or cancelled sessions restart below; outages do not.
            if (!(err instanceof ApiError) || _isRetryable(err)) throw err;
        }
    }

    const session = await apiRequest("/upload/presigned-multipart/init", {
        method: "POST",
        headers: { "Content-Type": "application/json", ..._authHeaders(options) },
        body: JSON.stringify({
            filename: file.name,
            size: file.size,
            mime_type: file.type || "application/octet-stream",
        }),
        signal: options.signal,
        timeoutMs: UPLOAD_CONTROL_TIMEOUT_MS,
    }).then((response) => response.json() as Promise<MultipartSession>);
    if (!totalsMatch(session)) {
        throw new Error("Server multipart plan does not match the file size");
    }
    return session;
}

async function _multipartUpload(file: File, options: UploadFileOptions): Promise<string> {
    const { onProgress, onBytesProgress, signal } = options;
    const session = await _beginMultipart(file, options);
    options.onMultipartStarted?.(session.upload_id);

    const sentByPart = new Map<number, number>();
    const reportProgress = () => {
        const sent = [...sentByPart.values()].reduce((sum, bytes) => sum + bytes, 0);
        onProgress?.(Math.round((sent / file.size) * 80));
        onBytesProgress?.(sent, file.size);
    };

    const etags: Array<{ PartNumber: number; ETag: string }> = [];
    const pending: Array<{ number: number; url: string; start: number; size: number }> = [];
    let offset = 0;
    for (const part of [...session.parts].sort((a, b) => a.part_number - b.part_number)) {
        if (part.etag) {
            etags.push({ PartNumber: part.part_number, ETag: part.etag });
            sentByPart.set(part.part_number, part.size);
        } else if (part.url) {
            pending.push({ number: part.part_number, url: part.url, start: offset, size: part.size });
        } else {
            throw new Error(`Server multipart plan has no URL for part ${part.part_number}`);
        }
        offset += part.size;
    }
    reportProgress();

    // One failed part cancels its siblings; the session stays resumable.
    const parts = new AbortController();
    const abortParts = () => parts.abort();
    if (signal?.aborted) abortParts();
    signal?.addEventListener("abort", abortParts, { once: true });
    const runPartWorker = async () => {
        for (let next = pending.shift(); next; next = pending.shift()) {
            const { number, url, start, size } = next;
            const etag = await _sendPart(
                url,
                file.slice(start, start + size),
                number,
                (bytes) => {
                    sentByPart.set(number, bytes);
                    reportProgress();
                },
                parts.signal,
            );
            sentByPart.set(number, size);
            reportProgress();
            etags.push({ PartNumber: number, ETag: etag });
        }
    };
    try {
        await Promise.all(Array.from({ length: MULTIPART_CONCURRENCY }, runPartWorker));
    } catch (err) {
        parts.abort();
        if (signal?.aborted) throw new Error(UPLOAD_CANCELLED);
        // A blocked or misconfigured bucket fails before any part is stored.
        const storageUnreachable =
            err instanceof StorageUnreachableError ||
            (err instanceof TransferInterruptedError && etags.length === 0);
        throw storageUnreachable ? new StorageUnreachableError((err as Error).message, session.upload_id) : err;
    } finally {
        signal?.removeEventListener("abort", abortParts);
    }

    _onStatusUpdate(options.onStatusUpdate, "finalizingMultipart", options.t);
    etags.sort((a, b) => a.PartNumber - b.PartNumber);
    await _withRetry(
        () =>
            apiRequest("/upload/presigned-multipart/complete", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ upload_id: session.upload_id, parts: etags }),
                signal,
                timeoutMs: RESPONSE_TIMEOUT_MS,
            }),
        signal,
    );
    return session.quarantine_key;
}

// ── tus path (when browsers cannot reach object storage directly) ─────────────

function _apiErrorFromTus(err: Error): Error {
    const response = (err as tus.DetailedError).originalResponse;
    if (!response) return new TransferInterruptedError(err.message);
    return _apiErrorFromBody(response.getStatus(), response.getBody() ?? "", err.message);
}

function _tusUpload(file: File, options: UploadFileOptions): Promise<string> {
    const { onProgress, onBytesProgress, signal } = options;

    return new Promise<string>((resolve, reject) => {
        if (signal?.aborted) {
            reject(new Error(UPLOAD_CANCELLED));
            return;
        }

        let quarantineKey: string | undefined;
        let knownUrl = options.tusUrl;

        const upload = new tus.Upload(file, {
            endpoint: `${API_BASE}/upload/tus`,
            uploadUrl: options.tusUrl,
            chunkSize: TUS_CHUNK_SIZE,
            retryDelays: TUS_RETRY_DELAYS,
            // The queue stores the upload URL itself.
            storeFingerprintForResuming: false,
            headers: _authHeaders(options),
            metadata: {
                filename: file.name,
                filetype: file.type || "application/octet-stream",
            },
            onShouldRetry: (err) => {
                const status = err.originalResponse?.getStatus() ?? 0;
                const isClientError = status >= 400 && status < 500;
                // 429 means the server's chunk slots are busy, which passes quickly.
                const retryable = !isClientError || status === 409 || status === 423 || status === 429;
                return retryable && (typeof navigator === "undefined" || navigator.onLine);
            },
            onBeforeRequest: (req) => {
                const token = getAccessToken();
                if (token) req.setHeader("Authorization", `Bearer ${token}`);

                const originalSend = req.send.bind(req);
                req.send = async (body) => {
                    if (req.getMethod() === "PATCH" && body instanceof Blob) {
                        const digest = await crypto.subtle.digest("SHA-256", await body.arrayBuffer());
                        req.setHeader("Upload-Checksum", `sha256 ${btoa(String.fromCharCode(...new Uint8Array(digest)))}`);
                    }
                    return originalSend(body);
                };
            },
            onProgress: (bytesUploaded, bytesTotal) => {
                onProgress?.(Math.round((bytesUploaded / bytesTotal) * 80));
                onBytesProgress?.(bytesUploaded, bytesTotal);
            },
            onAfterResponse: (_req, res) => {
                // Percent-encoded server-side: the key embeds the filename,
                // which may contain non-latin-1 characters.
                const key = res.getHeader("X-Lectern-File-Key");
                if (key) quarantineKey = decodeURIComponent(key);

                if (upload.url && upload.url !== knownUrl) {
                    knownUrl = upload.url;
                    options.onTusUrlAvailable?.(upload.url);
                }
            },
            onSuccess: () => {
                if (quarantineKey) resolve(quarantineKey);
                else reject(new Error("Server did not return X-Lectern-File-Key after tus upload"));
            },
            onError: (err) => {
                reject(signal?.aborted ? new Error(UPLOAD_CANCELLED) : _apiErrorFromTus(err));
            },
        });

        // Abort without DELETE: a paused upload must stay resumable. Removing the
        // file cancels the session on the server separately.
        signal?.addEventListener("abort", () => {
            upload.abort(false).catch(() => { });
            reject(new Error(UPLOAD_CANCELLED));
        }, { once: true });

        upload.start();
    });
}

// ── Public API ────────────────────────────────────────────────────────────────

let storageUnreachableFromBrowser = false;

async function _transfer(file: File, options: UploadFileOptions): Promise<string> {
    if (options.tusUrl) {
        _onStatusUpdate(options.onStatusUpdate, "resumingUpload", options.t);
        return _tusUpload(file, options);
    }

    // The backend owns the routing policy; if its config cannot be loaded, every
    // file still takes a resumable path.
    const config = await getUploadConfig().catch(() => null);
    const directThresholdBytes = Math.max(0, config?.direct_threshold_mb ?? 0) * 1024 * 1024;

    _onStatusUpdate(options.onStatusUpdate, "uploading", options.t);
    if (config?.recommended_path === "direct" && file.size < directThresholdBytes) {
        return _directUpload(file, options);
    }
    if (file.size >= MULTIPART_MIN_BYTES && !storageUnreachableFromBrowser) {
        try {
            return await _multipartUpload(file, options);
        } catch (err) {
            if (err instanceof StorageUnreachableError) {
                // Later files skip the probe; this one continues through the API.
                storageUnreachableFromBrowser = true;
                if (err.multipartUploadId) {
                    apiRequest(`/upload/presigned-multipart/${encodeURIComponent(err.multipartUploadId)}`, {
                        method: "DELETE",
                    }).catch(() => { });
                }
            } else if (!(err instanceof ApiError) || err.status !== 501) {
                // 501: the deployment does not expose object storage to browsers.
                throw err;
            }
        }
    }
    return _tusUpload(file, options);
}

function _toUploadResult(
    result: NonNullable<UploadEventPayload["result"]>,
    fallbackName: string,
    wasCompressed: boolean,
): UploadResult {
    return {
        file_key: result.file_key,
        size: result.size,
        original_size: result.original_size,
        mime_type: result.mime_type,
        content_encoding: result.content_encoding ?? null,
        correctedName: result.file_name ?? result.file_key.split("/").pop() ?? fallbackName,
        wasCompressed,
    };
}

/**
 * Upload a file through the pipeline: transfer its bytes (0–80 %), then wait for
 * server-side scanning and sanitization (80–100 %).
 */
export async function uploadFile(
    file: File,
    options: UploadFileOptions = {},
): Promise<UploadResult> {
    const { onProgress, signal } = options;

    onProgress?.(0);
    await ensureFreshAccessToken();

    if (file.type.startsWith("image/")) _onStatusUpdate(options.onStatusUpdate, "compressing", options.t);
    const { file: fileToUpload, compressed } = await compressImageIfNeeded(file, options.skipCompression);

    // Recompression is not byte-stable, so its output cannot continue an earlier session.
    const quarantineKey = await _transfer(
        fileToUpload,
        compressed ? { ...options, tusUrl: undefined, multipartUploadId: undefined } : options,
    );

    onProgress?.(80);
    _onStatusUpdate(options.onStatusUpdate, "processing", options.t);
    options.onTransferred?.(quarantineKey);

    const result = await _withProcessingWatcher(signal, () =>
        _waitForUploadCompletion(quarantineKey, {
            onProgress: (p) => onProgress?.(80 + Math.round(p * 19)),
            onStatusUpdate: options.onStatusUpdate,
            signal,
            t: options.t,
        }),
    );

    onProgress?.(100);
    return _toUploadResult(result, file.name, compressed);
}

function _onStatusUpdate(
    cb: ((s: string, stageIndex?: number, stageTotal?: number) => void) | undefined,
    msg: string,
    t?: (key: string) => string,
): void {
    cb?.(t ? t(msg) : msg);
}

// ── Processing watchers ───────────────────────────────────────────────────────

let activeProcessingWatchers = 0;
const processingWatcherWaiters: Array<() => void> = [];

/** Bound concurrent SSE streams; transfers keep running while watchers queue. */
async function _withProcessingWatcher<T>(signal: AbortSignal | undefined, watch: () => Promise<T>): Promise<T> {
    if (activeProcessingWatchers < MAX_PROCESSING_WATCHERS) {
        activeProcessingWatchers++;
    } else {
        await new Promise<void>((resolve, reject) => {
            const grant = () => {
                signal?.removeEventListener("abort", onAbort);
                resolve();
            };
            const onAbort = () => {
                processingWatcherWaiters.splice(processingWatcherWaiters.indexOf(grant), 1);
                reject(new Error(UPLOAD_CANCELLED));
            };
            processingWatcherWaiters.push(grant);
            signal?.addEventListener("abort", onAbort, { once: true });
        });
    }
    try {
        return await watch();
    } finally {
        const next = processingWatcherWaiters.shift();
        if (next) next();
        else activeProcessingWatchers--;
    }
}

// ── SSE stream reader ─────────────────────────────────────────────────────────

/** Terminal upload failure — must not be retried by the SSE reconnection loop. */
class UploadTerminalError extends Error {
    public readonly status: number;
    public readonly errorCode?: string;
    constructor(status: number, message: string, errorCode?: string) {
        super(message);
        this.name = "UploadTerminalError";
        this.status = status;
        this.errorCode = errorCode;
    }
}

/** Maximum wall-clock time (ms) to wait for SSE processing before giving up. */
const SSE_TOTAL_TIMEOUT = 15 * 60 * 1000;
const SSE_READ_TIMEOUT_MS = 30_000;
const SSE_MAX_RECONNECTS = 10;

type WatchOptions = {
    onProgress?: (p: number) => void;
    onStatusUpdate?: (s: string, stageIndex?: number, stageTotal?: number) => void;
    signal?: AbortSignal;
    t?: (key: string) => string;
};

async function _settleFromStatus(fileKey: string, options: WatchOptions): Promise<NonNullable<UploadEventPayload["result"]>> {
    const polled = await pollUploadStatus(fileKey);
    let result: NonNullable<UploadEventPayload["result"]> | null = null;
    try {
        if (polled) result = _handleUploadEvent("upload", JSON.stringify(polled), undefined, undefined, options.t);
    } catch (err) {
        if (err instanceof UploadTerminalError) throw new ApiError(err.status, err.message, err.errorCode);
        throw err;
    }
    if (result) return result;
    throw new Error("Upload processing timed out. Check upload history for status.");
}

async function _waitForUploadCompletion(
    fileKey: string,
    options: WatchOptions = {},
): Promise<NonNullable<UploadEventPayload["result"]>> {
    const { onStatusUpdate, signal } = options;
    let lastEventId = 0;
    let attempts = 0;
    let highWaterMark = 0;
    const deadline = Date.now() + SSE_TOTAL_TIMEOUT;

    // Reconnects replay earlier events; progress must never move backwards.
    const safeOnProgress = options.onProgress
        ? (p: number) => {
              if (p > highWaterMark) {
                  highWaterMark = p;
                  options.onProgress!(p);
              }
          }
        : undefined;

    while (true) {
        if (Date.now() > deadline) return _settleFromStatus(fileKey, options);

        try {
            const response = await apiRequest(`/upload/events/${encodeURIComponent(fileKey)}`, {
                headers: {
                    Accept: "text/event-stream",
                    "Cache-Control": "no-cache",
                    ...(lastEventId > 0 ? { "Last-Event-ID": String(lastEventId) } : {}),
                },
                signal,
            });

            if (!response.body) {
                throw new Error("SSE response has no body");
            }
            const reader = response.body.getReader();
            const decoder = new TextDecoder();

            let buffer = "";
            let currentEventType = "message";
            let currentData = "";

            const readWithTimeout = async () => {
                let timer: ReturnType<typeof setTimeout> | undefined;
                try {
                    return await Promise.race([
                        reader.read(),
                        new Promise<ReadableStreamReadResult<Uint8Array>>((_, reject) => {
                            timer = setTimeout(() => reject(new Error("SSE Read Timeout")), SSE_READ_TIMEOUT_MS);
                        }),
                    ]);
                } finally {
                    clearTimeout(timer);
                }
            };

            try {
                while (Date.now() <= deadline) {
                    const { done, value } = await readWithTimeout();
                    if (done) break;

                    // Data arrived, so long processing does not count as a failure.
                    attempts = 0;
                    buffer += decoder.decode(value, { stream: true });

                    const lines = buffer.split(/\r?\n/);
                    buffer = lines.pop() ?? "";

                    for (const line of lines) {
                        if (line === "") {
                            if (currentData !== "") {
                                const result = _handleUploadEvent(
                                    currentEventType,
                                    currentData,
                                    safeOnProgress,
                                    onStatusUpdate,
                                    options.t,
                                );
                                if (result !== null) return result;
                            }
                            currentEventType = "message";
                            currentData = "";
                        } else if (line.startsWith("event:")) {
                            currentEventType = line.slice(6).trim();
                        } else if (line.startsWith("data:")) {
                            currentData = line.slice(5).trimStart();
                        } else if (line.startsWith("id:")) {
                            lastEventId = parseInt(line.slice(3).trim()) || lastEventId;
                        }
                    }
                }
            } finally {
                reader.cancel().catch(() => { });
            }
        } catch (err) {
            if (signal?.aborted) throw new Error(UPLOAD_CANCELLED);
            if (err instanceof UploadTerminalError) throw new ApiError(err.status, err.message, err.errorCode);
            if (err instanceof ApiError && err.status >= 400 && err.status < 500 && err.status !== 429) throw err;
        }

        // Both a closed stream and a failed connection reconnect with backoff.
        attempts++;
        if (attempts >= SSE_MAX_RECONNECTS) return _settleFromStatus(fileKey, options);
        await _sleep(Math.min(1000 * 2 ** (attempts - 1), 10_000), signal);
    }
}

// Public alias for workflows (such as avatar adoption) that need to await the
// same security-processing terminal state without duplicating SSE/poll logic.
export { _waitForUploadCompletion as waitForUploadCompletion };

function _handleUploadEvent(
    eventType: string,
    rawData: string,
    onProgress?: (p: number) => void,
    onStatusUpdate?: (s: string, stageIndex?: number, stageTotal?: number) => void,
    t?: (key: string) => string,
): NonNullable<UploadEventPayload["result"]> | null {
    if (eventType === "ping" || rawData === "") return null;

    let payload: UploadEventPayload;
    try {
        payload = JSON.parse(rawData) as UploadEventPayload;
    } catch {
        return null;
    }

    const statusText = payload.stage && t ? t(`stages.${payload.stage}`) : payload.detail;
    if (statusText) {
        onStatusUpdate?.(statusText, payload.stage_index ?? undefined, payload.stage_total ?? undefined);
    }

    if (payload.overall_percent != null) onProgress?.(payload.overall_percent);

    switch (payload.status) {
        case "clean":
        case "applied":
            if (!payload.result) throw new Error("Received clean status with no result payload");
            onProgress?.(1.0);
            return payload.result;

        case "malicious": {
            const malwareDetail =
                payload.detail ??
                (t
                    ? t("maliciousRejection")
                    : "File was rejected: potential security threat detected");
            throw new UploadTerminalError(
                400,
                withMalwareErrorPrefix(malwareDetail),
            );
        }

        case "failed":
            throw new UploadTerminalError(
                500,
                payload.detail ?? "File processing failed",
                payload.error_code ?? "ERR_PROCESSING_FAILED",
            );

        default:
            return null;
    }
}

/**
 * Follow an upload whose bytes are already stored, e.g. after a page reload.
 * Returns the same UploadResult shape as uploadFile().
 */
export async function trackExistingUpload(
    quarantineKey: string,
    options: Pick<UploadFileOptions, "onProgress" | "onStatusUpdate" | "signal" | "t"> = {},
): Promise<UploadResult> {
    const { onProgress, signal } = options;
    onProgress?.(80);

    const result = await _withProcessingWatcher(signal, () =>
        _waitForUploadCompletion(quarantineKey, {
            onProgress: (p: number) => onProgress?.(80 + Math.round(p * 19)),
            onStatusUpdate: options.onStatusUpdate,
            signal,
            t: options.t,
        }),
    );

    onProgress?.(100);
    return _toUploadResult(result, quarantineKey, false);
}

// ── Status polling fallback ───────────────────────────────────────────────────

/**
 * Poll /upload/status/{fileKey} once. Used as a reconnect fallback when SSE
 * is unavailable (e.g. corporate proxies that buffer SSE).
 */
async function pollUploadStatus(fileKey: string): Promise<UploadEventPayload | null> {
    try {
        const r = await apiRequest(`/upload/status/${encodeURIComponent(fileKey)}`);
        return (await r.json()) as UploadEventPayload;
    } catch {
        return null;
    }
}
