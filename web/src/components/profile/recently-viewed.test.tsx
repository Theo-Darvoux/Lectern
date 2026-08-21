import React, { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { apiFetch } from "@/lib/api-client";
import { RecentlyViewed } from "./recently-viewed";

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={String(href)} {...props}>{children}</a>
  ),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

vi.mock("@/lib/api-client", () => ({ apiFetch: vi.fn() }));
vi.mock("@/components/home/material-card", () => ({
  MaterialCard: ({ material }: { material: { title: string } }) => (
    <article data-material-card>{material.title}</article>
  ),
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("RecentlyViewed", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    vi.mocked(apiFetch).mockResolvedValue(
      Array.from({ length: 9 }, (_, index) => ({
        id: `material-${index + 1}`,
        title: `Material ${index + 1}`,
        slug: `material-${index + 1}`,
        type: "document",
        directory_id: "directory-1",
        directory_path: "Algorithms",
      })),
    );
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  it("renders recently viewed materials as paginated shared responsive cards", async () => {
    await act(async () => {
      root.render(<RecentlyViewed />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const grid = container.querySelector<HTMLElement>("[data-recent-material-grid]");
    expect(grid?.className).toContain("grid-cols-2");
    expect(grid?.className).toContain("sm:grid-cols-3");
    expect(grid?.className).toContain("lg:grid-cols-4");
    expect(container.querySelectorAll("[data-material-card]")).toHaveLength(8);

    const next = container.querySelector<HTMLButtonElement>('button[aria-label="nextPage"]');
    expect(next).not.toBeNull();

    await act(async () => {
      next?.click();
    });

    expect(container.querySelectorAll("[data-material-card]")).toHaveLength(1);
    expect(container.textContent).toContain("Material 9");
  });
});
