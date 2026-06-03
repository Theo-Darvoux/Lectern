import { useState, useMemo } from "react";
import { useStagingStore, unwrapOp } from "@/lib/staging-store";
import type {
  CreateMaterialOp,
  CreateDirectoryOp,
  MoveItemOp,
  Operation,
  StagedOperation,
} from "@/lib/staging-store";
import type { SelectedItem } from "@/lib/selection-store";

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

  const [ghostDirStack, setGhostDirStack] = useState<GhostDirEntry[]>([]);
  const activeGhostDir =
    ghostDirStack.length > 0 ? ghostDirStack[ghostDirStack.length - 1] : null;

  const allOps = useMemo(() => {
    const local = operations.map((s) => unwrapOp(s));
    const external = (previewOperations ?? [])
      .map((op, idx) => ({ op, idx }))
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
  }, [operations, previewOperations]);

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
    return [...effectiveDirs].sort((a, b) =>
      String(a.name ?? "").localeCompare(String(b.name ?? "")),
    );
  }, [effectiveDirs]);

  const sortedMats = useMemo(() => {
    return [...effectiveMats].sort((a, b) =>
      String(a.title ?? "").localeCompare(String(b.title ?? "")),
    );
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