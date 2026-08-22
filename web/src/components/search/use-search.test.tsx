import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch } from "@/lib/api-client";
import {
  buildSearchPath,
  getSearchErrorMessageKey,
  useSearch,
  type SearchOptions,
  type SearchResponse,
} from "./use-search";

vi.mock("@/lib/api-client", () => ({
  apiFetch: vi.fn(),
  ApiError: class ApiError extends Error {
    constructor(public status: number, message: string) {
      super(message);
    }
  },
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function response(title: string): SearchResponse {
  return {
    items: [
      {
        id: title.toLowerCase(),
        search_type: "material",
        title,
        browse_path: `/browse/${title.toLowerCase()}`,
      },
    ],
    total: 1,
    page: 1,
    limit: 10,
  };
}

function renderSearch(initialQuery = "", initialOptions: SearchOptions = {}) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root: Root = createRoot(container);
  let latest: ReturnType<typeof useSearch> | undefined;

  function Harness({ query, options }: { query: string; options?: SearchOptions }) {
    const value = useSearch(query, options);
    React.useEffect(() => {
      latest = value;
    }, [value]);
    return null;
  }

  const render = (query: string, options?: SearchOptions) => {
    act(() => root.render(<Harness query={query} options={options} />));
  };

  render(initialQuery, initialOptions);
  return {
    get current() {
      if (!latest) throw new Error("Search hook did not render");
      return latest;
    },
    render,
    cleanup() {
      act(() => root.unmount());
      container.remove();
    },
  };
}

describe("useSearch", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(apiFetch).mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("never publishes results from an older query after the text changes", async () => {
    const algebra = deferred<SearchResponse>();
    vi.mocked(apiFetch).mockReturnValueOnce(algebra.promise);
    const hook = renderSearch("algebra");

    await act(async () => vi.advanceTimersByTime(300));
    hook.render("calculus");

    await act(async () => {
      algebra.resolve(response("Algebra"));
      await algebra.promise;
    });

    expect(hook.current.results).toEqual([]);
    hook.cleanup();
  });

  it("aborts an in-flight request when the query changes", async () => {
    vi.mocked(apiFetch).mockReturnValue(new Promise(() => undefined));
    const hook = renderSearch("algebra");

    await act(async () => vi.advanceTimersByTime(300));
    const firstOptions = vi.mocked(apiFetch).mock.calls[0]?.[1];
    expect(firstOptions?.signal?.aborted).toBe(false);

    hook.render("calculus");

    expect(firstOptions?.signal?.aborted).toBe(true);
    hook.cleanup();
  });

  it("searches again when a user returns to the last debounced query", async () => {
    vi.mocked(apiFetch)
      .mockReturnValueOnce(new Promise(() => undefined))
      .mockResolvedValueOnce(response("Algebra"));
    const hook = renderSearch("algebra");

    await act(async () => vi.advanceTimersByTime(300));
    hook.render("calculus");
    hook.render("algebra");

    await act(async () => {
      vi.advanceTimersByTime(300);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(apiFetch).toHaveBeenCalledTimes(2);
    expect(hook.current.results[0]?.title).toBe("Algebra");
    hook.cleanup();
  });

  it("exposes request failures instead of presenting them as empty results", async () => {
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error("Search unavailable"));
    const hook = renderSearch("algebra");

    await act(async () => {
      vi.advanceTimersByTime(300);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(hook.current.status).toBe("error");
    expect(hook.current.error?.message).toBe("Search unavailable");
    hook.cleanup();
  });

  it("does not identify old totals as belonging to a newly rendered query", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(response("Algebra"));
    const hook = renderSearch("algebra");

    await act(async () => {
      vi.advanceTimersByTime(300);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(hook.current.resolvedRequestKey).toBe(hook.current.requestKey);

    hook.render("calculus");

    expect(hook.current.resolvedRequestKey).not.toBe(hook.current.requestKey);
    hook.cleanup();
  });

  it("builds one canonical request for result-page filters and scope", () => {
    expect(
      buildSearchPath("linear algebra", {
        page: 3,
        limit: 24,
        kind: "material",
        materialType: "document",
        status: "current",
        directoryId: "directory-id",
        recursive: true,
      }),
    ).toBe(
      "/search?query=linear+algebra&page=3&limit=24&kind=material&material_type=document&status=current&directory_id=directory-id&recursive=true",
    );
  });

  it("retries the current query after a visible failure", async () => {
    vi.mocked(apiFetch)
      .mockRejectedValueOnce(new Error("Search unavailable"))
      .mockResolvedValueOnce(response("Algebra"));
    const hook = renderSearch("algebra");

    await act(async () => {
      vi.advanceTimersByTime(300);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(hook.current.status).toBe("error");

    await act(async () => {
      hook.current.retry();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(hook.current.status).toBe("success");
    expect(hook.current.results[0]?.title).toBe("Algebra");
    hook.cleanup();
  });

  it("maps actionable request failures to localized guidance", () => {
    expect(getSearchErrorMessageKey(new ApiError(400, "too broad"))).toBe("refineQuery");
    expect(getSearchErrorMessageKey(new ApiError(429, "slow down"))).toBe("slowDown");
    expect(getSearchErrorMessageKey(new Error("offline"))).toBe(
      "searchUnavailableDescription",
    );
  });

  it("preserves previous results while fetching updated filters or pages to prevent flickering", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(response("Algebra 1"));
    const hook = renderSearch("algebra", { kind: "material" });

    await act(async () => {
      vi.advanceTimersByTime(300);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(hook.current.results[0]?.title).toBe("Algebra 1");

    const deferredSecond = deferred<SearchResponse>();
    vi.mocked(apiFetch).mockReturnValueOnce(deferredSecond.promise);

    // Update filter from 'material' to 'directory'
    hook.render("algebra", { kind: "directory" });

    // Existing results should still be present while loading the new filter request
    expect(hook.current.status).toBe("loading");
    expect(hook.current.results.length).toBe(1);
    expect(hook.current.results[0]?.title).toBe("Algebra 1");

    // Once resolved, results are updated to new data
    await act(async () => {
      deferredSecond.resolve(response("Algebra Folder"));
      await deferredSecond.promise;
    });

    expect(hook.current.status).toBe("success");
    expect(hook.current.results[0]?.title).toBe("Algebra Folder");
    hook.cleanup();
  });
});
