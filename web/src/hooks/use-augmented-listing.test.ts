import { describe, expect, it } from "vitest";
import { applyStagedEdits, indexStagedOps, type AugmentedOp } from "./use-augmented-listing";
import type { Operation } from "@/lib/staging-store";

const local = (op: Operation, idx = 0): AugmentedOp =>
  ({ ...op, isExternal: false, _previewIdx: undefined, _storeIndex: idx }) as AugmentedOp;

describe("indexStagedOps", () => {
  it("keeps a moved item's edits so it stays editable and shows the new title", () => {
    const { mats } = indexStagedOps([
      local({ op: "move_item", target_type: "material", target_id: "m1", new_parent_id: "d2" }),
      local({ op: "edit_material", material_id: "m1", title: "Renamed" }, 1),
    ]);
    const info = mats.get("m1");
    expect(info?.staged).toBe("moved");
    expect(applyStagedEdits({ id: "m1", title: "Old" }, info?.edits)).toMatchObject({
      title: "Renamed",
    });
  });

  it("ranks deletion above move and edit regardless of order", () => {
    const { dirs } = indexStagedOps([
      local({ op: "edit_directory", directory_id: "d1", name: "X" }),
      local({ op: "delete_directory", directory_id: "d1" }, 1),
      local({ op: "move_item", target_type: "directory", target_id: "d1", new_parent_id: null }, 2),
    ]);
    expect(dirs.get("d1")?.staged).toBe("deleted");
  });

  it("returns the same object when there is nothing to overlay", () => {
    const data = { id: "m1", title: "T" };
    expect(applyStagedEdits(data, undefined)).toBe(data);
  });
});
