import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  pendingCreatesForParent,
  pendingOperations,
  usePendingContributionsStore,
} from "./pending-contributions";
import { reconcileSubmittedOperations } from "./pr-client";
import { useAuthStore, useBrowseRefreshStore } from "./stores";

const apiFetch = vi.hoisted(() => vi.fn());

vi.mock("./api-client", () => ({
  ApiError: class ApiError extends Error {
    constructor(public status: number) {
      super(`API error ${status}`);
    }
  },
  apiFetch,
  apiFetchWithResponse: vi.fn(),
}));

describe("pending contributions", () => {
  beforeEach(() => {
    usePendingContributionsStore.setState({ ownerId: null, contributions: {} });
    useBrowseRefreshStore.setState({ refreshCount: 0 });
    useAuthStore.setState({ user: null, isAuthenticated: false });
    apiFetch.mockReset();
    apiFetch.mockResolvedValue({ status: "open" });
  });

  it("keeps submitted operations visible until the contribution closes", () => {
    const operation = {
      op: "create_material" as const,
      temp_id: "$mat-1",
      directory_id: "directory-1",
      title: "Budget",
      type: "document" as const,
    };

    usePendingContributionsStore.getState().track("pr-1", [operation]);
    expect(pendingOperations(usePendingContributionsStore.getState())).toEqual([
      { ...operation, temp_id: "$pending-pr-1-mat-1" },
    ]);

    usePendingContributionsStore.getState().resolve("pr-1");
    expect(pendingOperations(usePendingContributionsStore.getState())).toEqual([]);
  });

  it("tracks operations when submission is awaiting approval", () => {
    const operation = {
      op: "create_material" as const,
      temp_id: "$mat-2",
      directory_id: "directory-1",
      title: "Forecast",
      type: "document" as const,
    };

    reconcileSubmittedOperations({ id: "pr-2", status: "open" }, [operation]);

    expect(pendingOperations(usePendingContributionsStore.getState())).toEqual([
      { ...operation, temp_id: "$pending-pr-2-mat-2" },
    ]);
  });

  it("immediately refreshes browse data when submission is auto-approved", () => {
    const operation = {
      op: "create_material" as const,
      temp_id: "$mat-approved",
      directory_id: "directory-1",
      title: "Published budget",
      type: "document" as const,
    };

    reconcileSubmittedOperations({ id: "pr-approved", status: "approved" }, [operation]);

    expect(useBrowseRefreshStore.getState().refreshCount).toBe(1);
    expect(pendingOperations(usePendingContributionsStore.getState())).toEqual([]);
  });

  it("removes a pending projection if approval races ahead of the POST response", async () => {
    apiFetch.mockResolvedValueOnce({ status: "approved" });
    const operation = {
      op: "create_material" as const,
      temp_id: "$mat-race",
      directory_id: "directory-1",
      title: "Fast approval",
      type: "document" as const,
    };

    reconcileSubmittedOperations({ id: "pr-race", status: "open" }, [operation]);
    expect(Object.keys(usePendingContributionsStore.getState().contributions)).toEqual(["pr-race"]);

    await vi.waitFor(() => {
      expect(usePendingContributionsStore.getState().contributions).toEqual({});
    });
    expect(useBrowseRefreshStore.getState().refreshCount).toBe(1);
  });

  it("namespaces temporary IDs across simultaneous contributions", () => {
    const operation = {
      op: "create_material" as const,
      temp_id: "$mat-1",
      directory_id: null,
      title: "Budget",
      type: "document" as const,
    };
    const store = usePendingContributionsStore.getState();
    store.track("pr-a", [operation]);
    store.track("pr-b", [operation]);

    expect(pendingOperations(usePendingContributionsStore.getState()).map((op) =>
      op.op === "create_material" ? op.temp_id : undefined,
    )).toEqual(["$pending-pr-a-mat-1", "$pending-pr-b-mat-1"]);
  });

  it("clears pending data when the authenticated account changes", () => {
    const store = usePendingContributionsStore.getState();
    store.activateOwner("user-a");
    store.track("pr-a", [{
      op: "create_directory",
      temp_id: "$dir-1",
      name: "Private draft",
    }]);

    usePendingContributionsStore.getState().activateOwner("user-b");

    expect(usePendingContributionsStore.getState().ownerId).toBe("user-b");
    expect(usePendingContributionsStore.getState().contributions).toEqual({});
  });

  it("does not attribute a late submission response to a different account", () => {
    useAuthStore.setState({
      user: { id: "user-b" } as NonNullable<ReturnType<typeof useAuthStore.getState>["user"]>,
      isAuthenticated: true,
    });

    reconcileSubmittedOperations(
      { id: "pr-user-a", status: "open" },
      [{ op: "create_directory", temp_id: "$dir-1", name: "Account A" }],
      "user-a",
    );

    expect(usePendingContributionsStore.getState().contributions).toEqual({});
  });

  it("projects pending files and folders into their destination tree branch", () => {
    const operations = [
      {
        op: "create_directory" as const,
        temp_id: "$dir-1",
        parent_id: "directory-1",
        name: "Reports",
        type: "folder" as const,
      },
      {
        op: "create_material" as const,
        temp_id: "$mat-3",
        directory_id: "directory-1",
        title: "Budget",
        type: "document" as const,
      },
    ];

    expect(pendingCreatesForParent(operations, "directory-1")).toEqual({
      directories: [operations[0]],
      materials: [operations[1]],
    });
  });
});
