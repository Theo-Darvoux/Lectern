import { describe, expect, it, vi } from "vitest";
import { ResourceCache } from "./resource-cache";

describe("ResourceCache", () => {
    it("expires entries after their TTL", () => {
        vi.useFakeTimers();
        const cache = new ResourceCache<string, number>({ maxEntries: 2, ttlMs: 1_000 });
        cache.set("a", 1);

        vi.advanceTimersByTime(1_001);
        expect(cache.has("a")).toBe(false);
        expect(cache.get("a")).toBeUndefined();
        vi.useRealTimers();
    });

    it("evicts the least recently used entry at capacity", () => {
        const cache = new ResourceCache<string, number>({ maxEntries: 2, ttlMs: 60_000 });
        cache.set("a", 1);
        cache.set("b", 2);
        cache.get("a");
        cache.set("c", 3);

        expect(cache.has("a")).toBe(true);
        expect(cache.has("b")).toBe(false);
        expect(cache.has("c")).toBe(true);
    });

    it("invalidates all representations carrying an entity tag", () => {
        const cache = new ResourceCache<string, number>({ maxEntries: 4, ttlMs: 60_000 });
        cache.set("listing", 1, { tags: ["material:1", "directory:root"] });
        cache.set("detail", 2, { tags: ["material:1"] });
        cache.set("other", 3, { tags: ["material:2"] });

        expect(cache.invalidateTag("material:1")).toEqual(["listing", "detail"]);
        expect(cache.has("listing")).toBe(false);
        expect(cache.has("detail")).toBe(false);
        expect(cache.get("other")).toBe(3);
    });
});
