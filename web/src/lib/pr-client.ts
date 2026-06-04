import { apiFetch } from "@/lib/api-client";
import { type Operation } from "@/lib/staging-store";
import { useConfigStore } from "@/lib/stores";
import { toast } from "sonner";

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

/**
 * Post operations directly as a PR without putting them in the staging store.
 * Returns the PR ID if successful, null otherwise.
 */
export async function submitDirectOperations(
    ops: Operation[],
    manualTitle: string | undefined,
    manualDescription: string | null | undefined,
    t: any
): Promise<{ id: string; status: string } | null> {
    if (ops.length === 0) return null;

    const title = manualTitle || autoTitle(ops, t);

    const promise = apiFetch<{ id: string; status: string }>(
        "/pull-requests",
        {
            method: "POST",
            body: JSON.stringify({
                title: title.trim(),
                description: manualDescription ? truncateDescription(manualDescription) : null,
                operations: ops,
            }),
        }
    );

    toast.promise(promise, {
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
        return result;
    } catch {
        return null;
    }
}
