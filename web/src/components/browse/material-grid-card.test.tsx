// Regression test for the "eye" button bug: in grid view, clicking the preview
// (eye) button navigated via a hard browser navigation (the <button> is nested
// inside a Next <Link> / <a>) because the handler forgot e.preventDefault().
// The hard reload re-ran AuthGuard before the token rehydrated and bounced the
// user to /login. The fix adds e.preventDefault() so navigation stays client-side.
import { describe, it, expect, vi, beforeEach } from "vitest";
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";

const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, prefetch: vi.fn(), replace: vi.fn() }),
}));

vi.mock("next/link", () => ({
  __esModule: true,
  default: React.forwardRef(function MockLink(
    { href, children, ...rest }: { href: string; children: React.ReactNode },
    ref: React.Ref<HTMLAnchorElement>,
  ) {
    return React.createElement("a", { href, ref, ...rest }, children);
  }),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => {
    const t = (key: string) => key;
    (t as unknown as { has: () => boolean }).has = () => false;
    return t;
  },
}));

vi.mock("@/lib/browse-prefetch", () => ({ prefetchBrowsePath: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ apiFetch: vi.fn() }));
vi.mock("@/hooks/use-in-view", () => ({ useInView: () => false }));
vi.mock("@/lib/stores", () => ({
  useUIStore: (selector: (s: { openSidebar: () => void }) => unknown) =>
    selector({ openSidebar: vi.fn() }),
}));
vi.mock("@/components/home/material-preview", () => ({ MaterialPreview: () => null }));
vi.mock("./item-actions-menu", () => ({
  ItemActionsMenu: ({ children }: { children: React.ReactNode }) => children,
  ItemActionsDropdownTrigger: () => null,
}));

import { MaterialGridCard } from "./material-grid-card";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const material = {
  id: "m1",
  slug: "doc-1",
  title: "Doc 1",
  type: "document",
  current_version_info: { file_name: "a.pdf", file_mime_type: "application/pdf" },
};

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

describe("MaterialGridCard eye button", () => {
  beforeEach(() => {
    mockPush.mockClear();
  });

  it("prevents the default anchor navigation and pushes client-side", async () => {
    await render(
      <MaterialGridCard material={material} pathBase="/browse/math" navIndex={0} />,
    );

    const eyeButton = container.querySelector(
      '[aria-label="viewOrPreviewFor"]',
    ) as HTMLButtonElement | null;
    expect(eyeButton).not.toBeNull();

    // A cancelable click; dispatchEvent returns false if preventDefault ran.
    const clickEvent = new MouseEvent("click", { bubbles: true, cancelable: true });
    let dispatchReturned = true;
    await act(async () => {
      dispatchReturned = eyeButton!.dispatchEvent(clickEvent);
    });

    expect(clickEvent.defaultPrevented).toBe(true); // no hard anchor navigation
    expect(dispatchReturned).toBe(false);
    expect(mockPush).toHaveBeenCalledTimes(1);
    expect(mockPush).toHaveBeenCalledWith("/browse/math/doc-1");

    await act(async () => {
      root.unmount();
    });
    container.remove();
  });
});
