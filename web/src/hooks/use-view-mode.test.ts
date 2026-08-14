import { describe, it, expect, beforeEach, afterEach } from "vitest";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { useViewMode, type ViewMode } from "./use-view-mode";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("useViewMode", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    localStorage.clear();
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    if (container) {
      container.remove();
    }
  });

  it("defaults to grid view when localStorage is empty", async () => {
    let currentMode: ViewMode | undefined;

    function TestComponent() {
      const { mode } = useViewMode();
      currentMode = mode;
      return null;
    }

    const root = createRoot(container);
    await act(async () => {
      root.render(React.createElement(TestComponent));
    });

    expect(currentMode).toBe("grid");
  });

  it("restores list view from localStorage if previously stored", async () => {
    localStorage.setItem("browse-view-mode", "list");
    let currentMode: ViewMode | undefined;

    function TestComponent() {
      const { mode } = useViewMode();
      currentMode = mode;
      return null;
    }

    const root = createRoot(container);
    await act(async () => {
      root.render(React.createElement(TestComponent));
    });

    expect(currentMode).toBe("list");
  });

  it("updates mode and persists to localStorage on setMode", async () => {
    let hookResult: ReturnType<typeof useViewMode> | undefined;

    function TestComponent() {
      hookResult = useViewMode();
      return null;
    }

    const root = createRoot(container);
    await act(async () => {
      root.render(React.createElement(TestComponent));
    });

    expect(hookResult?.mode).toBe("grid");

    await act(async () => {
      hookResult?.setMode("list");
    });

    expect(hookResult?.mode).toBe("list");
    expect(localStorage.getItem("browse-view-mode")).toBe("list");

    await act(async () => {
      hookResult?.setMode("grid");
    });

    expect(hookResult?.mode).toBe("grid");
    expect(localStorage.getItem("browse-view-mode")).toBe("grid");
  });
});
