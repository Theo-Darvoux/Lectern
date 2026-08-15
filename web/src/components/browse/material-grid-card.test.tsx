import { describe, it, expect, vi, beforeEach } from "vitest";
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";

const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, prefetch: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/browse/math",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("next/link", () => ({
  __esModule: true,
  default: React.forwardRef(function MockLink(
    { href, children, onClick, ...rest }: { href: string; children: React.ReactNode; onClick?: (e: React.MouseEvent) => void },
    ref: React.Ref<HTMLAnchorElement>,
  ) {
    return React.createElement("a", { href, ref, onClick, ...rest }, children);
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

describe("MaterialGridCard", () => {
  beforeEach(() => {
    mockPush.mockClear();
    if (container) {
      container.remove();
    }
  });

  it("renders the material title", async () => {
    await render(
      <MaterialGridCard material={material} pathBase="/browse/math" navIndex={0} />,
    );
    expect(container.textContent).toContain("Doc 1");
  });

  it("calls onNavigate when clicked if provided", async () => {
    const onNavigateMock = vi.fn();
    await render(
      <MaterialGridCard
        material={material}
        pathBase="/browse/math"
        navIndex={0}
        onNavigate={onNavigateMock}
      />,
    );

    const linkElement = container.querySelector("a") as HTMLAnchorElement;
    expect(linkElement).not.toBeNull();

    const clickEvent = new MouseEvent("click", { bubbles: true, cancelable: true });
    await act(async () => {
      linkElement.dispatchEvent(clickEvent);
    });

    expect(clickEvent.defaultPrevented).toBe(true);
    expect(onNavigateMock).toHaveBeenCalledTimes(1);
  });

  it("triggers onToggleSelect in selectMode", async () => {
    const onToggleSelectMock = vi.fn();
    await render(
      <MaterialGridCard
        material={material}
        pathBase="/browse/math"
        navIndex={0}
        selectMode={true}
        onToggleSelect={onToggleSelectMock}
      />,
    );

    const linkElement = container.querySelector("a") as HTMLAnchorElement;
    const clickEvent = new MouseEvent("click", { bubbles: true, cancelable: true });
    await act(async () => {
      linkElement.dispatchEvent(clickEvent);
    });

    expect(clickEvent.defaultPrevented).toBe(true);
    expect(onToggleSelectMock).toHaveBeenCalledTimes(1);
  });
});
