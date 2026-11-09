import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { safeLocalStorage } from "./safe-storage";

// ---------------------------------------------------------------------------
// Upload expiry
// ---------------------------------------------------------------------------

/**
 * Staged files stay usable for this long. Matches the server: the upload
 * cleanup job's orphan cutoff and the expiry of a staged QCM's claim.
 */
const UPLOAD_EXPIRY_MS = 48 * 60 * 60 * 1000;

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
    content_sha256?: string | null;
    metadata?: Record<string, unknown>;
    parent_material_id?: string | null;
    attachments?: Record<string, unknown>[];
}

export interface EditMaterialOp {
    op: "edit_material";
    material_id: string;
    /** Display only: the item's current title, for the draft list. */
    target_title?: string | null;
    title?: string | null;
    type?: string | null;
    description?: string | null;
    tags?: string[] | null;
    file_key?: string | null;
    file_name?: string | null;
    file_size?: number | null;
    file_mime_type?: string | null;
    content_sha256?: string | null;
    diff_summary?: string | null;
    metadata?: Record<string, unknown> | null;
    version_lock?: number;
}

export interface DeleteMaterialOp {
    op: "delete_material";
    material_id: string;
    /** Display only: the item's title, for the draft list. */
    target_title?: string | null;
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
    /** Display only: the item's current name, for the draft list. */
    target_name?: string | null;
    name?: string | null;
    type?: string | null;
    description?: string | null;
    tags?: string[] | null;
    metadata?: Record<string, unknown> | null;
}

export interface DeleteDirectoryOp {
    op: "delete_directory";
    directory_id: string;
    /** Display only: the item's name, for the draft list. */
    target_name?: string | null;
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

/** One draft entry. `id` stays the same while the entry is edited. */
export interface StagedOperation {
    id: string;
    operation: Operation;
    /** Unix epoch ms when the operation's file was staged. */
    stagedAt: number;
}

// ---------------------------------------------------------------------------
// Operation helpers
// ---------------------------------------------------------------------------

/** Does this operation reference a file upload that could expire? */
export function hasFileKey(op: Operation): op is CreateMaterialOp | EditMaterialOp {
    return (op.op === "create_material" || op.op === "edit_material") && !!op.file_key;
}

/** Name of the item an operation acts on, for display. */
export function operationName(op: Operation): string {
    switch (op.op) {
        case "create_material":
            return op.title;
        case "create_directory":
            return op.name;
        case "edit_material":
            return op.title ?? op.target_title ?? "";
        case "edit_directory":
            return op.name ?? op.target_name ?? "";
        case "delete_material":
            return op.target_title ?? "";
        case "delete_directory":
            return op.target_name ?? "";
        case "move_item":
            return op.target_title ?? op.target_name ?? "";
    }
}

/** Temp ids of the folders the draft creates. */
export function stagedDirectoryIds(entries: StagedOperation[]): Set<string> {
    const ids = new Set<string>();
    for (const { operation } of entries) {
        if (operation.op === "create_directory" && operation.temp_id) ids.add(operation.temp_id);
    }
    return ids;
}

/** Milliseconds remaining before the upload expires, or null if no file. */
export function msUntilExpiry(staged: StagedOperation): number | null {
    if (!hasFileKey(staged.operation)) return null;
    return staged.stagedAt + UPLOAD_EXPIRY_MS - Date.now();
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

/** Human-readable time remaining, e.g. "5h 23m" or "12m". */
export function formatTimeRemaining(ms: number, t: (key: string) => string): string {
    if (ms <= 0) return t("expired");
    const totalMin = Math.floor(ms / 60_000);
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    if (h > 0) return `${h}${t("hours")} ${m}${t("minutes")}`;
    return `${m}${t("minutes")}`;
}

// ---------------------------------------------------------------------------
// Draft rules
// ---------------------------------------------------------------------------

function newEntryId(): string {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function newEntry(operation: Operation, stagedAt: number): StagedOperation {
    return { id: newEntryId(), operation, stagedAt };
}

function fileKeyOf(op: Operation): string | null | undefined {
    return op.op === "create_material" || op.op === "edit_material" ? op.file_key : undefined;
}

/** Replace an entry's operation. A new file restarts the expiry clock. */
function revise(entry: StagedOperation, operation: Operation, now: number): StagedOperation {
    const fileChanged = fileKeyOf(operation) !== fileKeyOf(entry.operation);
    return { ...entry, operation, stagedAt: fileChanged ? now : entry.stagedAt };
}

function isDelete(op: Operation): op is DeleteMaterialOp | DeleteDirectoryOp {
    return op.op === "delete_material" || op.op === "delete_directory";
}

/** The existing item an operation acts on, or null for a creation. */
function targetKey(op: Operation): string | null {
    switch (op.op) {
        case "edit_material":
        case "delete_material":
            return `material:${op.material_id}`;
        case "edit_directory":
        case "delete_directory":
            return `directory:${op.directory_id}`;
        case "move_item":
            return `${op.target_type}:${op.target_id}`;
        default:
            return null;
    }
}

function createdTempId(op: Operation): string | undefined {
    return op.op === "create_material" || op.op === "create_directory" ? op.temp_id : undefined;
}

/** Every id this operation needs to exist; temp ids are the `$…` ones. */
function referencedIds(op: Operation): (string | null | undefined)[] {
    switch (op.op) {
        case "create_material":
            return [op.directory_id, op.parent_material_id];
        case "create_directory":
            return [op.parent_id];
        case "edit_material":
        case "delete_material":
            return [op.material_id];
        case "edit_directory":
        case "delete_directory":
            return [op.directory_id];
        case "move_item":
            return [op.target_id, op.new_parent_id];
    }
}

/** Remove entries and everything in the draft that depends on what they create. */
function removeWithDependents(
    entries: StagedOperation[],
    ids: Iterable<string>,
): StagedOperation[] {
    const removed = new Set(ids);
    const goneTempIds = new Set<string>();
    let changed = true;
    while (changed) {
        changed = false;
        for (const { id, operation } of entries) {
            const tempId = createdTempId(operation);
            if (removed.has(id)) {
                if (tempId && !goneTempIds.has(tempId)) {
                    goneTempIds.add(tempId);
                    changed = true;
                }
            } else if (referencedIds(operation).some((ref) => ref && goneTempIds.has(ref))) {
                removed.add(id);
                changed = true;
            }
        }
    }
    return entries.filter((entry) => !removed.has(entry.id));
}

/** Drop undefined fields so they can't erase values when merged. */
function definedFields<T extends object>(value: T): T {
    return Object.fromEntries(
        Object.entries(value).filter(([, field]) => field !== undefined),
    ) as T;
}

/**
 * Add an operation to the draft. Each item has at most one edit and one move,
 * and a deletion replaces whatever else was pending on the item.
 */
function stage(
    entries: StagedOperation[],
    op: Operation,
    now: number = Date.now(),
): StagedOperation[] {
    const target = targetKey(op);
    if (target === null) return [...entries, newEntry(op, now)];

    const sameTarget = (entry: StagedOperation) => targetKey(entry.operation) === target;

    if (isDelete(op)) {
        const targetId = op.op === "delete_material" ? op.material_id : op.directory_id;
        if (targetId.startsWith("$")) {
            // The item only exists in this draft: forget its creation instead.
            const creations = entries.filter((e) => createdTempId(e.operation) === targetId);
            return removeWithDependents(entries, creations.map((e) => e.id));
        }
        return [...entries.filter((entry) => !sameTarget(entry)), newEntry(op, now)];
    }

    if (entries.some((entry) => sameTarget(entry) && isDelete(entry.operation))) return entries;

    const existing = entries.find((entry) => sameTarget(entry) && entry.operation.op === op.op);
    if (!existing) return [...entries, newEntry(op, now)];

    const merged = { ...existing.operation, ...definedFields(op) } as Operation;
    return entries.map((entry) => (entry === existing ? revise(entry, merged, now) : entry));
}

// ---------------------------------------------------------------------------
// Persistence
// ---------------------------------------------------------------------------

interface PersistedStaging {
    operations: StagedOperation[];
    ownerId: string | null;
    _tempCounter: number;
}

/** QCM content used to be copied into the draft; it now lives on the server. */
function withoutQcmDraft(op: Operation): Operation {
    if (op.op !== "create_material" && op.op !== "edit_material") return op;
    if (!op.metadata || !("qcm_draft" in op.metadata)) return op;
    const { qcm_draft: _dropped, ...metadata } = op.metadata;
    const { metadata: _old, ...rest } = op;
    return (Object.keys(metadata).length > 0 ? { ...rest, metadata } : rest) as Operation;
}

/** Upgrade a pre-v1 draft: ids, one edit per item, every creation addressable. */
function migrateV0(persisted: unknown): PersistedStaging {
    const state = (persisted ?? {}) as {
        operations?: (Operation | Partial<StagedOperation>)[];
        _tempCounter?: number;
    };
    let counter = state._tempCounter ?? 0;
    let operations: StagedOperation[] = [];
    for (const raw of state.operations ?? []) {
        if (!raw) continue;
        const wrapped = "operation" in raw && raw.operation ? raw : null;
        let operation = withoutQcmDraft(wrapped ? wrapped.operation! : (raw as Operation));
        if (operation.op === "create_material" && !operation.temp_id) {
            operation = { ...operation, temp_id: `$mat-${++counter}` };
        }
        operations = stage(operations, operation, wrapped?.stagedAt ?? Date.now());
    }
    return { operations, ownerId: null, _tempCounter: counter };
}

// ---------------------------------------------------------------------------
// Staging store
// ---------------------------------------------------------------------------

interface StagingState extends PersistedStaging {
    /** Whether the review drawer is open */
    reviewOpen: boolean;

    addOperation: (op: Operation) => void;
    addOperations: (ops: Operation[]) => void;
    /** Remove an entry and everything that depends on it. */
    removeOperation: (id: string) => void;
    removeOperations: (ids: string[]) => void;
    /** Drop everything pending on items that no longer need it, e.g. once deleted. */
    forgetItems: (items: { type: "directory" | "material"; id: string }[]) => void;
    /** Replace an entry's operation. Returns false if the entry no longer exists. */
    updateOperation: (id: string, op: Operation) => boolean;
    clearOperations: () => void;
    /** Remove all operations whose uploads have expired. Returns how many went. */
    purgeExpired: () => number;
    /** Bind the draft to the signed-in user; another account's draft is dropped. */
    activateOwner: (ownerId: string | null) => void;

    /** Never reset: a stale temp id must not be handed out again. */
    nextTempId: (prefix: string) => string;

    setReviewOpen: (open: boolean) => void;
}

export const useStagingStore = create<StagingState>()(
    persist(
        (set, get) => ({
            operations: [],
            ownerId: null,
            reviewOpen: false,
            _tempCounter: 0,

            addOperation: (op) => get().addOperations([op]),

            addOperations: (ops) =>
                set((s) => ({ operations: ops.reduce((all, op) => stage(all, op), s.operations) })),

            removeOperation: (id) => get().removeOperations([id]),

            removeOperations: (ids) =>
                set((s) => ({ operations: removeWithDependents(s.operations, ids) })),

            forgetItems: (items) => {
                const keys = new Set(items.map((item) => `${item.type}:${item.id}`));
                set((s) => ({
                    operations: s.operations.filter((entry) => {
                        const key = targetKey(entry.operation);
                        return key === null || !keys.has(key);
                    }),
                }));
            },

            updateOperation: (id, op) => {
                if (!get().operations.some((entry) => entry.id === id)) return false;
                const now = Date.now();
                set((s) => ({
                    operations: s.operations.map((entry) =>
                        entry.id === id ? revise(entry, op, now) : entry,
                    ),
                }));
                return true;
            },

            clearOperations: () => set({ operations: [] }),

            purgeExpired: () => {
                const before = get().operations;
                const after = removeWithDependents(
                    before,
                    before.filter(isExpired).map((entry) => entry.id),
                );
                if (after.length !== before.length) set({ operations: after });
                return before.length - after.length;
            },

            activateOwner: (ownerId) =>
                set((s) => {
                    // Signing out keeps the draft: an expired session must not lose work.
                    if (ownerId === null || s.ownerId === ownerId) return s;
                    return { ownerId, operations: s.ownerId === null ? s.operations : [] };
                }),

            nextTempId: (prefix) => {
                const count = get()._tempCounter + 1;
                set({ _tempCounter: count });
                return `$${prefix}-${count}`;
            },

            setReviewOpen: (open) => set({ reviewOpen: open }),
        }),
        {
            name: "lectern-staging",
            version: 1,
            storage: createJSONStorage(() => safeLocalStorage),
            partialize: (state): PersistedStaging => ({
                operations: state.operations,
                ownerId: state.ownerId,
                _tempCounter: state._tempCounter,
            }),
            migrate: (persisted, version) =>
                version >= 1 ? (persisted as PersistedStaging) : migrateV0(persisted),
        },
    ),
);

// ── Cross-tab sync ───────────────────────────────────────────────────────────

if (typeof window !== "undefined") {
    window.addEventListener("storage", (e) => {
        if (e.key === "lectern-staging") {
            useStagingStore.persist.rehydrate();
        }
    });
}
