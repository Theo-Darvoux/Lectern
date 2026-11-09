"use client";

/**
 * Navigation-persistent upload queue backed by Zustand.
 *
 * File objects and AbortControllers are NOT stored here (they can't be serialized);
 * they live in upload-runtime.ts. This store tracks durable upload state: identity,
 * destination, status, resume handles and result. Frame-batched progress lives in
 * upload-telemetry.ts so hot callbacks never reach storage.
 *
 * The queue is per tab, like the File objects it refers to.
 */

import { create } from "zustand";
import { persist, type PersistStorage, type StorageValue } from "zustand/middleware";
import { safeSessionStorage } from "./safe-storage";

export interface QueueItem {
    /** Stable client-side UUID for this upload */
    clientId: string;
    /** UUID used for server-side upload idempotency */
    uploadId: string;
    /** Bounded server-issued batch admission capability. */
    uploadGroupId?: string;

    // File identity (serializable)
    fileName: string;
    fileSize: number;
    fileLastModified?: number;
    fileMimeType: string;
    /** User-editable display title */
    title: string;

    // Destination, fixed when the file is added
    directoryId?: string | null;
    directoryName?: string;
    parentMaterialId?: string | null;
    /** Relative directory path from drop root, e.g. "FolderA/sub". "" = destination itself. */
    targetDirPath: string;

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
    /** Transfer speed and remaining time, set while bytes are moving. */
    bps?: number;
    etaSec?: number;

    // Resume handles
    tusUrl?: string;
    multipartUploadId?: string;
    /** Set once the bytes are stored; processing can then be followed without the File. */
    quarantineKey?: string;
    skipCompression?: boolean;

    // Result (set once status === "done")
    fileKey?: string;
    correctedName?: string;
    serverSize?: number;
    mimeType?: string;
    wasCompressed?: boolean;

    // Error (set once status === "error" | "virus")
    error?: string;
    /** True when a reload discarded the browser-only File reference. */
    referenceLost?: boolean;

    /** Set on the temporary row shown while a dropped folder is being read. */
    folderName?: string;
}

interface UploadQueueState {
    items: QueueItem[];
    /** Draft temp id of each folder to create, by destination-scoped path. */
    dirTempIds: Record<string, string>;
    /** Destination-scoped paths whose folder is already in the draft. */
    stagedDirKeys: string[];

    // Mutations
    addItems: (items: QueueItem[]) => void;
    updateItem: (clientId: string, patch: Partial<QueueItem>) => void;
    removeItem: (clientId: string) => void;
    clearAll: () => void;
    setDirTempIds: (ids: Record<string, string>) => void;
    markDirsStaged: (keys: string[]) => void;
    renameDirectory: (oldPath: string, newPath: string) => void;
}

type PersistedQueue = Pick<UploadQueueState, "items" | "dirTempIds" | "stagedDirKeys">;

/** Key of a folder path within the destination its files were dropped on. */
export function dirKey(item: Pick<QueueItem, "directoryId" | "parentMaterialId">, path: string): string {
    return `${item.directoryId ?? ""}|${item.parentMaterialId ?? ""}|${path}`;
}

export function titleFromFilename(name: string): string {
    return name
        .replace(/\.[^.]+$/, "")
        .replace(/(^|\s)(\p{L})/gu, (_, sep, ch) => sep + ch.toUpperCase())
        .trim();
}

function renamedPath(path: string, oldPath: string, newPath: string): string {
    if (path === oldPath) return newPath;
    return path.startsWith(`${oldPath}/`) ? newPath + path.slice(oldPath.length) : path;
}

function renamedKey(key: string, oldPath: string, newPath: string): string {
    const separator = key.indexOf("|", key.indexOf("|") + 1);
    return key.slice(0, separator + 1) + renamedPath(key.slice(separator + 1), oldPath, newPath);
}

function durableQueueItem(item: QueueItem): QueueItem {
    return {
        ...item,
        // Live display values are recomputed after a reload.
        progress: item.status === "done" ? 100 : 0,
        processingStatus: "",
        stageIndex: undefined,
        stageTotal: undefined,
        bps: undefined,
        etaSec: undefined,
    };
}

/** A reload discards every File, so rows that still needed one must get it back. */
function restoreAfterReload(items: QueueItem[]): QueueItem[] {
    return items.flatMap((stored) => {
        const item = durableQueueItem(stored);
        if (item.status === "done" || item.status === "virus") return [item];
        if (item.folderName) return item.status === "error" ? [item] : [];
        if (item.quarantineKey && item.status !== "error") return [{ ...item, status: "uploading" as const }];
        const resumable = Boolean(item.tusUrl || item.multipartUploadId);
        return [{
            ...item,
            status: resumable && item.status !== "error" ? ("paused" as const) : ("error" as const),
            referenceLost: true,
        }];
    });
}

const WRITE_DELAY_MS = 250;
let queuedWrite: { name: string; value: StorageValue<PersistedQueue> } | null = null;
let writeTimer: ReturnType<typeof setTimeout> | null = null;

function flushQueuedWrite(): void {
    if (writeTimer !== null) clearTimeout(writeTimer);
    writeTimer = null;
    if (queuedWrite) safeSessionStorage.setItem(queuedWrite.name, JSON.stringify(queuedWrite.value));
    queuedWrite = null;
}

/** Serializing the whole queue on each change would be quadratic for large folders. */
const debouncedStorage: PersistStorage<PersistedQueue> = {
    getItem: (name) => {
        const raw = safeSessionStorage.getItem(name);
        if (!raw) return null;
        try {
            return JSON.parse(raw) as StorageValue<PersistedQueue>;
        } catch {
            return null;
        }
    },
    setItem: (name, value) => {
        queuedWrite = { name, value };
        writeTimer ??= setTimeout(flushQueuedWrite, WRITE_DELAY_MS);
    },
    removeItem: (name) => {
        queuedWrite = null;
        safeSessionStorage.removeItem(name);
    },
};

if (typeof window !== "undefined") {
    window.addEventListener("pagehide", flushQueuedWrite);
}

export const useUploadQueue = create<UploadQueueState>()(
    persist(
        (set) => ({
            items: [],
            dirTempIds: {},
            stagedDirKeys: [],

            addItems: (items) => set((s) => ({ items: [...s.items, ...items] })),

            updateItem: (clientId, patch) =>
                set((s) => ({
                    items: s.items.map((item) => (item.clientId === clientId ? { ...item, ...patch } : item)),
                })),

            removeItem: (clientId) =>
                set((s) => {
                    const items = s.items.filter((i) => i.clientId !== clientId);
                    // Folder ids are only meaningful while a row can still use them.
                    return items.length > 0 ? { items } : { items, dirTempIds: {}, stagedDirKeys: [] };
                }),

            clearAll: () => set({ items: [], dirTempIds: {}, stagedDirKeys: [] }),

            setDirTempIds: (ids) => set((s) => ({ dirTempIds: { ...s.dirTempIds, ...ids } })),

            markDirsStaged: (keys) =>
                set((s) => ({ stagedDirKeys: [...new Set([...s.stagedDirKeys, ...keys])] })),

            renameDirectory: (oldPath, newPath) =>
                set((s) => ({
                    items: s.items.map((item) => {
                        const targetDirPath = renamedPath(item.targetDirPath, oldPath, newPath);
                        return targetDirPath === item.targetDirPath ? item : { ...item, targetDirPath };
                    }),
                    dirTempIds: Object.fromEntries(
                        Object.entries(s.dirTempIds).map(([key, id]) => [renamedKey(key, oldPath, newPath), id]),
                    ),
                    stagedDirKeys: s.stagedDirKeys.map((key) => renamedKey(key, oldPath, newPath)),
                })),
        }),
        {
            name: "lectern_upload_queue",
            storage: debouncedStorage,
            partialize: (s) => ({
                items: s.items.map(durableQueueItem),
                dirTempIds: s.dirTempIds,
                stagedDirKeys: s.stagedDirKeys,
            }),
            merge: (persisted, current) => {
                const stored = persisted as Partial<PersistedQueue> | undefined;
                return { ...current, ...stored, items: restoreAfterReload(stored?.items ?? []) };
            },
        },
    ),
);
