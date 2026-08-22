import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SearchListCard, SearchGridItem } from "./search-results-list";
import type { SearchResult } from "./use-search";

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

vi.mock("@/components/search/search-modal", () => ({
  SearchResultThumbnail: () => <div data-testid="thumbnail" />,
}));

vi.mock("@/components/browse/directory-grid-card", () => ({
  DirectoryGridCard: ({ directory }: { directory: Record<string, unknown> }) => (
    <div data-testid="directory-grid-card">{String(directory.name)}</div>
  ),
}));

vi.mock("@/components/browse/material-grid-card", () => ({
  MaterialGridCard: ({ material }: { material: Record<string, unknown> }) => (
    <div data-testid="material-grid-card">{String(material.title)}</div>
  ),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("SearchListCard and SearchGridItem", () => {
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

  it("renders SearchListCard with responsive elements for a material result", () => {
    const mockResult: SearchResult = {
      id: "mat-1",
      search_type: "material",
      title: "Linear Algebra Notes",
      browse_path: "/materials/mat-1",
      file_name: "notes.pdf",
      file_mime_type: "application/pdf",
      description: "Comprehensive notes for semester 1",
      ancestor_path: "Math / Algebra",
      total_views: 42,
      like_count: 7,
      tags: ["math", "algebra"],
      match_context: "Linear combinations",
      matched_field: "description",
    };

    const handleSelect = vi.fn();

    act(() => {
      root.render(<SearchListCard result={mockResult} onSelect={handleSelect} />);
    });

    const card = container.querySelector('[role="button"]');
    expect(card).toBeTruthy();
    expect(container.textContent).toContain("Linear Algebra Notes");
    expect(container.textContent).toContain("Math / Algebra");
    expect(container.textContent).toContain("Comprehensive notes for semester 1");
    expect(container.textContent).toContain("#math");

    act(() => {
      card?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(handleSelect).toHaveBeenCalledWith(mockResult);
  });

  it("renders SearchGridItem for a directory", () => {
    const mockDir: SearchResult = {
      id: "dir-1",
      search_type: "directory",
      name: "Computer Science",
      browse_path: "/directories/dir-1",
    };

    act(() => {
      root.render(<SearchGridItem result={mockDir} />);
    });

    expect(container.querySelector('[data-testid="directory-grid-card"]')).toBeTruthy();
    expect(container.textContent).toContain("Computer Science");
  });

  it("renders SearchGridItem for a material", () => {
    const mockMat: SearchResult = {
      id: "mat-2",
      search_type: "material",
      title: "Operating Systems Lecture 1",
      browse_path: "/materials/mat-2",
    };

    act(() => {
      root.render(<SearchGridItem result={mockMat} />);
    });

    expect(container.querySelector('[data-testid="material-grid-card"]')).toBeTruthy();
    expect(container.textContent).toContain("Operating Systems Lecture 1");
  });
});
