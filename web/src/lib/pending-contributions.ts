import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { Operation } from "@/lib/staging-store";
import { safeLocalStorage } from "@/lib/safe-storage";

interface PendingContribution {
  operations: Operation[];
  submittedAt: number;
}

interface PendingContributionsState {
  ownerId: string | null;
  contributions: Record<string, PendingContribution>;
  activateOwner: (ownerId: string | null) => void;
  track: (id: string, operations: Operation[]) => void;
  resolve: (id: string) => void;
}

export const usePendingContributionsStore = create<PendingContributionsState>()(
  persist(
    (set) => ({
      ownerId: null,
      contributions: {},
      activateOwner: (ownerId) =>
        set((state) => {
          if (state.ownerId === ownerId) return state;
          return { ownerId, contributions: {} };
        }),
      track: (id, operations) =>
        set((state) => ({
          contributions: {
            ...state.contributions,
            [id]: { operations, submittedAt: Date.now() },
          },
        })),
      resolve: (id) =>
        set((state) => {
          if (!(id in state.contributions)) return state;
          const contributions = { ...state.contributions };
          delete contributions[id];
          return { contributions };
        }),
    }),
    {
      name: "lectern-pending-contributions",
      storage: createJSONStorage(() => safeLocalStorage),
      partialize: (state) => ({
        ownerId: state.ownerId,
        contributions: state.contributions,
      }),
    },
  ),
);

function namespaceTempId(value: string | null | undefined, contributionId: string) {
  return value?.startsWith("$") ? `$pending-${contributionId}-${value.slice(1)}` : value;
}

function namespaceOperation(operation: Operation, contributionId: string): Operation {
  if (operation.op === "create_directory") {
    return {
      ...operation,
      temp_id: namespaceTempId(operation.temp_id, contributionId) ?? undefined,
      ...(operation.parent_id !== undefined
        ? { parent_id: namespaceTempId(operation.parent_id, contributionId) }
        : {}),
    };
  }
  if (operation.op === "create_material") {
    return {
      ...operation,
      temp_id: namespaceTempId(operation.temp_id, contributionId) ?? undefined,
      directory_id: namespaceTempId(operation.directory_id, contributionId) ?? null,
      ...(operation.parent_material_id !== undefined
        ? {
            parent_material_id: namespaceTempId(
              operation.parent_material_id,
              contributionId,
            ),
          }
        : {}),
    };
  }
  if (operation.op === "move_item") {
    return {
      ...operation,
      new_parent_id: namespaceTempId(operation.new_parent_id, contributionId) ?? null,
    };
  }
  return operation;
}

export function pendingOperations(state: PendingContributionsState): Operation[] {
  return Object.entries(state.contributions).flatMap(([id, contribution]) =>
    contribution.operations.map((operation) => namespaceOperation(operation, id)),
  );
}

export function pendingCreatesForParent(
  operations: Operation[],
  parentId: string | null,
): {
  directories: Extract<Operation, { op: "create_directory" }>[];
  materials: Extract<Operation, { op: "create_material" }>[];
} {
  const directories: Extract<Operation, { op: "create_directory" }>[] = [];
  const materials: Extract<Operation, { op: "create_material" }>[] = [];
  for (const operation of operations) {
    if (
      operation.op === "create_directory" &&
      (operation.parent_id ?? null) === parentId
    ) {
      directories.push(operation);
    } else if (
      operation.op === "create_material" &&
      !operation.parent_material_id &&
      (operation.directory_id ?? null) === parentId
    ) {
      materials.push(operation);
    }
  }
  return { directories, materials };
}

export function resolvePendingContributionEvent(event: MessageEvent): void {
  try {
    const payload = JSON.parse(event.data) as { id?: unknown };
    if (typeof payload.id === "string") {
      usePendingContributionsStore.getState().resolve(payload.id);
    }
  } catch {
    // A malformed best-effort event is reconciled by the next authoritative fetch.
  }
}
