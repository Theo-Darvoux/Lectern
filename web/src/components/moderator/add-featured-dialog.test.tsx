import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FeaturedItemSearch } from "./add-featured-dialog";

const searchMock = vi.hoisted(() => ({
  retry: vi.fn(),
  useSearch: vi.fn(),
}));

vi.mock("@/components/search/use-search", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/search/use-search")>();
  return {
    ...actual,
    useSearch: searchMock.useSearch,
  };
});

function errorSearchState() {
  return {
      results: [],
      total: 0,
      status: "error",
      error: new Error("offline"),
      retry: searchMock.retry,
      loading: false,
      requestKey: "/search?query=algebra",
      resolvedRequestKey: null,
  };
}

vi.mock("@/components/ui/popover", () => ({
  Popover: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  PopoverTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  PopoverContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock("next-intl", () => ({
  useTranslations: (namespace: string) => (key: string) => {
    const messages: Record<string, string> = {
      "Moderator.featured.dialog.searchPlaceholder": "Search materials and folders",
      "Search.results": "Search results",
      "Search.searchUnavailable": "Search is temporarily unavailable",
      "Search.searchUnavailableDescription": "Please try again in a moment.",
      "Search.retry": "Try again",
      "Search.filterByKind": "Filter search results",
      "Search.kinds.all": "All",
      "Search.kinds.material": "Materials",
      "Search.kinds.directory": "Folders",
      "Search.resultCount": "1 result",
      "Search.inLocation": "In Mathematics",
      "Search.material": "Material",
      "Search.untitled": "Untitled",
      "Search.matchedFields.tag": "Tag",
    };
    return messages[`${namespace}.${key}`] ?? key;
  },
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
(globalThis as unknown as { ResizeObserver: typeof ResizeObserver }).ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;
Element.prototype.scrollIntoView = vi.fn();

describe("FeaturedItemSearch", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    searchMock.retry.mockReset();
    searchMock.useSearch.mockReset();
    searchMock.useSearch.mockReturnValue(errorSearchState());
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("shows the shared actionable search error instead of a false empty state", () => {
    act(() =>
      root.render(
        <FeaturedItemSearch
          selectedTitle=""
          onSelect={vi.fn()}
        />,
      ),
    );

    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "Search is temporarily unavailable",
    );
    const retryButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "Try again",
    );
    act(() => retryButton?.click());
    expect(searchMock.retry).toHaveBeenCalledOnce();
  });

  it("uses the same accessible result-kind filters as global search", () => {
    act(() =>
      root.render(
        <FeaturedItemSearch selectedTitle="" onSelect={vi.fn()} />,
      ),
    );

    const materials = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "Materials",
    );
    expect(materials).toBeDefined();
    expect(materials?.getAttribute("aria-pressed")).toBe("false");

    act(() => materials?.click());

    expect(materials?.getAttribute("aria-pressed")).toBe("true");
    expect(searchMock.useSearch).toHaveBeenLastCalledWith("", { kind: "material" });
  });

  it("shows shared path and match context while preserving picker selection", () => {
    searchMock.useSearch.mockReturnValue({
      results: [
        {
          id: "material-1",
          search_type: "material",
          title: "Linear Algebra",
          type: "document",
          browse_path: "/browse/math/linear-algebra",
          ancestor_path: "Mathematics",
          matched_field: "tag",
          match_context: "vectors",
        },
      ],
      total: 1,
      status: "success",
      error: null,
      retry: searchMock.retry,
      loading: false,
      requestKey: "/search?query=linear",
      resolvedRequestKey: "/search?query=linear",
    });
    const onSelect = vi.fn();

    act(() =>
      root.render(
        <FeaturedItemSearch selectedTitle="" onSelect={onSelect} />,
      ),
    );

    expect(container.textContent).toContain("Linear Algebra");
    expect(container.textContent).toContain("In Mathematics");
    expect(container.textContent).toContain("Tag:");
    expect(container.textContent).toContain("vectors");

    const result = container.querySelector('[cmdk-item]') as HTMLElement | null;
    act(() => result?.click());
    expect(onSelect).toHaveBeenCalledWith(
      "material-1",
      "Linear Algebra",
      "material",
      "/math/linear-algebra",
    );
  });
});
