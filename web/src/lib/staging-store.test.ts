import { beforeEach, describe, expect, it, vi } from "vitest";
import { isExpired, useStagingStore, type Operation } from "./staging-store";

const store = () => useStagingStore.getState();
const ops = () => store().operations.map((entry) => entry.operation);

const createDir = (temp_id: string, parent_id: string | null = null): Operation => ({
  op: "create_directory",
  temp_id,
  parent_id,
  name: temp_id,
});
const createMat = (temp_id: string, directory_id: string | null = null): Operation => ({
  op: "create_material",
  temp_id,
  directory_id,
  title: temp_id,
  type: "document",
});

describe("staging store", () => {
  beforeEach(() => {
    useStagingStore.setState({ operations: [], ownerId: null, _tempCounter: 0 });
  });

  it("merges edits of an item so an earlier change survives a later one", () => {
    store().addOperation({ op: "edit_material", material_id: "m", title: "New" });
    store().addOperation({ op: "edit_material", material_id: "m", description: "D" });
    expect(ops()).toEqual([
      { op: "edit_material", material_id: "m", title: "New", description: "D" },
    ]);
  });

  it("keeps a metadata edit and a file replacement in one entry", () => {
    store().addOperation({ op: "edit_material", material_id: "m", title: "New" });
    store().addOperation({ op: "edit_material", material_id: "m", file_key: "cas/x" });
    expect(ops()).toEqual([
      { op: "edit_material", material_id: "m", title: "New", file_key: "cas/x" },
    ]);
  });

  it("replaces an earlier move of the same item", () => {
    const move = (new_parent_id: string): Operation => ({
      op: "move_item",
      target_type: "material",
      target_id: "m",
      new_parent_id,
    });
    store().addOperations([move("a"), move("b")]);
    expect(ops()).toEqual([move("b")]);
  });

  it("lets a deletion replace pending changes and deduplicates it", () => {
    const del: Operation = { op: "delete_material", material_id: "m" };
    store().addOperation({ op: "edit_material", material_id: "m", title: "New" });
    store().addOperation(del);
    store().addOperation(del);
    store().addOperation({ op: "edit_material", material_id: "m", title: "Later" });
    expect(ops()).toEqual([del]);
  });

  it("treats deleting a draft-only item as cancelling its creation", () => {
    store().addOperations([
      createMat("$mat-1"),
      { op: "create_material", temp_id: "$mat-2", directory_id: null, title: "a", type: "document", parent_material_id: "$mat-1" },
    ]);
    store().addOperation({ op: "delete_material", material_id: "$mat-1" });
    expect(ops()).toEqual([]);
  });

  it("removes everything that depends on a removed folder", () => {
    store().addOperations([
      createDir("$dir-1"),
      createDir("$dir-2", "$dir-1"),
      createMat("$mat-1", "$dir-2"),
      { op: "move_item", target_type: "material", target_id: "real", new_parent_id: "$dir-2" },
      { op: "move_item", target_type: "directory", target_id: "$dir-1", new_parent_id: null },
      createMat("$mat-2"),
    ]);
    store().removeOperation(store().operations[0].id);
    expect(ops()).toEqual([createMat("$mat-2")]);
  });

  it("purges expired files together with their dependents", () => {
    vi.useFakeTimers();
    try {
      store().addOperation({ ...createMat("$mat-1"), file_key: "cas/old" } as Operation);
      vi.advanceTimersByTime(49 * 60 * 60 * 1000);
      store().addOperation({
        ...createMat("$mat-2"),
        file_key: "cas/new",
        parent_material_id: "$mat-1",
      } as Operation);
      expect(store().operations.filter(isExpired)).toHaveLength(1);
      expect(store().purgeExpired()).toBe(2);
      expect(ops()).toEqual([]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("restarts the expiry clock when an entry's file changes", () => {
    vi.useFakeTimers();
    try {
      store().addOperation({ ...createMat("$mat-1"), file_key: "cas/old" } as Operation);
      const { id } = store().operations[0];
      vi.advanceTimersByTime(49 * 60 * 60 * 1000);
      expect(store().updateOperation(id, { ...createMat("$mat-1"), file_key: "cas/new" } as Operation)).toBe(true);
      expect(isExpired(store().operations[0])).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("reports an update of an entry that no longer exists", () => {
    expect(store().updateOperation("gone", createMat("$mat-1"))).toBe(false);
    expect(ops()).toEqual([]);
  });

  it("never hands out a temp id twice, even after clearing the draft", () => {
    const first = store().nextTempId("dir");
    store().clearOperations();
    expect(store().nextTempId("dir")).not.toBe(first);
  });

  it("drops another account's draft but keeps it across a sign-out", () => {
    store().activateOwner("a");
    store().addOperation(createMat("$mat-1"));
    store().activateOwner(null);
    store().activateOwner("a");
    expect(ops()).toHaveLength(1);
    store().activateOwner("b");
    expect(ops()).toEqual([]);
  });

  it("migrates a v0 draft", async () => {
    localStorage.setItem(
      "lectern-staging",
      JSON.stringify({
        version: 0,
        state: {
          _tempCounter: 3,
          operations: [
            { op: "delete_directory", directory_id: "bare" },
            {
              stagedAt: 5,
              operation: {
                op: "create_material",
                directory_id: null,
                title: "q",
                type: "qcm",
                file_key: "cas/q",
                metadata: { qcm_draft: { version: 1 } },
              },
            },
            { stagedAt: 6, operation: { op: "edit_material", material_id: "m", title: "T" } },
            { stagedAt: 7, operation: { op: "edit_material", material_id: "m", file_key: "cas/m", metadata: { qcm_draft: {} } } },
          ],
        },
      }),
    );
    await useStagingStore.persist.rehydrate();

    expect(store().operations.every((entry) => entry.id)).toBe(true);
    expect(ops()).toEqual([
      { op: "delete_directory", directory_id: "bare" },
      { op: "create_material", temp_id: "$mat-4", directory_id: null, title: "q", type: "qcm", file_key: "cas/q" },
      { op: "edit_material", material_id: "m", title: "T", file_key: "cas/m" },
    ]);
    expect(store().operations[1].stagedAt).toBe(5);
    expect(store()._tempCounter).toBe(4);
  });
});
