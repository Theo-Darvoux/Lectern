import React, { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { ContributionList } from "./contribution-list";
import { apiFetch } from "@/lib/api-client";

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={String(href)} {...props}>{children}</a>
  ),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => Object.assign((key: string) => key, { has: () => true }),
}));

vi.mock("@/lib/api-client", () => ({ apiFetch: vi.fn() }));
vi.mock("@/components/home/material-card", () => ({
  MaterialCard: ({ material }: { material: { title: string } }) => (
    <article data-material-card>{material.title}</article>
  ),
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockedApiFetch = vi.mocked(apiFetch);

describe("ContributionList cards", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockedApiFetch.mockImplementation(async (url) => ({
      items: [
        {
          id: String(url).includes("page=2") ? "material-2" : "material-1",
          directory_id: "directory-1",
          directory_path: "Algorithms",
          title: String(url).includes("page=2") ? "Graphs" : "Sorting",
          slug: String(url).includes("page=2") ? "graphs" : "sorting",
          description: null,
          type: "document",
          download_count: 0,
          total_views: 12,
          like_count: 3,
          is_liked: false,
          is_favourited: false,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
      total: 9,
      page: String(url).includes("page=2") ? 2 : 1,
      pages: 2,
    }));
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  it("renders material cards in a responsive grid and keeps server pagination", async () => {
    await act(async () => {
      root.render(<ContributionList userId="user-1" type="materials" />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const grid = container.querySelector<HTMLElement>("[data-contribution-grid]");
    expect(grid?.className).toContain("grid-cols-2");
    expect(grid?.className).toContain("sm:grid-cols-3");
    expect(grid?.className).toContain("lg:grid-cols-4");
    expect(container.querySelectorAll("[data-material-card]")).toHaveLength(1);

    const pagination = container.querySelector<HTMLElement>("[data-profile-pagination]");
    expect(pagination?.className).toContain("flex-col");
    expect(pagination?.className).toContain("sm:flex-row");

    const next = container.querySelector<HTMLButtonElement>('button[aria-label="nextPage"]');
    expect(next).not.toBeNull();

    await act(async () => {
      next?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(mockedApiFetch).toHaveBeenLastCalledWith(
      expect.stringContaining("page=2"),
    );
    expect(container.textContent).toContain("Graphs");
  });

  it("renders annotation cards as clickable links to the annotated material", async () => {
    mockedApiFetch.mockResolvedValueOnce({
      items: [
        {
          id: "ann-123",
          material_id: "mat-456",
          material_title: "Calculus Notes",
          material_slug: "calculus-notes",
          directory_path: "math/analysis",
          body: "Crucial derivative rule to remember",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      pages: 1,
    });

    await act(async () => {
      root.render(<ContributionList userId="user-1" type="annotations" />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const link = container.querySelector<HTMLAnchorElement>("a[data-annotation-card]");
    expect(link).not.toBeNull();
    expect(link?.getAttribute("href")).toBe("/browse/math/analysis/calculus-notes?annotation=ann-123");
    expect(container.textContent).toContain("Crucial derivative rule to remember");
    expect(container.textContent).toContain("Calculus Notes");
  });
});
