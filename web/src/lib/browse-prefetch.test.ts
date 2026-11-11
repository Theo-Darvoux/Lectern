import { describe, it, expect, vi, beforeEach } from "vitest";
import { apiFetch } from "./api-client";
import {
  fetchBrowsePath,
  invalidateBrowseEntity,
  invalidateBrowsePath,
  prefetchBrowsePath,
} from "./browse-prefetch";
import { getQueryClient } from "./query-client";

vi.mock("./api-client", () => ({
  apiFetch: vi.fn(),
  isRetriableError: () => false,
}));

const mockApiFetch = vi.mocked(apiFetch);

/** A promise whose resolution we control, to overlap concurrent requests. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const isInvalidated = (path: string) =>
  getQueryClient().getQueryState(["browse", path])?.isInvalidated ?? false;

describe("browse-prefetch", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getQueryClient().clear();
  });

  it("maps the empty path to /browse and a path to /browse/<path>", async () => {
    mockApiFetch.mockResolvedValue({ ok: true });

    await fetchBrowsePath("");
    await fetchBrowsePath("a/b");

    const options = expect.objectContaining({
      timeoutMs: expect.any(Number),
      signal: expect.any(AbortSignal),
    });
    expect(mockApiFetch).toHaveBeenNthCalledWith(1, "/browse", options);
    expect(mockApiFetch).toHaveBeenNthCalledWith(2, "/browse/a/b", options);
  });

  it("serves a recent copy from cache", async () => {
    const payload = { type: "directory_listing" };
    mockApiFetch.mockResolvedValue(payload);

    expect(await fetchBrowsePath("maths")).toBe(payload);
    expect(await fetchBrowsePath("maths")).toBe(payload);
    expect(mockApiFetch).toHaveBeenCalledTimes(1);
  });

  it("joins an in-flight prefetch instead of issuing a second request (hover then click)", async () => {
    const d = deferred<unknown>();
    mockApiFetch.mockReturnValue(d.promise as never);

    const hover = prefetchBrowsePath("chimie");
    const click = fetchBrowsePath("chimie");
    d.resolve({ ok: true });
    await Promise.all([hover, click]);

    expect(mockApiFetch).toHaveBeenCalledTimes(1);
  });

  it("prefetch is a no-op when the path is already cached", async () => {
    mockApiFetch.mockResolvedValue({ ok: true });
    await fetchBrowsePath("bio");
    await prefetchBrowsePath("bio");

    expect(mockApiFetch).toHaveBeenCalledTimes(1);
  });

  it("swallows prefetch errors, and a later fetch tries again", async () => {
    mockApiFetch.mockRejectedValueOnce(new Error("boom"));
    await expect(prefetchBrowsePath("broken")).resolves.toBeUndefined();

    const payload = { type: "directory_listing" };
    mockApiFetch.mockResolvedValueOnce(payload);
    expect(await fetchBrowsePath("broken")).toBe(payload);
    expect(mockApiFetch).toHaveBeenCalledTimes(2);
  });

  it("propagates errors from a direct fetch", async () => {
    mockApiFetch.mockRejectedValue(new Error("network"));
    await expect(fetchBrowsePath("nope")).rejects.toThrow("network");
  });

  it("invalidates exactly one path", async () => {
    mockApiFetch.mockResolvedValue({ ok: true });
    await fetchBrowsePath("live");
    await fetchBrowsePath("other");

    invalidateBrowsePath("live");

    expect(isInvalidated("live")).toBe(true);
    expect(isInvalidated("other")).toBe(false);
  });

  it("invalidates every cached representation of an SSE entity, and the current path", async () => {
    mockApiFetch
      .mockResolvedValueOnce({
        type: "directory_listing",
        directory: { id: "root" },
        materials: [{ id: "material-1" }],
      })
      .mockResolvedValueOnce({ type: "material", material: { id: "material-1" } })
      .mockResolvedValueOnce({ type: "material", material: { id: "material-2" } })
      .mockResolvedValueOnce({ type: "material", material: { id: "material-3" } });

    await fetchBrowsePath("");
    await fetchBrowsePath("material-1");
    await fetchBrowsePath("material-2");
    await fetchBrowsePath("material-3");
    invalidateBrowseEntity("material:material-1", "material-3");

    expect(isInvalidated("")).toBe(true);
    expect(isInvalidated("material-1")).toBe(true);
    expect(isInvalidated("material-2")).toBe(false);
    expect(isInvalidated("material-3")).toBe(true);
  });
});
