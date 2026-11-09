"use client";

/**
 * Upload execution shared by every UploadDrawer: browser-only File references,
 * the transfer queue and its concurrency limit. Module scope makes uploads
 * independent of which component started them.
 */

import { apiRequest } from "@/lib/api-client";
import { logicalFileSize, trackExistingUpload, uploadFile } from "@/lib/upload-client";
import { isMalwareUploadErrorMessage, localizeUploadError } from "@/lib/upload-errors";
import { titleFromFilename, useUploadQueue, type QueueItem } from "@/lib/upload-queue";
import { clearAllUploadTelemetry, clearUploadTelemetry, updateUploadTelemetry } from "@/lib/upload-telemetry";

type Translate = (key: string) => string;

const MAX_CONCURRENT_TRANSFERS = 4;
const ETA_INTERVAL_MS = 500;
const ETA_MIN_SAMPLES = 3;

interface SpeedEntry {
    lastBytes: number;
    lastTime: number;
    lastEmitTime: number;
    smoothedBps: number;
    samples: number;
}

const files = new Map<string, File>();
const previews = new Map<string, string>();
const controllers = new Map<string, AbortController>();
const speeds = new Map<string, SpeedEntry>();
let waiting: string[] = [];
let activeTransfers = 0;
let processingResumed = false;

const queue = () => useUploadQueue.getState();
const findItem = (clientId: string) => queue().items.find((item) => item.clientId === clientId);

export const hasUploadFile = (clientId: string) => files.has(clientId);
export const getUploadPreview = (clientId: string) => previews.get(clientId);

export function attachUploadFile(clientId: string, file: File): void {
    files.set(clientId, file);
    if (file.type.startsWith("image/") && !previews.has(clientId)) {
        previews.set(clientId, URL.createObjectURL(file));
    }
}

function releaseFile(clientId: string): void {
    const preview = previews.get(clientId);
    if (preview) URL.revokeObjectURL(preview);
    previews.delete(clientId);
    files.delete(clientId);
}

function reportSpeed(clientId: string, uploaded: number, total: number): void {
    const now = Date.now();
    const previous = speeds.get(clientId) ?? {
        lastBytes: 0,
        lastTime: now,
        lastEmitTime: 0,
        smoothedBps: 0,
        samples: 0,
    };
    const elapsed = (now - previous.lastTime) / 1000;
    const instant = elapsed > 0 ? Math.max(0, uploaded - previous.lastBytes) / elapsed : 0;
    const smoothedBps = previous.smoothedBps === 0 ? instant : 0.7 * previous.smoothedBps + 0.3 * instant;
    const samples = previous.samples + 1;
    const shouldEmit = samples >= ETA_MIN_SAMPLES && now - previous.lastEmitTime >= ETA_INTERVAL_MS;

    speeds.set(clientId, {
        lastBytes: uploaded,
        lastTime: now,
        lastEmitTime: shouldEmit ? now : previous.lastEmitTime,
        smoothedBps,
        samples,
    });
    if (shouldEmit) {
        updateUploadTelemetry(clientId, {
            bps: smoothedBps,
            etaSec: smoothedBps > 0 ? Math.round((total - uploaded) / smoothedBps) : undefined,
        });
    }
}

function completeItem(clientId: string, result: Awaited<ReturnType<typeof uploadFile>>, wasCompressed: boolean): void {
    const current = findItem(clientId);
    clearUploadTelemetry(clientId);
    queue().updateItem(clientId, {
        status: "done",
        progress: 100,
        fileKey: result.file_key,
        correctedName: result.correctedName,
        serverSize: logicalFileSize(result),
        mimeType: result.mime_type,
        wasCompressed,
        quarantineKey: undefined,
        // A title the user never edited follows the server-corrected name.
        title: current?.title === titleFromFilename(current?.fileName ?? "")
            ? titleFromFilename(result.correctedName)
            : current?.title,
    });
}

function failItem(clientId: string, err: unknown, t: Translate): void {
    const message = localizeUploadError(err, t);
    clearUploadTelemetry(clientId);
    queue().updateItem(clientId, {
        status: isMalwareUploadErrorMessage(message) ? "virus" : "error",
        error: message,
    });
}

function drain(t: Translate): void {
    while (activeTransfers < MAX_CONCURRENT_TRANSFERS && waiting.length > 0) {
        const clientId = waiting.shift()!;
        const item = findItem(clientId);
        const file = files.get(clientId);
        if (!item || !file || item.status !== "pending") continue;
        activeTransfers++;
        void runTransfer(item, file, t);
    }
}

async function runTransfer(item: QueueItem, file: File, t: Translate): Promise<void> {
    const { clientId } = item;
    const controller = new AbortController();
    controllers.set(clientId, controller);

    // The slot covers byte transfer only, so server processing never delays the next file.
    let slotHeld = true;
    const releaseSlot = () => {
        if (!slotHeld) return;
        slotHeld = false;
        activeTransfers--;
        drain(t);
    };

    queue().updateItem(clientId, { status: "uploading", error: undefined, referenceLost: false });
    try {
        const result = await uploadFile(file, {
            onProgress: (progress) => updateUploadTelemetry(clientId, { progress }),
            onStatusUpdate: (processingStatus, stageIndex, stageTotal) =>
                updateUploadTelemetry(clientId, { processingStatus, stageIndex, stageTotal }),
            onBytesProgress: (uploaded, total) => reportSpeed(clientId, uploaded, total),
            onTusUrlAvailable: (tusUrl) => queue().updateItem(clientId, { tusUrl }),
            onMultipartStarted: (multipartUploadId) => queue().updateItem(clientId, { multipartUploadId }),
            onTransferred: (quarantineKey) => {
                queue().updateItem(clientId, { quarantineKey });
                updateUploadTelemetry(clientId, { bps: undefined, etaSec: undefined });
                releaseSlot();
            },
            signal: controller.signal,
            uploadId: item.uploadId,
            uploadGroupId: item.uploadGroupId,
            tusUrl: item.tusUrl,
            multipartUploadId: item.multipartUploadId,
            skipCompression: item.skipCompression,
            t,
        });
        completeItem(clientId, result, result.wasCompressed);
    } catch (err) {
        if (!controller.signal.aborted) failItem(clientId, err, t);
    } finally {
        controllers.delete(clientId);
        speeds.delete(clientId);
        releaseSlot();
    }
}

async function followProcessing(item: QueueItem, t: Translate): Promise<void> {
    const { clientId, quarantineKey } = item;
    if (!quarantineKey) return;
    const controller = new AbortController();
    controllers.set(clientId, controller);
    try {
        const result = await trackExistingUpload(quarantineKey, {
            onProgress: (progress) => updateUploadTelemetry(clientId, { progress }),
            onStatusUpdate: (processingStatus, stageIndex, stageTotal) =>
                updateUploadTelemetry(clientId, { processingStatus, stageIndex, stageTotal }),
            signal: controller.signal,
            t,
        });
        completeItem(clientId, result, item.wasCompressed ?? false);
    } catch (err) {
        if (!controller.signal.aborted) failItem(clientId, err, t);
    } finally {
        controllers.delete(clientId);
    }
}

/** Reconnect rows whose bytes were stored before a page reload. Runs once per page. */
export function resumeProcessingAfterReload(t: Translate): void {
    if (processingResumed) return;
    processingResumed = true;
    for (const item of queue().items) {
        if (item.status === "uploading" && item.quarantineKey) void followProcessing(item, t);
    }
}

export function scheduleUpload(clientId: string, t: Translate): void {
    if (controllers.has(clientId) || waiting.includes(clientId)) return;
    queue().updateItem(clientId, { status: "pending", error: undefined, referenceLost: false });
    waiting.push(clientId);
    drain(t);
}

/**
 * Run a stopped row again. A failed attempt is settled on the server, so it gets a
 * new upload ID; its old batch admission does not cover that ID.
 */
export function restartUpload(clientId: string, t: Translate, uploadGroupId?: string): void {
    const item = findItem(clientId);
    if (!item) return;
    clearUploadTelemetry(clientId);
    queue().updateItem(clientId, {
        uploadGroupId,
        ...(item.status === "error" ? { uploadId: crypto.randomUUID(), quarantineKey: undefined } : {}),
        progress: 0,
        processingStatus: "",
    });
    scheduleUpload(clientId, t);
}

/** Stop the transfer but keep its server session, so it can continue later. */
export function pauseUpload(clientId: string): void {
    queue().updateItem(clientId, { status: "paused" });
    updateUploadTelemetry(clientId, { bps: undefined, etaSec: undefined });
    controllers.get(clientId)?.abort();
}

function cancelOnServer(item: QueueItem): void {
    const cancel = item.tusUrl
        ? apiRequest(item.tusUrl, { method: "DELETE", headers: { "Tus-Resumable": "1.0.0" } })
        : item.multipartUploadId
          ? apiRequest(`/upload/presigned-multipart/${encodeURIComponent(item.multipartUploadId)}`, { method: "DELETE" })
          : apiRequest(`/upload/${encodeURIComponent(item.uploadId)}`, { method: "DELETE" });
    // Cleanup jobs reclaim anything this best-effort request does not reach.
    cancel.catch(() => { });
}

/** Remove a row the user no longer wants, releasing its server resources. */
export function removeUpload(clientId: string): void {
    const item = findItem(clientId);
    controllers.get(clientId)?.abort();
    waiting = waiting.filter((id) => id !== clientId);
    releaseFile(clientId);
    clearUploadTelemetry(clientId);
    queue().removeItem(clientId);
    const reachedServer = item && (item.status !== "pending" || item.tusUrl || item.multipartUploadId);
    if (item && !item.folderName && reachedServer) cancelOnServer(item);
}

/** Drop a row whose upload now belongs to the draft. */
export function forgetUpload(clientId: string): void {
    releaseFile(clientId);
    clearUploadTelemetry(clientId);
    queue().removeItem(clientId);
}

if (typeof window !== "undefined") {
    window.addEventListener("beforeunload", (event) => {
        if (activeTransfers > 0 || waiting.length > 0) {
            event.preventDefault();
            event.returnValue = "";
        }
    });

    // An emptied queue (logout, or the last row leaving) has nothing left to run.
    useUploadQueue.subscribe((state, previous) => {
        if (previous.items.length === 0 || state.items.length > 0) return;
        controllers.forEach((controller) => controller.abort());
        waiting = [];
        [...previews.keys()].forEach(releaseFile);
        files.clear();
        clearAllUploadTelemetry();
    });
}
