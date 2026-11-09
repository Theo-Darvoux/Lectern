import { ApiError, apiFetch, apiFetchWithResponse } from "@/lib/api-client";
import { operationName, type Operation } from "@/lib/staging-store";
import { useAuthStore, useBrowseRefreshStore, useConfigStore } from "@/lib/stores";
import { usePendingContributionsStore } from "@/lib/pending-contributions";
import { toast } from "sonner";

export async function fetchOpenPRCount(): Promise<number> {
    try {
        const res = await apiFetchWithResponse<unknown[]>(
            "/pull-requests?status=open&page=1&limit=1"
        );
        const total = res.response.headers.get("X-Total-Count");
        return total ? parseInt(total, 10) : 0;
    } catch {
        return 0;
    }
}

/**
 * Maximum length of a PR description / moderator note.
 *
 * The single source of truth lives in the backend (`MAX_PR_DESCRIPTION_LENGTH`
 * in api/app/schemas/pull_request.py) and is delivered to the client via the
 * public config endpoint. We read it from the config store here so the UI and
 * the server can never disagree — a mismatch would make the API reject the POST
 * with a 422 and the contribution would silently fail to be created (and thus
 * never auto-approved).
 */
export function getMaxDescriptionLength(): number | null {
    return useConfigStore.getState().config?.max_contribution_note_length ?? null;
}

/** Truncate a description so it never exceeds the server-enforced maximum. */
export function truncateDescription(text: string): string {
    const max = getMaxDescriptionLength();
    if (max === null || text.length <= max) return text;
    return text.slice(0, max);
}

/** Keep browse projections in sync with the authoritative submission state. */
export function reconcileSubmittedOperations(
    result: { id: string; status: string },
    operations: Operation[],
    submittingOwnerId?: string | null,
): void {
    if (result.status === "approved") {
        useBrowseRefreshStore.getState().triggerBrowseRefresh();
        return;
    }
    if (result.status === "open") {
        const currentUserId = useAuthStore.getState().user?.id;
        const currentOwnerId = currentUserId ? String(currentUserId) : null;
        if (submittingOwnerId !== undefined && submittingOwnerId !== currentOwnerId) {
            return;
        }
        const ownerId = submittingOwnerId ?? currentOwnerId;
        if (ownerId) {
            usePendingContributionsStore.getState().activateOwner(ownerId);
        }
        usePendingContributionsStore.getState().track(result.id, operations);
        // The close event can race ahead of the POST response for very fast
        // deferred auto-approvals. Re-read the authoritative status after
        // tracking so such an event can never leave a stale pending ghost.
        void reconcilePendingContribution(result.id);
    }
}

export async function reconcilePendingContribution(id: string): Promise<void> {
    try {
        const pullRequest = await apiFetch<{ status: string }>(`/pull-requests/${id}`);
        if (pullRequest.status === "open") return;
    } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 404) return;
    }

    if (id in usePendingContributionsStore.getState().contributions) {
        usePendingContributionsStore.getState().resolve(id);
        useBrowseRefreshStore.getState().triggerBrowseRefresh();
    }
}

/** Generate a sensible auto-title from one or multiple operations. */
export function autoTitle(ops: Operation[], t: any): string {
    if (ops.length === 0) return t("draft");
    if (ops.length > 1) {
        // e.g. "Deleted 3 items", "Moved 5 items"
        const types = new Set(ops.map((o) => o.op));
        if (types.size === 1) {
            const type = Array.from(types)[0];
            if (type.startsWith("delete_"))
                return t("deletedItems", { count: ops.length });
            if (type === "move_item")
                return t("movedItems", { count: ops.length });
        }
        return t("modifiedItems", { count: ops.length });
    }

    const op = ops[0];
    switch (op.op) {
        case "create_material":
            return op.title ? t("addMaterial", { name: op.title }) : t("addMaterialGeneric");
        case "edit_material":
            return op.title
                ? t("editMaterial", { name: op.title })
                : t("editMaterialGeneric");
        case "delete_material":
            return t("deleteMaterial");
        case "create_directory":
            return op.name ? t("createDirectory", { name: op.name }) : t("createDirectoryGeneric");
        case "edit_directory":
            return op.name ? t("editDirectory", { name: op.name }) : t("editDirectoryGeneric");
        case "delete_directory":
            return t("deleteDirectory");
        case "move_item":
            return t("moveItem", {
                type: op.target_type === "directory" ? t("folder") : t("material")
            });
        default:
            return t("draft");
    }
}

function isRealId(id: string | null | undefined): id is string {
    return !!id && !id.startsWith("$");
}

function destinationOf(op: Operation): string | null {
    switch (op.op) {
        case "move_item":
            return op.new_parent_id;
        case "create_material":
            return op.directory_id;
        case "create_directory":
            return op.parent_id ?? null;
        default:
            return null;
    }
}

/** List each operation's item with its folder path, one per line. */
export async function describeOperationPaths(
    ops: Operation[],
    fallbackItemName: string,
): Promise<string> {
    const materialIds = ops.flatMap((op) =>
        (op.op === "edit_material" || op.op === "delete_material") && isRealId(op.material_id)
            ? [op.material_id]
            : [],
    );
    const directoryIds = ops.map(destinationOf).filter(isRealId);

    let materials: Record<string, { directory_id: string | null; title: string }> = {};
    let directories: Record<string, { name: string }[]> = {};
    if (materialIds.length > 0 || directoryIds.length > 0) {
        try {
            const resolved = await apiFetch<{
                materials: typeof materials;
                directories: typeof directories;
            }>("/directories/resolve-paths", {
                method: "POST",
                body: JSON.stringify({
                    material_ids: [...new Set(materialIds)],
                    directory_ids: [...new Set(directoryIds)],
                }),
            });
            materials = resolved.materials ?? {};
            directories = resolved.directories ?? {};
        } catch {
            // Names alone still make a useful note.
        }
    }

    const lines = ops.map((op) => {
        const material =
            op.op === "edit_material" || op.op === "delete_material"
                ? materials[op.material_id]
                : undefined;
        const name = material?.title ?? (operationName(op) || fallbackItemName);
        const directoryId = material ? material.directory_id : destinationOf(op);
        const path = isRealId(directoryId) ? directories[directoryId] : undefined;
        return path?.length ? [...path.map((p) => p.name), name].join(" › ") : name;
    });
    return truncateDescription(lines.join("\n"));
}

/**
 * Post operations directly as a PR without putting them in the staging store.
 * Returns the PR ID if successful, null otherwise.
 */
export async function submitDirectOperations(
    ops: Operation[],
    manualTitle: string | undefined,
    manualDescription: string | null | undefined,
    t: any,
    isAnonymous = false
): Promise<{ id: string; status: string } | null> {
    if (ops.length === 0) return null;

    const title = manualTitle || autoTitle(ops, t);
    const submittingUserId = useAuthStore.getState().user?.id;
    const submittingOwnerId = submittingUserId ? String(submittingUserId) : null;

    const promise = apiFetch<{ id: string; status: string }>(
        "/pull-requests",
        {
            method: "POST",
            body: JSON.stringify({
                title: title.trim(),
                description: manualDescription ? truncateDescription(manualDescription) : null,
                operations: ops,
                is_anonymous: isAnonymous,
            }),
        }
    );

    toast.promise(promise, {
        id: "submit-contribution",
        loading: t("submitting"),
        success: (result) => {
            if (result.status === "approved") {
                return t("published");
            }
            return t("submitted");
        },
        error: (err) => (err instanceof Error ? err.message : t("failed")),
    });

    try {
        const result = await promise;
        reconcileSubmittedOperations(result, ops, submittingOwnerId);
        return result;
    } catch {
        return null;
    }
}
