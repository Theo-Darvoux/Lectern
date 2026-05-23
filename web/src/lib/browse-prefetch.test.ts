import { describe, it, expect, vi, beforeEach } from "vitest";
import { apiFetch } from "./api-client";
import {
  browseCache,
  fetchBrowsePath,
  prefetchBrowsePath,
} from "./browse-prefetch";

vi.mock("./api-client", () => ({
  apiFetch: vi.fn(),
}));

const mockApiFetch = vi.mocked(apiFetch);

/** A promise whose resolution we control, to overlap concurrent requests. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (err: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("browse-prefetch", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    browseCache.clear();
  });

  it("maps the empty path to /browse and a path to /browse/<path>", async () => {
    mockApiFetch.mockResolvedValue({ ok: true });

    await fetchBrowsePath("");
    await fetchBrowsePath("a/b");

    expect(mockApiFetch).toHaveBeenNthCalledWith(1, "/browse");
    expect(mockApiFetch).toHaveBeenNthCalledWith(2, "/browse/a/b");
  });

  it("caches the result and serves subsequent reads from cache", async () => {
    const payload = { type: "directory_listing" };
    mockApiFetch.mockResolvedValue(payload);

    const first = await fetchBrowsePath("maths");
    expect(first).toBe(payload);
    expect(browseCache.get("maths")).toBe(payload);

    const second = await fetchBrowsePath("maths");
    expect(second).toBe(payload);
    // Cache hit — no second network call.
    expect(mockApiFetch).toHaveBeenCalledTimes(1);
  });

  it("de-duplicates concurrent requests for the same path", async () => {
    const d = deferred<unknown>();
    mockApiFetch.mockReturnValue(d.promise);

    const p1 = fetchBrowsePath("physique");
    const p2 = fetchBrowsePath("physique");

    expect(mockApiFetch).toHaveBeenCalledTimes(1);

    const payload = { type: "directory_listing" };
    d.resolve(payload);

    expect(await p1).toBe(payload);
    expect(await p2).toBe(payload);
  });

  it("joins an in-flight prefetch instead of issuing a second request (hover then click)", async () => {
    const d = deferred<unknown>();
    mockApiFetch.mockReturnValue(d.promise);

    // Hover starts a prefetch...
    const hover = prefetchBrowsePath("chimie");
    // ...then the user clicks before it resolves.
    const click = fetchBrowsePath("chimie");

    expect(mockApiFetch).toHaveBeenCalledTimes(1);

    d.resolve({ ok: true });
    await Promise.all([hover, click]);
  });

  it("force bypasses the cache to revalidate", async () => {
    const stale = { v: 1 };
    const fresh = { v: 2 };
    mockApiFetch.mockResolvedValueOnce(stale).mockResolvedValueOnce(fresh);

    await fetchBrowsePath("info");
    expect(browseCache.get("info")).toBe(stale);

    const revalidated = await fetchBrowsePath("info", { force: true });
    expect(revalidated).toBe(fresh);
    expect(browseCache.get("info")).toBe(fresh);
    expect(mockApiFetch).toHaveBeenCalledTimes(2);
  });

  it("preserves the cached object identity when a revalidation is unchanged", async () => {
    const original = { type: "directory_listing", directories: [{ id: "1" }] };
    // Server returns a structurally-equal but distinct object on revalidation.
    const equalCopy = { type: "directory_listing", directories: [{ id: "1" }] };
    mockApiFetch.mockResolvedValueOnce(original).mockResolvedValueOnce(equalCopy);

    await fetchBrowsePath("svt");
    expect(browseCache.get("svt")).toBe(original);

    const revalidated = await fetchBrowsePath("svt", { force: true });
    // Identity is kept so downstream memoized rows can bail out of re-rendering.
    expect(revalidated).toBe(original);
    expect(browseCache.get("svt")).toBe(original);
    expect(mockApiFetch).toHaveBeenCalledTimes(2);
  });

  it("prefetch is a no-op when the path is already cached", async () => {
    mockApiFetch.mockResolvedValue({ ok: true });
    await fetchBrowsePath("bio");
    expect(mockApiFetch).toHaveBeenCalledTimes(1);

    await prefetchBrowsePath("bio");
    expect(mockApiFetch).toHaveBeenCalledTimes(1);
  });

  it("swallows prefetch errors and leaves the path uncached", async () => {
    mockApiFetch.mockRejectedValue(new Error("boom"));

    await expect(prefetchBrowsePath("broken")).resolves.toBeUndefined();
    expect(browseCache.has("broken")).toBe(false);
  });

  it("propagates errors from a foreground fetch", async () => {
    mockApiFetch.mockRejectedValue(new Error("network"));
    await expect(fetchBrowsePath("nope")).rejects.toThrow("network");
    expect(browseCache.has("nope")).toBe(false);
  });
});
