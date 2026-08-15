import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { safeLocalStorage } from "./safe-storage";
import { useUploadQueue, type QueueItem } from "./upload-queue";
import {
    clearAllUploadTelemetry,
    mergeUploadTelemetry,
    updateUploadTelemetry,
    useUploadTelemetry,
} from "./upload-telemetry";

const item: QueueItem = {
    clientId: "upload-1",
    uploadId: "server-upload-1",
    fileName: "notes.pdf",
    fileSize: 42,
    fileMimeType: "application/pdf",
    title: "Notes",
    status: "uploading",
    progress: 0,
    processingStatus: "",
    targetDirPath: "",
};

describe("upload telemetry", () => {
    beforeEach(() => {
        vi.useFakeTimers();
        useUploadQueue.setState({ items: [item], activeCount: 0 });
        clearAllUploadTelemetry();
    });

    afterEach(() => {
        clearAllUploadTelemetry();
        useUploadQueue.setState({ items: [], activeCount: 0 });
        vi.restoreAllMocks();
        vi.useRealTimers();
    });

    it("coalesces rapid progress and processing updates into one display frame", () => {
        const listener = vi.fn();
        const unsubscribe = useUploadTelemetry.subscribe(listener);

        updateUploadTelemetry(item.clientId, { progress: 12 });
        updateUploadTelemetry(item.clientId, { progress: 37 });
        updateUploadTelemetry(item.clientId, {
            processingStatus: "Scanning",
            stageIndex: 1,
            stageTotal: 3,
        });

        expect(listener).not.toHaveBeenCalled();
        vi.advanceTimersByTime(16);

        expect(listener).toHaveBeenCalledOnce();
        expect(useUploadTelemetry.getState().byId[item.clientId]).toEqual({
            progress: 37,
            processingStatus: "Scanning",
            stageIndex: 1,
            stageTotal: 3,
        });
        unsubscribe();
    });

    it("keeps telemetry updates out of durable storage", () => {
        const persistSpy = vi.spyOn(safeLocalStorage, "setItem");

        updateUploadTelemetry(item.clientId, { progress: 50 });
        vi.advanceTimersByTime(16);

        expect(persistSpy).not.toHaveBeenCalled();

        useUploadQueue.getState().updateItem(item.clientId, { status: "paused" });
        expect(persistSpy).toHaveBeenCalledOnce();
    });

    it("persists resumability without stale display telemetry", () => {
        const persistSpy = vi.spyOn(safeLocalStorage, "setItem");

        useUploadQueue.getState().updateItem(item.clientId, {
            tusUrl: "https://uploads.test/resume/1",
            progress: 74,
            processingStatus: "Virus scan",
            stageIndex: 2,
            stageTotal: 4,
        });

        const [, serialized] = persistSpy.mock.calls.at(-1)!;
        const persisted = JSON.parse(serialized).state.items[0];
        expect(persisted).toMatchObject({
            clientId: item.clientId,
            tusUrl: "https://uploads.test/resume/1",
            progress: 0,
            processingStatus: "",
        });
        expect(persisted).not.toHaveProperty("stageIndex");
        expect(persisted).not.toHaveProperty("stageTotal");
    });

    it("overlays volatile telemetry without mutating the durable queue item", () => {
        updateUploadTelemetry(item.clientId, {
            progress: 82,
            processingStatus: "Indexing",
        });
        vi.advanceTimersByTime(16);

        const [displayItem] = mergeUploadTelemetry(
            useUploadQueue.getState().items,
            useUploadTelemetry.getState().byId,
        );

        expect(displayItem).toMatchObject({ progress: 82, processingStatus: "Indexing" });
        expect(useUploadQueue.getState().items[0]).toMatchObject({
            progress: 0,
            processingStatus: "",
        });
    });
});
