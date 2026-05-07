import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { safeLocalStorage } from "./safe-storage";

// ---------------------------------------------------------------------------
// Upload expiry — files in MinIO are cleaned up after 24h
// ---------------------------------------------------------------------------

/** Uploads are deleted after this many milliseconds (24 hours). */
const UPLOAD_EXPIRY_MS = 24 * 60 * 60 * 1000;

/** Warning threshold — show a warning when less than this time remains. */
const UPLOAD_WARNING_MS = 6 * 60 * 60 * 1000;

// ---------------------------------------------------------------------------
// Operation types (mirrors backend schemas)
// ---------------------------------------------------------------------------


export interface CreateMaterialOp {
    op: "create_material";
    temp_id?: string;
    directory_id: string | null; // real UUID or $temp-id, or null for root
    title: string;
    type: string;
    description?: string | null;
    tags?: string[];
    file_key?: string | null;
    file_name?: string | null;
    file_size?: number | null;
    file_mime_type?: string | null;
    metadata?: Record<string, unknown>;
    parent_material_id?: string | null;
    attachments?: Record<string, unknown>[];
}

export interface EditMaterialOp {
    op: "edit_material";
    material_id: string;
    title?: string | null;
    type?: string | null;
    description?: string | null;
    tags?: string[] | null;
    file_key?: string | null;
    file_name?: string | null;
    file_size?: number | null;
    file_mime_type?: string | null;
    diff_summary?: string | null;
    metadata?: Record<string, unknown> | null;
    version_lock?: number;
}

export interface DeleteMaterialOp {
    op: "delete_material";
    material_id: string;
}

export interface CreateDirectoryOp {
    op: "create_directory";
    temp_id?: string;
    parent_id?: string | null;
    name: string;
    type?: string;
    description?: string | null;
    tags?: string[];
    metadata?: Record<string, unknown>;
}

export interface EditDirectoryOp {
    op: "edit_directory";
    directory_id: string;
    name?: string | null;
    type?: string | null;
    description?: string | null;
    tags?: string[] | null;
    metadata?: Record<string, unknown> | null;
}

export interface DeleteDirectoryOp {
    op: "delete_directory";
    directory_id: string;
}

export interface MoveItemOp {
    op: "move_item";
    target_type: "directory" | "material";
    target_id: string;
    new_parent_id: string | null;
    target_name?: string | null;
    target_title?: string | null;
    target_material_type?: string | null;
}

export type Operation =
    | CreateMaterialOp
    | EditMaterialOp
    | DeleteMaterialOp
    | CreateDirectoryOp
    | EditDirectoryOp
    | DeleteDirectoryOp
    | MoveItemOp;

/** Wrapper that pairs an operation with its creation timestamp. */
export interface StagedOperation {
    operation: Operation;
    /** Unix epoch ms when the operation was staged */
    stagedAt: number;
}

// ---------------------------------------------------------------------------
// Expiry helpers
// ---------------------------------------------------------------------------

/**
 * Safely extract the inner Operation from a StagedOperation.
 * Handles legacy items that were persisted as bare Operations (no wrapper).
 */
export function unwrapOp(staged: StagedOperation): Operation {
    if (staged && "operation" in staged && staged.operation) return staged.operation;
    // Legacy: the item IS the operation itself
    return staged as unknown as Operation;
}

/** Does this operation reference a file upload that could expire? */
export function hasFileKey(op: Operation): op is CreateMaterialOp | EditMaterialOp {
    if (!op) return false;
    if (op.op === "create_material" || op.op === "edit_material") {
        return !!op.file_key;
    }
    return false;
}

/** Milliseconds remaining before the upload expires, or null if no file. */
export function msUntilExpiry(staged: StagedOperation): number | null {
    if (!hasFileKey(unwrapOp(staged))) return null;
    const stagedAt = staged?.stagedAt ?? Date.now();
    return stagedAt + UPLOAD_EXPIRY_MS - Date.now();
}

/** Is the uploaded file for this operation expired? */
export function isExpired(staged: StagedOperation): boolean {
    const ms = msUntilExpiry(staged);
    return ms !== null && ms <= 0;
}

/** Is the uploaded file expiring soon (within warning threshold)? */
export function isExpiringSoon(staged: StagedOperation): boolean {
    const ms = msUntilExpiry(staged);
    return ms !== null && ms > 0 && ms <= UPLOAD_WARNING_MS;
}

// ---------------------------------------------------------------------------
// Staging store
// ---------------------------------------------------------------------------

interface StagingState {
    /** All staged operations with timestamps */
    operations: StagedOperation[];
    /** Whether the review drawer is open */
    reviewOpen: boolean;
    /** Auto-incrementing counter for temp IDs */
    _tempCounter: number;

    // Operations
    addOperation: (op: Operation) => void;
    addOperations: (ops: Operation[]) => void;
    removeOperation: (index: number) => void;
    updateOperation: (index: number, op: Operation) => void;
    clearOperations: () => void;
    /** Remove all operations whose uploads have expired */
    purgeExpired: () => number;

    // Temp ID generation
    nextTempId: (prefix: string) => string;

    // Review drawer
    setReviewOpen: (open: boolean) => void;

    // Computed-like
    operationCount: () => number;
    hasOperations: () => boolean;
    expiredCount: () => number;
    expiringSoonCount: () => number;
}

export const useStagingStore = create<StagingState>()(
    persist(
        (set, get) => ({
            operations: [],
            reviewOpen: false,
            _tempCounter: 0,

            addOperation: (op) =>
                set((s) => ({
                    operations: [
                        ...s.operations,
                        { operation: op, stagedAt: Date.now() },
                    ],
                })),

            addOperations: (ops) =>
                set((s) => ({
                    operations: [
                        ...s.operations,
                        ...ops.map((op) => ({
                            operation: op,
                            stagedAt: Date.now(),
                        })),
                    ],
                })),

            removeOperation: (index) =>
                set((s) => {
                    const target = s.operations[index]
                        ? unwrapOp(s.operations[index])
                        : undefined;
                    if (!target) return s;

                    // Collect indices to remove (the target + cascading children)
                    const toRemove = new Set<number>([index]);

                    // If removing a create_directory with a temp_id, cascade-remove
                    // all operations that live inside that directory (recursively).
                    if (target.op === "create_directory" && target.temp_id) {
                        const tempIds = new Set<string>([target.temp_id]);
                        // Iteratively expand: find nested staged folders
                        let changed = true;
                        while (changed) {
                            changed = false;
                            for (let i = 0; i < s.operations.length; i++) {
                                if (toRemove.has(i)) continue;
                                const op = unwrapOp(s.operations[i]);
                                if (
                                    op.op === "create_directory" &&
                                    op.parent_id &&
                                    tempIds.has(op.parent_id)
                                ) {
                                    toRemove.add(i);
                                    if (op.temp_id) tempIds.add(op.temp_id);
                                    changed = true;
                                }
                            }
                        }
                        // Now remove materials / moves referencing any of those dirs
                        for (let i = 0; i < s.operations.length; i++) {
                            if (toRemove.has(i)) continue;
                            const op = unwrapOp(s.operations[i]);
                            if (
                                op.op === "create_material" &&
                                op.directory_id !== null && tempIds.has(op.directory_id)
                            ) {
                                toRemove.add(i);
                            } else if (
                                op.op === "move_item" &&
                                op.new_parent_id &&
                                tempIds.has(op.new_parent_id)
                            ) {
                                toRemove.add(i);
                            }
                        }
                    }

                    // If removing a create_material with a temp_id, cascade-remove
                    // any staged attachments whose parent_material_id references it.
                    if (target.op === "create_material" && target.temp_id) {
                        for (let i = 0; i < s.operations.length; i++) {
                            if (toRemove.has(i)) continue;
                            const op = unwrapOp(s.operations[i]);
                            if (
                                op.op === "create_material" &&
                                op.parent_material_id === target.temp_id
                            ) {
                                toRemove.add(i);
                            }
                        }
                    }

                    return {
                        operations: s.operations.filter(
                            (_, i) => !toRemove.has(i),
                        ),
                    };
                }),

            updateOperation: (index, op) =>
                set((s) => ({
                    operations: s.operations.map((item, i) =>
                        i === index
                            ? { ...item, operation: op }
                            : item,
                    ),
                })),

            clearOperations: () => set({ operations: [], _tempCounter: 0 }),

            purgeExpired: () => {
                const before = get().operations;
                const after = before.filter((s) => !isExpired(s));
                const removed = before.length - after.length;
                if (removed > 0) set({ operations: after });
                return removed;
            },

            nextTempId: (prefix) => {
                const count = get()._tempCounter + 1;
                set({ _tempCounter: count });
                return `$${prefix}-${count}`;
            },

            setReviewOpen: (open) => set({ reviewOpen: open }),

            operationCount: () => get().operations.length,
            hasOperations: () => get().operations.length > 0,
            expiredCount: () =>
                get().operations.filter((s) => isExpired(s)).length,
            expiringSoonCount: () =>
                get().operations.filter((s) => isExpiringSoon(s)).length,
        }),
        {
            name: "wikint-staging",
            storage: createJSONStorage(() => safeLocalStorage),
            // Persist operations and temp ID counter
            partialize: (state) => ({
                operations: state.operations,
                _tempCounter: state._tempCounter,
            }),
        },
    ),
);

// ── Cross-tab sync (O11) ─────────────────────────────────────────────────────

if (typeof window !== "undefined") {
    window.addEventListener("storage", (e) => {
        if (e.key === "wikint-staging") {
            useStagingStore.persist.rehydrate();
        }
    });
}

// ---------------------------------------------------------------------------
// Post-hydration: purge expired
// ---------------------------------------------------------------------------

if (typeof window !== "undefined") {
    // Run once after the store hydrates from localStorage
    const unsub = useStagingStore.persist.onFinishHydration(() => {
        useStagingStore.getState().purgeExpired();
        unsub();
    });
}

// ---------------------------------------------------------------------------
// Helper: human-readable label for an operation
// ---------------------------------------------------------------------------



/** Human-readable time remaining, e.g. "5h 23m" or "12m". */
export function formatTimeRemaining(ms: number, t: any): string {
    if (ms <= 0) return t("expired");
    const totalMin = Math.floor(ms / 60_000);
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    if (h > 0) return `${h}${t("hours")} ${m}${t("minutes")}`;
    return `${m}${t("minutes")}`;
}
