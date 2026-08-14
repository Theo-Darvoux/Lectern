import { describe, it, expect, beforeEach, afterEach } from "vitest";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { useOffline } from "./use-offline";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("useOffline", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    if (container) {
      container.remove();
    }
  });

  it("defaults to online (isOffline = false)", async () => {
    let offlineState: boolean | undefined;

    function TestComponent() {
      offlineState = useOffline();
      return null;
    }

    const root = createRoot(container);
    await act(async () => {
      root.render(React.createElement(TestComponent));
    });

    expect(offlineState).toBe(false);
  });

  it("updates to true on lectern-api-unreachable and back to false on lectern-api-reachable", async () => {
    let offlineState: boolean | undefined;

    function TestComponent() {
      offlineState = useOffline();
      return null;
    }

    const root = createRoot(container);
    await act(async () => {
      root.render(React.createElement(TestComponent));
    });

    expect(offlineState).toBe(false);

    await act(async () => {
      window.dispatchEvent(new CustomEvent("lectern-api-unreachable"));
    });

    expect(offlineState).toBe(true);

    await act(async () => {
      window.dispatchEvent(new CustomEvent("lectern-api-reachable"));
    });

    expect(offlineState).toBe(false);
  });

  it("updates to false when browser fires online event", async () => {
    let offlineState: boolean | undefined;

    function TestComponent() {
      offlineState = useOffline();
      return null;
    }

    const root = createRoot(container);
    await act(async () => {
      root.render(React.createElement(TestComponent));
    });

    await act(async () => {
      window.dispatchEvent(new CustomEvent("lectern-api-unreachable"));
    });
    expect(offlineState).toBe(true);

    await act(async () => {
      window.dispatchEvent(new Event("online"));
    });
    expect(offlineState).toBe(false);
  });
});
