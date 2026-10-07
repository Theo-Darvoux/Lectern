import { useState, useMemo } from "react";
import { compareNatural } from "@/lib/utils";
import { compareMaterialStatus } from "@/components/content-status-badge";
import { useStagingStore, unwrapOp } from "@/lib/staging-store";
import type {
  CreateMaterialOp,
  CreateDirectoryOp,
  EditDirectoryOp,
  EditMaterialOp,
  MoveItemOp,
  Operation,
  StagedOperation,
} from "@/lib/staging-store";
import type { SelectedItem } from "@/lib/selection-store";
import {
  pendingOperations,
  usePendingContributionsStore,
} from "@/lib/pending-contributions";
import { useAuthStore } from "@/lib/stores";

export function stagedStatus(
  ops: (StagedOperation | Operation)[],
  id: string,
  kind: "directory" | "material",
): "edited" | "deleted" | "moved" | null {
  for (const staged of ops) {
    const op = unwrapOp(staged as StagedOperation);
    if (kind === "directory") {
      if (op.op === "delete_directory" && op.directory_id === id)
        return "deleted";
      if (op.op === "edit_directory" && op.directory_id === id) return "edited";
    } else {
      if (op.op === "delete_material" && op.material_id === id)
        return "deleted";
      if (op.op === "edit_material" && op.material_id === id) return "edited";
    }
    if (op.op === "move_item" && op.target_type === kind && op.target_id === id)
      return "moved";
  }
  return null;
}

export type StagedState = "edited" | "deleted" | "moved";

export interface StagedInfo {
  /** Strongest staged state for the item: deleted > moved > edited */
  staged: StagedState;
  /** Metadata/content edits staged for the item, in staging order */
  edits: (EditMaterialOp | EditDirectoryOp)[];
  /** PR preview index of the first external material edit, if any */
  previewOpIndex?: number;
}

const STAGED_RANK: Record<StagedState, number> = { edited: 0, moved: 1, deleted: 2 };

/**
 * Index staged operations by target id once per render so each row is an O(1)
 * lookup. An item can carry several ops at once (e.g. moved *and* renamed), so
 * keep the strongest state for the badge and every edit for the display.
 */
export function indexStagedOps(ops: AugmentedOp[]) {
  const dirs = new Map<string, StagedInfo>();
  const mats = new Map<string, StagedInfo>();
  const note = (
    m: Map<string, StagedInfo>,
    id: string,
    staged: StagedState,
    op: AugmentedOp,
  ) => {
    const cur = m.get(id) ?? { staged, edits: [] };
    if (STAGED_RANK[staged] > STAGED_RANK[cur.staged]) cur.staged = staged;
    if (op.op === "edit_material" || op.op === "edit_directory") {
      cur.edits.push(op);
      if (op.op === "edit_material" && op.isExternal && cur.previewOpIndex === undefined) {
        cur.previewOpIndex = op._previewIdx;
      }
    }
    m.set(id, cur);
  };
  for (const o of ops) {
    if (o.op === "edit_directory") note(dirs, o.directory_id, "edited", o);
    else if (o.op === "delete_directory") note(dirs, o.directory_id, "deleted", o);
    else if (o.op === "edit_material") note(mats, o.material_id, "edited", o);
    else if (o.op === "delete_material") note(mats, o.material_id, "deleted", o);
    else if (o.op === "move_item") {
      note(o.target_type === "directory" ? dirs : mats, o.target_id, "moved", o);
    }
  }
  return { dirs, mats };
}

/** Overlay staged edits on an item's data so the listing shows pending values. */
export function applyStagedEdits(
  data: Record<string, unknown>,
  edits: (EditMaterialOp | EditDirectoryOp)[] | undefined,
): Record<string, unknown> {
  if (!edits?.length) return data;
  let out = data;
  for (const op of edits) {
    out = {
      ...out,
      ...(op.op === "edit_material" && op.title != null ? { title: op.title } : {}),
      ...(op.op === "edit_directory" && op.name != null ? { name: op.name } : {}),
      ...(op.type != null ? { type: op.type } : {}),
      ...(op.description != null ? { description: op.description } : {}),
      ...(op.tags != null ? { tags: op.tags } : {}),
    };
  }
  return out;
}

export interface GhostDirEntry {
  tempId: string;
  name: string;
}

export type AugmentedOp = Operation & {
  isExternal: boolean;
  _previewIdx: number | undefined;
  /** Index into the staging store's `operations` array (undefined for external PR ops). */
  _storeIndex: number | undefined;
};

export type NavItem =
  | { type: "dir"; dir: Record<string, unknown> }
  | { type: "ghost-dir"; tempId: string; name: string; op: AugmentedOp & (CreateDirectoryOp | MoveItemOp) }
  | { type: "mat"; mat: Record<string, unknown> }
  | { type: "ghost-mat"; op: AugmentedOp & (CreateMaterialOp | MoveItemOp) };

interface UseAugmentedListingProps {
  directory: Record<string, unknown> | null;
  directories: Record<string, unknown>[];
  materials: Record<string, unknown>[];
  previewOperations: Operation[];
}

export function useAugmentedListing({
  directory,
  directories,
  materials,
  previewOperations,
}: UseAugmentedListingProps) {
  const rawOperations = useStagingStore((s) => s.operations);
  const operations = useMemo(() => rawOperations ?? [], [rawOperations]);
  const pendingContributions = usePendingContributionsStore((s) => s.contributions);
  const pendingOwnerId = usePendingContributionsStore((s) => s.ownerId);
  const currentOwnerId = useAuthStore((s) => s.user?.id ? String(s.user.id) : null);
  const submittedOperations = useMemo(
    () => pendingOwnerId === currentOwnerId
      ? pendingOperations(usePendingContributionsStore.getState())
      : [],
    [currentOwnerId, pendingContributions, pendingOwnerId],
  );

  const [ghostDirStack, setGhostDirStack] = useState<GhostDirEntry[]>([]);
  const activeGhostDir =
    ghostDirStack.length > 0 ? ghostDirStack[ghostDirStack.length - 1] : null;

  const allOps = useMemo(() => {
    const local = operations.map((s) => unwrapOp(s));
    const external = [
      ...submittedOperations.map((op) => ({ op, idx: undefined as number | undefined })),
      ...(previewOperations ?? []).map((op, idx) => ({ op, idx })),
    ]
      .filter(({ op: externalOp }) => {
        if (
          externalOp.op === "edit_directory" ||
          externalOp.op === "delete_directory"
        ) {
          return !local.some(
            (l) =>
              (l.op === "edit_directory" || l.op === "delete_directory") &&
              l.directory_id === externalOp.directory_id,
          );
        }
        if (
          externalOp.op === "edit_material" ||
          externalOp.op === "delete_material"
        ) {
          return !local.some(
            (l) =>
              (l.op === "edit_material" || l.op === "delete_material") &&
              l.material_id === externalOp.material_id,
          );
        }
        return true;
      })
      .map(({ op, idx }) => ({ ...op, isExternal: true, _previewIdx: idx }));

    return [
      ...local.map((op, idx) => ({
        ...op,
        isExternal: false,
        _previewIdx: undefined as number | undefined,
        _storeIndex: idx,
      })),
      ...external.map((op) => ({ ...op, _storeIndex: undefined as number | undefined })),
    ];
  }, [operations, previewOperations, submittedOperations]);

  const realDirId = directory?.id ? String(directory.id) : null;
  const realDirName = directory?.name ? String(directory.name) : "Root";
  const dirId = activeGhostDir ? activeGhostDir.tempId : realDirId;
  const dirName = activeGhostDir ? activeGhostDir.name : realDirName;
  const isRoot = !dirId;

  const ghostDirs = allOps.filter((op) => {
    if (
      op.op === "create_directory" &&
      (isRoot ? !op.parent_id : op.parent_id === dirId)
    )
      return true;
    if (op.op === "move_item" && op.target_type === "directory") {
      const isTarget = isRoot ? !op.new_parent_id : op.new_parent_id === dirId;
      return isTarget;
    }
    return false;
  }) as (AugmentedOp & (CreateDirectoryOp | MoveItemOp))[];

  const ghostMaterials = allOps.filter((op) => {
    if (op.op === "create_material") {
      const isCreatedHere = isRoot ? !op.directory_id : op.directory_id === dirId;
      if (isCreatedHere) return true;
    }

    if (op.op === "move_item" && op.target_type === "material") {
      const isTarget = isRoot ? !op.new_parent_id : op.new_parent_id === dirId;
      return isTarget;
    }
    return false;
  }) as (AugmentedOp & (CreateMaterialOp | MoveItemOp))[];

  const effectiveDirs = useMemo(
    () => (activeGhostDir ? [] : directories),
    [activeGhostDir, directories],
  );
  const effectiveMats = useMemo(
    () => (activeGhostDir ? [] : materials),
    [activeGhostDir, materials],
  );

  const sortedDirs = useMemo(() => {
    return [...effectiveDirs].sort((a, b) => {
      const statusDiff = compareMaterialStatus(a.status, b.status);
      if (statusDiff !== 0) return statusDiff;
      return compareNatural(String(a.name ?? ""), String(b.name ?? ""));
    });
  }, [effectiveDirs]);

  const sortedMats = useMemo(() => {
    return [...effectiveMats].sort((a, b) => {
      const statusDiff = compareMaterialStatus(a.status, b.status);
      if (statusDiff !== 0) return statusDiff;
      return compareNatural(String(a.title ?? ""), String(b.title ?? ""));
    });
  }, [effectiveMats]);

  const isEmpty =
    effectiveDirs.length === 0 &&
    effectiveMats.length === 0 &&
    ghostDirs.length === 0 &&
    ghostMaterials.length === 0;

  const enterGhostDir = (tempId: string, name: string) => {
    setGhostDirStack((prev) => [...prev, { tempId, name }]);
  };

  const goBack = () => {
    setGhostDirStack((prev) => prev.slice(0, -1));
  };

  const flatItems = useMemo<NavItem[]>(
    () => [
      ...sortedDirs.map((dir) => ({ type: "dir" as const, dir })),
      ...ghostDirs.map((op) => ({
        type: "ghost-dir" as const,
        tempId:
          (op.op === "create_directory" ? op.temp_id : op.target_id) || "",
        name:
          (op.op === "create_directory" ? op.name : op.target_name) ||
          "Unnamed",
        op,
      })),
      ...sortedMats.map((mat) => ({ type: "mat" as const, mat })),
      ...ghostMaterials.map((op) => ({ type: "ghost-mat" as const, op })),
    ],
    [sortedDirs, ghostDirs, sortedMats, ghostMaterials],
  );

  const allSelectableItems = useMemo<SelectedItem[]>(() => [
    ...effectiveDirs.map((d) => ({
      id: String(d.id),
      type: "directory" as const,
      name: String(d.name ?? ""),
      parentId: dirId || null,
    })),
    ...ghostDirs.filter(op => !op.isExternal).map((op) => ({
      id: (op.op === "create_directory" ? op.temp_id : op.target_id) || "",
      type: "directory" as const,
      name: (op.op === "create_directory" ? op.name : op.target_name) || "Unnamed",
      parentId: dirId || null,
    })),
    ...effectiveMats.map((m) => ({
      id: String(m.id),
      type: "material" as const,
      name: String(m.title ?? ""),
      parentId: dirId || null,
      material_type: String(m.type ?? "other"),
    })),
    ...ghostMaterials.filter(op => !op.isExternal).map((op) => ({
      id: (op.op === "create_material" ? op.temp_id : op.target_id) || "",
      type: "material" as const,
      name: (op.op === "create_material" ? op.title : op.target_title) || "Unnamed",
      parentId: dirId || null,
      material_type: (op.op === "create_material" ? op.type : op.target_material_type) || "other",
    })),
  ], [effectiveDirs, ghostDirs, effectiveMats, ghostMaterials, dirId]);

  return {
    operations,
    allOps,
    realDirId,
    realDirName,
    dirId,
    dirName,
    activeGhostDir,
    ghostDirStack,
    setGhostDirStack,
    enterGhostDir,
    goBack,
    sortedDirs,
    sortedMats,
    ghostDirs,
    ghostMaterials,
    isEmpty,
    flatItems,
    allSelectableItems,
  };
}
