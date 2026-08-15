import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { useScrollHide } from "./use-scroll-hide";
import { useUIStore } from "@/lib/stores";

vi.mock("./use-media-query", () => ({
  useIsMobile: () => false,
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function TestScrollComponent({ onlyShowAtTop }: { onlyShowAtTop?: boolean }) {
  const scrollRef = React.useRef<HTMLDivElement>(null);
  useScrollHide(scrollRef, { onlyShowAtTop });
  return (
    <div
      ref={scrollRef}
      data-testid="scroll-container"
      style={{ height: 500, overflowY: "auto" }}
    >
      <div style={{ height: 2000 }}>Scrollable Content</div>
    </div>
  );
}

describe("useScrollHide hook", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    useUIStore.setState({ navbarVisible: true, materialActionsOpen: false });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    if (root) {
      act(() => root.unmount());
    }
    container?.remove();
  });

  it("hides navbar on downward scroll past threshold and allows reopening", async () => {
    await act(async () => {
      root.render(<TestScrollComponent />);
    });

    const scrollContainer = container.querySelector("[data-testid='scroll-container']") as HTMLElement;
    Object.defineProperty(scrollContainer, "scrollHeight", { value: 2000, configurable: true });
    Object.defineProperty(scrollContainer, "clientHeight", { value: 500, configurable: true });

    // Initial state
    expect(useUIStore.getState().navbarVisible).toBe(true);

    // Scroll down 200px (past 150px threshold)
    act(() => {
      Object.defineProperty(scrollContainer, "scrollTop", { value: 200, configurable: true });
      scrollContainer.dispatchEvent(new Event("scroll"));
    });

    expect(useUIStore.getState().navbarVisible).toBe(false);

    // Reopen navbar (e.g. via mouse to top)
    act(() => {
      useUIStore.getState().setNavbarVisible(true);
    });
    expect(useUIStore.getState().navbarVisible).toBe(true);
  });
});
