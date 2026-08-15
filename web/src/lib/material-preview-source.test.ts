import { afterEach, describe, expect, it, vi } from "vitest";
import { apiFetch, apiFetchRetry } from "./api-client";
import {
    clearMaterialPreviewCache,
    getMaterialThumbnail,
    recalculateMaterialThumbnail,
} from "./material-preview-source";

vi.mock("./api-client", () => ({
    apiFetch: vi.fn(),
    apiFetchRetry: vi.fn(),
}));

const mockedApiFetch = vi.mocked(apiFetch);
const mockedApiFetchRetry = vi.mocked(apiFetchRetry);

afterEach(() => {
    clearMaterialPreviewCache();
    vi.clearAllMocks();
});

describe("getMaterialThumbnail", () => {
    it("deduplicates concurrent requests for the same material", async () => {
        let resolveRequest!: (value: { url: string; thumbnail_type: "webp" }) => void;
        mockedApiFetchRetry.mockReturnValueOnce(
            new Promise((resolve) => {
                resolveRequest = resolve;
            }),
        );

        const first = getMaterialThumbnail("material-1");
        const second = getMaterialThumbnail("material-1");
        resolveRequest({ url: "https://cdn.test/preview.webp", thumbnail_type: "webp" });

        await expect(Promise.all([first, second])).resolves.toEqual([
            { url: "https://cdn.test/preview.webp", thumbnailType: "webp" },
            { url: "https://cdn.test/preview.webp", thumbnailType: "webp" },
        ]);
        expect(mockedApiFetchRetry).toHaveBeenCalledOnce();
    });

    it("bounds thumbnail request concurrency", async () => {
        const resolvers: Array<() => void> = [];
        mockedApiFetchRetry.mockImplementation(
            () =>
                new Promise((resolve) => {
                    resolvers.push(() =>
                        resolve({ url: "https://cdn.test/preview.webp", thumbnail_type: "webp" }),
                    );
                }),
        );

        const requests = Array.from({ length: 5 }, (_, index) =>
            getMaterialThumbnail(`material-${index}`),
        );
        await Promise.resolve();

        expect(mockedApiFetchRetry).toHaveBeenCalledTimes(4);

        resolvers.shift()?.();
        await Promise.resolve();
        await Promise.resolve();
        expect(mockedApiFetchRetry).toHaveBeenCalledTimes(5);

        for (const resolve of resolvers) resolve();
        await Promise.all(requests);
    });

    it("refreshes cached signed URLs after their safe TTL", async () => {
        vi.useFakeTimers();
        mockedApiFetchRetry
            .mockResolvedValueOnce({ url: "https://cdn.test/first", thumbnail_type: "webp" })
            .mockResolvedValueOnce({ url: "https://cdn.test/second", thumbnail_type: "webp" });

        await expect(getMaterialThumbnail("material-1")).resolves.toMatchObject({
            url: "https://cdn.test/first",
        });
        vi.advanceTimersByTime(10 * 60 * 1000 + 1);
        await expect(getMaterialThumbnail("material-1")).resolves.toMatchObject({
            url: "https://cdn.test/second",
        });

        expect(mockedApiFetchRetry).toHaveBeenCalledTimes(2);
        vi.useRealTimers();
    });

    it("bounds retained preview entries", async () => {
        mockedApiFetchRetry.mockImplementation(async (path) => ({
            url: `https://cdn.test${String(path)}`,
            thumbnail_type: "webp",
        }));

        for (let index = 0; index < 257; index += 1) {
            await getMaterialThumbnail(`material-${index}`);
        }
        await getMaterialThumbnail("material-0");

        expect(mockedApiFetchRetry).toHaveBeenCalledTimes(258);
    });
});

describe("recalculateMaterialThumbnail", () => {
    it("calls the recalculate endpoint and invalidates cached thumbnail", async () => {
        mockedApiFetchRetry.mockResolvedValueOnce({
            url: "https://cdn.test/old.webp",
            thumbnail_type: "webp",
        });
        mockedApiFetch.mockResolvedValueOnce({ status: "queued", material_id: "m-123" });

        // Cache a thumbnail
        await getMaterialThumbnail("m-123");
        expect(mockedApiFetchRetry).toHaveBeenCalledTimes(1);

        // Recalculate
        await recalculateMaterialThumbnail("m-123");
        expect(mockedApiFetch).toHaveBeenCalledWith("/materials/m-123/recalculate-thumbnail", {
            method: "POST",
        });

        // Requesting thumbnail again should trigger a new fetch since cache was invalidated
        mockedApiFetchRetry.mockResolvedValueOnce({
            url: "https://cdn.test/new.webp",
            thumbnail_type: "webp",
        });
        const fresh = await getMaterialThumbnail("m-123");
        expect(mockedApiFetchRetry).toHaveBeenCalledTimes(2);
        expect(fresh).toEqual({ url: "https://cdn.test/new.webp", thumbnailType: "webp" });
    });
});

