import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";

let mockPathname = "/";

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
}));

vi.mock("next/link", () => ({
  __esModule: true,
  default: React.forwardRef(function MockLink(
    {
      href,
      children,
      onClick,
      ...rest
    }: {
      href: string;
      children: React.ReactNode;
      onClick?: (e: React.MouseEvent<HTMLAnchorElement>) => void;
    },
    ref: React.Ref<HTMLAnchorElement>,
  ) {
    return React.createElement(
      "a",
      {
        href,
        ref,
        onClick: (e: React.MouseEvent<HTMLAnchorElement>) => {
          onClick?.(e);
        },
        ...rest,
      },
      children,
    );
  }),
}));

import { BrowseLink, navigateBrowse } from "./browse-link";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

async function render(node: React.ReactElement) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(node);
  });
}

describe("BrowseLink and navigateBrowse", () => {
  let pushStateSpy: ReturnType<typeof vi.spyOn>;
  let replaceStateSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    mockPathname = "/";
    pushStateSpy = vi.spyOn(window.history, "pushState").mockImplementation(() => {});
    replaceStateSpy = vi.spyOn(window.history, "replaceState").mockImplementation(() => {});
    if (container) {
      container.remove();
    }
  });

  afterEach(() => {
    pushStateSpy.mockRestore();
    replaceStateSpy.mockRestore();
  });

  describe("BrowseLink", () => {
    it("does NOT intercept click when the current page is outside /browse (e.g. Home page '/')", async () => {
      mockPathname = "/";
      const onClickMock = vi.fn();

      await render(
        <BrowseLink href="/browse/tsp/semestre-5" onClick={onClickMock}>
          Link to Browse
        </BrowseLink>,
      );

      const link = container.querySelector("a") as HTMLAnchorElement;
      expect(link).not.toBeNull();

      const clickEvent = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 });
      await act(async () => {
        link.dispatchEvent(clickEvent);
      });

      expect(onClickMock).toHaveBeenCalledTimes(1);
      // Navigation should NOT be prevented on home page so Next.js Link can navigate
      expect(clickEvent.defaultPrevented).toBe(false);
      expect(pushStateSpy).not.toHaveBeenCalled();
    });

    it("intercepts click and calls history.pushState when already on /browse", async () => {
      mockPathname = "/browse/tsp";
      const onClickMock = vi.fn();

      await render(
        <BrowseLink href="/browse/tsp/semestre-5" onClick={onClickMock}>
          Link to Course
        </BrowseLink>,
      );

      const link = container.querySelector("a") as HTMLAnchorElement;
      expect(link).not.toBeNull();

      const clickEvent = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 });
      await act(async () => {
        link.dispatchEvent(clickEvent);
      });

      expect(onClickMock).toHaveBeenCalledTimes(1);
      expect(clickEvent.defaultPrevented).toBe(true);
      expect(pushStateSpy).toHaveBeenCalledWith(null, "", "/browse/tsp/semestre-5");
    });

    it("does not intercept when modifier keys are pressed (e.g. metaKey for open in new tab)", async () => {
      mockPathname = "/browse/tsp";

      await render(
        <BrowseLink href="/browse/tsp/semestre-5">
          Link to Course
        </BrowseLink>,
      );

      const link = container.querySelector("a") as HTMLAnchorElement;
      const clickEvent = new MouseEvent("click", {
        bubbles: true,
        cancelable: true,
        button: 0,
        metaKey: true,
      });

      await act(async () => {
        link.dispatchEvent(clickEvent);
      });

      expect(clickEvent.defaultPrevented).toBe(false);
      expect(pushStateSpy).not.toHaveBeenCalled();
    });
  });

  describe("navigateBrowse", () => {
    it("calls window.history.pushState when inside /browse", () => {
      Object.defineProperty(window, "location", {
        value: { pathname: "/browse/tsp" },
        writable: true,
      });

      navigateBrowse("/browse/tsp/course");
      expect(pushStateSpy).toHaveBeenCalledWith(null, "", "/browse/tsp/course");
    });

    it("calls window.history.replaceState when replace is true inside /browse", () => {
      Object.defineProperty(window, "location", {
        value: { pathname: "/browse/tsp" },
        writable: true,
      });

      navigateBrowse("/browse/tsp/course", { replace: true });
      expect(replaceStateSpy).toHaveBeenCalledWith(null, "", "/browse/tsp/course");
    });
  });
});
