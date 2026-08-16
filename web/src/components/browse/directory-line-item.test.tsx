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
  useLocale: () => "fr",
  useTranslations: () => {
    const t = (key: string, params?: Record<string, unknown>) => {
      if (key === "itemsCount") return `${params?.count ?? 0} éléments`;
      return key;
    };
    (t as unknown as { has: () => boolean }).has = () => false;
    return t;
  },
}));

vi.mock("@/lib/browse-prefetch", () => ({ prefetchBrowsePath: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ apiFetch: vi.fn() }));
vi.mock("./item-actions-menu", () => ({
  ItemActionsMenu: ({ children }: { children: React.ReactNode }) => children,
  ItemActionsDropdownTrigger: () => null,
}));

import { DirectoryLineItem } from "./directory-line-item";
import { useDirectoryColorOverrides, useDirectoryIconOverrides } from "@/lib/stores";

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

describe("DirectoryLineItem", () => {
  beforeEach(() => {
    mockPush.mockClear();
    if (container) {
      container.remove();
    }
    useDirectoryColorOverrides.setState({ overrides: new Map() });
    useDirectoryIconOverrides.setState({ overrides: new Map() });
  });

  it("renders folder name and item count", async () => {
    const dir = {
      id: "dir-1",
      slug: "analyse-de-donnees",
      name: "Analyse de données",
      child_directory_count: 2,
      child_material_count: 2,
    };

    await render(
      <DirectoryLineItem
        directory={dir}
        pathBase="/browse/math"
        isMobile={false}
      />
    );

    expect(container.textContent).toContain("Analyse de données");
    expect(container.textContent).toContain("4 éléments");
  });

  it("applies the default blue color when no metadata thumbnail_color is set", async () => {
    const dir = {
      id: "dir-1",
      slug: "cf2",
      name: "CF2",
    };

    await render(
      <DirectoryLineItem
        directory={dir}
        pathBase="/browse/math"
        isMobile={false}
      />
    );

    const svgIcon = container.querySelector("svg");
    expect(svgIcon).not.toBeNull();
    expect(svgIcon?.className.baseVal || svgIcon?.getAttribute("class")).toContain("text-blue-400");
  });

  it("applies the color set in metadata (e.g. green)", async () => {
    const dir = {
      id: "dir-green",
      slug: "analyse-de-donnees",
      name: "Analyse de données",
      metadata: { thumbnail_color: "green" },
    };

    await render(
      <DirectoryLineItem
        directory={dir}
        pathBase="/browse/math"
        isMobile={false}
      />
    );

    const svgIcon = container.querySelector("svg");
    expect(svgIcon).not.toBeNull();
    expect(svgIcon?.className.baseVal || svgIcon?.getAttribute("class")).toContain("text-green-400");
  });

  it("applies store color override dynamically", async () => {
    const dir = {
      id: "dir-dyn",
      slug: "stats",
      name: "Statistiques",
      metadata: { thumbnail_color: "blue" },
    };

    useDirectoryColorOverrides.getState().setColorOverride("dir-dyn", "purple");

    await render(
      <DirectoryLineItem
        directory={dir}
        pathBase="/browse/math"
        isMobile={false}
      />
    );

    const svgIcon = container.querySelector("svg");
    expect(svgIcon).not.toBeNull();
    expect(svgIcon?.className.baseVal || svgIcon?.getAttribute("class")).toContain("text-purple-400");
  });

  it("applies staged deleted styling", async () => {
    const dir = {
      id: "dir-deleted",
      slug: "deleted-dir",
      name: "Deleted Folder",
      metadata: { thumbnail_color: "green" },
    };

    await render(
      <DirectoryLineItem
        directory={dir}
        staged="deleted"
        pathBase="/browse/math"
        isMobile={false}
      />
    );

    const svgIcon = container.querySelector("svg");
    expect(svgIcon).not.toBeNull();
    expect(svgIcon?.className.baseVal || svgIcon?.getAttribute("class")).toContain("text-red-500");
  });
});
