import { beforeEach, describe, expect, it } from "vitest";
import { dirKey, useUploadQueue, type QueueItem } from "./upload-queue";

const base: QueueItem = {
  clientId: "a",
  uploadId: "u",
  fileName: "n.pdf",
  fileSize: 1,
  fileMimeType: "application/pdf",
  title: "N",
  status: "uploading",
  progress: 40,
  processingStatus: "x",
  targetDirPath: "",
};

async function reloadWith(items: QueueItem[]) {
  sessionStorage.setItem(
    "lectern_upload_queue",
    JSON.stringify({ state: { items, dirTempIds: { k: "dir-1" }, stagedDirKeys: [] }, version: 0 }),
  );
  await useUploadQueue.persist.rehydrate();
  return useUploadQueue.getState();
}

describe("queue restore after reload", () => {
  beforeEach(() => {
    useUploadQueue.getState().clearAll();
    sessionStorage.clear();
  });

  it("flags interrupted direct uploads so re-dropping recovers them", async () => {
    const [item] = (await reloadWith([base])).items;
    expect(item).toMatchObject({ status: "error", referenceLost: true, progress: 0 });
  });

  it("keeps resumable transfers paused", async () => {
    const state = await reloadWith([
      { ...base, clientId: "t", tusUrl: "http://t" },
      { ...base, clientId: "m", multipartUploadId: "m" },
    ]);
    expect(state.items.map((i) => i.status)).toEqual(["paused", "paused"]);
    expect(state.items.every((i) => i.referenceLost)).toBe(true);
  });

  it("keeps watching files whose bytes were already stored", async () => {
    const [item] = (await reloadWith([{ ...base, quarantineKey: "q" }])).items;
    expect(item).toMatchObject({ status: "uploading", quarantineKey: "q" });
    expect(item.referenceLost).toBeUndefined();
  });

  it("leaves finished rows alone and drops folder scans in progress", async () => {
    const state = await reloadWith([
      { ...base, clientId: "done", status: "done" },
      { ...base, clientId: "virus", status: "virus" },
      { ...base, clientId: "scan", folderName: "F" },
    ]);
    expect(state.items.map((i) => i.clientId)).toEqual(["done", "virus"]);
    expect(state.items[0].referenceLost).toBeUndefined();
  });

  it("restores the temp ids of folders still to create", async () => {
    expect((await reloadWith([base])).dirTempIds).toEqual({ k: "dir-1" });
  });
});

describe("folder bookkeeping", () => {
  beforeEach(() => useUploadQueue.getState().clearAll());

  it("scopes folders by destination", () => {
    expect(dirKey({ directoryId: "a" }, "x")).not.toBe(dirKey({ directoryId: "b" }, "x"));
  });

  it("renames a folder, its descendants and its temp ids", () => {
    const state = useUploadQueue.getState();
    state.addItems([
      { ...base, clientId: "1", targetDirPath: "Old" },
      { ...base, clientId: "2", targetDirPath: "Old/sub" },
      { ...base, clientId: "3", targetDirPath: "Older" },
    ]);
    state.setDirTempIds({ "||Old": "d1", "||Old/sub": "d2", "||Older": "d3" });

    state.renameDirectory("Old", "New");

    const after = useUploadQueue.getState();
    expect(after.items.map((i) => i.targetDirPath)).toEqual(["New", "New/sub", "Older"]);
    expect(after.dirTempIds).toEqual({ "||New": "d1", "||New/sub": "d2", "||Older": "d3" });
  });

  it("forgets folder ids once the last row leaves", () => {
    const state = useUploadQueue.getState();
    state.addItems([base]);
    state.setDirTempIds({ k: "d" });
    state.removeItem(base.clientId);
    expect(useUploadQueue.getState().dirTempIds).toEqual({});
  });
});
