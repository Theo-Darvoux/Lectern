/**
 * Regression tests for useBrowseSSE — verifies that the hook registers the
 * correct SSE event listeners so material creates/edits/deletes always trigger
 * a listing refresh without requiring a full page reload.
 *
 * We mock subscribeToSSE and capture every `listeners` object passed to it
 * across all calls, then assert that every required event type is present.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { useBrowseSSE } from "./use-browse-sse";
import { invalidateBrowseEntity } from "@/lib/browse-prefetch";
import { pendingOperations, usePendingContributionsStore } from "@/lib/pending-contributions";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const capturedListeners: Array<Record<string, unknown>> = [];

vi.mock("@/lib/sse-client", () => ({
    subscribeToSSE: vi.fn((opts: { listeners: Record<string, unknown> }) => {
        capturedListeners.push(opts.listeners);
        return { close: vi.fn() };
    }),
}));

vi.mock("next/navigation", () => ({
    useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

vi.mock("@/lib/api-client", () => ({ API_BASE: "http://api.test" }));
vi.mock("@/lib/browse-prefetch", () => ({
    invalidateBrowseEntity: vi.fn(),
    invalidateBrowsePath: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const noop = () => {};
/** Render a component that calls the hook, return a cleanup fn. */
function renderHookWith(
    data: Parameters<typeof useBrowseSSE>[0],
): () => void {
    const container = document.createElement("div");
    document.body.appendChild(container);

    function TestComponent() {
        useBrowseSSE(data, "/browse/test", noop);
        return null;
    }

    const root = createRoot(container);
    act(() => {
        root.render(React.createElement(TestComponent));
    });

    return () => {
        act(() => root.unmount());
        container.remove();
    };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useBrowseSSE — directory listing", () => {
    beforeEach(() => {
        capturedListeners.length = 0;
        usePendingContributionsStore.setState({ contributions: {} });
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    const dirData = {
        type: "directory_listing" as const,
        directory: { id: "dir-abc" },
        breadcrumbs: [{ id: "dir-abc", name: "Test", slug: "test" }],
    };

    it("registers child_added listener", () => {
        const cleanup = renderHookWith(dirData);
        act(() => { vi.runAllTimers(); });
        expect(capturedListeners.some((l) => "child_added" in l)).toBe(true);
        cleanup();
    });

    it("registers child_updated listener", () => {
        const cleanup = renderHookWith(dirData);
        act(() => { vi.runAllTimers(); });
        expect(capturedListeners.some((l) => "child_updated" in l)).toBe(true);
        cleanup();
    });

    it("registers child_removed listener", () => {
        const cleanup = renderHookWith(dirData);
        act(() => { vi.runAllTimers(); });
        expect(capturedListeners.some((l) => "child_removed" in l)).toBe(true);
        cleanup();
    });

    it("registers pr_closed listener", () => {
        const cleanup = renderHookWith(dirData);
        act(() => { vi.runAllTimers(); });
        expect(capturedListeners.some((l) => "pr_closed" in l)).toBe(true);
        cleanup();
    });

    it("child_updated triggers fetchData", () => {
        const fetchData = vi.fn();
        const container = document.createElement("div");
        document.body.appendChild(container);

        function TestComponent() {
            useBrowseSSE(dirData, "/browse/test", fetchData);
            return null;
        }

        const root = createRoot(container);
        act(() => { root.render(React.createElement(TestComponent)); });
        act(() => { vi.runAllTimers(); });

        // Find the listeners that have child_updated and call it.
        const listenersWithUpdate = capturedListeners.find((l) => "child_updated" in l);
        expect(listenersWithUpdate).toBeDefined();
        act(() => { (listenersWithUpdate!["child_updated"] as () => void)(); });

        expect(fetchData).toHaveBeenCalled();
        expect(invalidateBrowseEntity).toHaveBeenCalledWith(
            "directory:dir-abc",
            "/browse/test",
        );

        act(() => root.unmount());
        container.remove();
    });

    it("child_added immediately refreshes the current listing", () => {
        const fetchData = vi.fn();
        const container = document.createElement("div");
        document.body.appendChild(container);

        function TestComponent() {
            useBrowseSSE(dirData, "/browse/test", fetchData);
            return null;
        }

        const root = createRoot(container);
        act(() => { root.render(React.createElement(TestComponent)); });
        act(() => { vi.runAllTimers(); });

        const listeners = capturedListeners.find((candidate) => "child_added" in candidate);
        act(() => {
            (listeners?.child_added as (event: MessageEvent) => void)(
                new MessageEvent("child_added"),
            );
        });

        expect(fetchData).toHaveBeenCalledWith(true);
        expect(invalidateBrowseEntity).toHaveBeenCalledWith(
            "directory:dir-abc",
            "/browse/test",
        );

        act(() => root.unmount());
        container.remove();
    });

    it("removes optimistic pending files when their contribution closes", () => {
        usePendingContributionsStore.getState().track("pr-closed", [{
            op: "create_material",
            temp_id: "$mat-pending",
            directory_id: "dir-abc",
            title: "Pending",
            type: "document",
        }]);
        const cleanup = renderHookWith(dirData);
        act(() => { vi.runAllTimers(); });

        const listeners = capturedListeners.find((candidate) => "pr_closed" in candidate);
        act(() => {
            (listeners?.pr_closed as (event: MessageEvent) => void)(
                new MessageEvent("pr_closed", { data: JSON.stringify({ id: "pr-closed" }) }),
            );
        });

        expect(pendingOperations(usePendingContributionsStore.getState())).toEqual([]);
        cleanup();
    });
});

describe("useBrowseSSE — material view", () => {
    beforeEach(() => {
        capturedListeners.length = 0;
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    const matData = {
        type: "material" as const,
        material: { id: "mat-xyz" },
        breadcrumbs: [
            { id: "dir-abc", name: "Parent", slug: "parent" },
            { id: "mat-xyz", name: "Doc", slug: "doc" },
        ],
    };

    it("registers material_deleted listener", () => {
        const cleanup = renderHookWith(matData);
        act(() => { vi.runAllTimers(); });
        expect(capturedListeners.some((l) => "material_deleted" in l)).toBe(true);
        cleanup();
    });
});
