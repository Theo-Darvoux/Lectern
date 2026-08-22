import { describe, expect, it } from "vitest";

import { parseSearchPageState, updateSearchPageParams } from "./search-page-state";

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
});
