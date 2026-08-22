import { describe, expect, it } from "vitest";

import {
  getValidSearchPage,
  parseSearchPageState,
  SEARCH_MAX_PAGE,
  updateSearchPageParams,
} from "./search-page-state";

describe("search page URL state", () => {
  it("sanitizes unsupported filters and page values", () => {
    const state = parseSearchPageState(
      new URLSearchParams("q=algebra&kind=unknown&type=exe&status=deleted&page=-2"),
    );

    expect(state).toEqual({
      query: "algebra",
      kind: "all",
      materialType: "all",
      status: "all",
      page: 1,
      directoryId: undefined,
      directoryName: undefined,
      recursive: false,
    });
  });

  it("resets pagination when a search filter changes", () => {
    const params = new URLSearchParams("q=algebra&kind=material&page=4");

    expect(updateSearchPageParams(params, { status: "current" }).toString()).toBe(
      "q=algebra&kind=material&status=current",
    );
  });

  it("keeps all mixed-index pages reachable and recovers stale page numbers", () => {
    expect(parseSearchPageState(new URLSearchParams("page=999")).page).toBe(SEARCH_MAX_PAGE);
    expect(getValidSearchPage(61, 20, 50)).toBe(4);
    expect(getValidSearchPage(0, 20, 7)).toBe(1);
  });
});
