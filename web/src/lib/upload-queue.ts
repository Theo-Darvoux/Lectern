"use client";

/**
 * Navigation-persistent upload queue backed by Zustand.
 *
 * File objects and AbortControllers are NOT stored here (they can't be serialized).
 * This store tracks upload state (progress, status, result) so the UI can reconnect
 * after navigation without losing visible progress.
 *
 * The upload engine (async uploadFile calls, AbortController management) lives in
 * the UploadDrawer component via `useUploadQueueEngine`.
 */

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { safeLocalStorage } from "./safe-storage";

export interface QueueItem {
    /** Stable client-side UUID for this upload */
    clientId: string;
    /** UUID used for server-side upload idempotency */
    uploadId: string;

    // File identity (serializable)
    fileName: string;
    fileSize: number;
    fileMimeType: string;
    /** User-editable display title */
    title: string;

    // Upload state
    status: "pending" | "uploading" | "paused" | "done" | "error" | "virus";
    /** 0–100 */
    progress: number;
    /** Latest granular status message (e.g. "Scanning for malware…") */
    processingStatus: string;
    /** Current processing stage index (0-based), set during server-side processing */
    stageIndex?: number;
    /** Total number of processing stages */
    stageTotal?: number;

    // Optional tus URL for resumability across sessions
    tusUrl?: string;

    /** SHA-256 hash computed before upload. Used to verify re-attached files. */
    contentSha256?: string;

    // Result (set once status === "done")
    fileKey?: string;
    correctedName?: string;
    serverSize?: number;
    mimeType?: string;
    wasCompressed?: boolean;

    // Error (set once status === "error" | "virus")
    error?: string;

    /** Relative directory path from drop root, e.g. "FolderA/sub". "" = current dir. */
    targetDirPath: string;

    /**
     * When true, this item came from a batch-zip extraction.
     * The quarantine key is already reserved on the server; no File object is needed.
     * The upload engine will skip directly to SSE tracking.
     */
    isFromBatchZip?: boolean;
    /** Name of the source folder, set for batch-zip items and placeholder zip items. */
    folderName?: string;
}

interface UploadQueueState {
    items: QueueItem[];

    /** Number of currently active (uploading) transfers — used for concurrency control. */
    activeCount: number;

    // Mutations
    addItems: (items: QueueItem[]) => void;
    updateItem: (clientId: string, patch: Partial<QueueItem>) => void;
    removeItem: (clientId: string) => void;
    clearCompleted: () => void;
    clearAll: () => void;
    setActiveCount: (n: number) => void;
}

export const useUploadQueue = create<UploadQueueState>()(
    persist(
        (set) => ({
            items: [],
            activeCount: 0,

            addItems: (items) => set((s) => ({ items: [...s.items, ...items] })),

            updateItem: (clientId, patch) =>
                set((s) => ({
                    items: s.items.map((item) => (item.clientId === clientId ? { ...item, ...patch } : item)),
                })),

            removeItem: (clientId) => set((s) => ({ items: s.items.filter((i) => i.clientId !== clientId) })),

            clearCompleted: () => set((s) => ({ items: s.items.filter((i) => i.status !== "done") })),

            clearAll: () => set({ items: [], activeCount: 0 }),

            setActiveCount: (n) => set({ activeCount: n }),
        }),
        {
            name: "wikint_upload_queue",
            storage: createJSONStorage(() => safeLocalStorage),
            partialize: (s) => ({ items: s.items }),
            // Recover stale 'uploading' items after page reload (audit review fix):
            // items with a tusUrl can be resumed; others are marked as errored.
            onRehydrateStorage: () => (state) => {
                if (!state) return;
                state.items = state.items.map((item) => {
                    if (item.status === "uploading") {
                        return {
                            ...item,
                            status: item.tusUrl ? "paused" : "error",
                            error: item.tusUrl ? undefined : "Upload interrupted by page reload",
                        };
                    }
                    return item;
                });
            },
        },
    ),
);

// ── Cross-tab sync (O11) ─────────────────────────────────────────────────────

if (typeof window !== "undefined") {
    window.addEventListener("storage", (e) => {
        if (e.key === "wikint_upload_queue") {
            useUploadQueue.persist.rehydrate();
        }
    });
}

// ── Selectors (A11) ──────────────────────────────────────────────────────────

