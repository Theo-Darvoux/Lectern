import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const searchState = vi.hoisted(() => ({
  results: [
    {
      id: "algebra-1",
      search_type: "material" as const,
      title: "Algebra 1",
      browse_path: "/materials/algebra-1",
    },
  ],
  total: 1,
  status: "loading" as const,
  error: null,
  retry: vi.fn(),
  requestKey: "algebra|directory",
  resolvedRequestKey: "algebra|material",
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams("q=algebra&kind=directory"),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

vi.mock("@/components/search/use-search", () => ({
  getSearchErrorMessageKey: () => "searchUnavailableDescription",
  useSearch: () => searchState,
}));

vi.mock("@/components/search/search-modal", () => ({
  SearchResultThumbnail: () => <div data-testid="thumbnail" />,
}));

vi.mock("@/components/browse/directory-grid-card", () => ({
  DirectoryGridCard: () => <div data-testid="directory-grid-card" />,
}));

vi.mock("@/components/browse/material-grid-card", () => ({
  MaterialGridCard: () => <div data-testid="material-grid-card" />,
}));

vi.mock("@/lib/external-link-store", () => ({
  useExternalLinkStore: (selector: (state: { openLink: ReturnType<typeof vi.fn> }) => unknown) =>
    selector({ openLink: vi.fn() }),
}));

import SearchPage from "./page";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("SearchPage filter transitions and view modes", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("keeps existing results visually stable and interactive while filters refresh", () => {
    act(() => root.render(<SearchPage />));

    const result = Array.from(container.querySelectorAll('[role="button"]')).find((element) =>
      element.textContent?.includes("Algebra 1"),
    );
    expect(result).toBeTruthy();

    const resultsContainer = result?.parentElement;
    expect(resultsContainer?.classList.contains("opacity-60")).toBe(false);
    expect(resultsContainer?.classList.contains("pointer-events-none")).toBe(false);
  });

  it("toggles to grid view when grid view button is clicked", () => {
    act(() => root.render(<SearchPage />));

    const gridBtn = container.querySelector('button[aria-label="Grid View"]');
    expect(gridBtn).toBeTruthy();

    act(() => {
      gridBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(container.querySelector('[data-testid="material-grid-card"]')).toBeTruthy();
  });
});
