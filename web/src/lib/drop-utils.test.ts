import { describe, expect, it } from "vitest";

import { collectDroppedItems, traverseFolder } from "./drop-utils";

function fileEntry(name: string, result: File | DOMException): FileSystemFileEntry {
  return {
    name,
    fullPath: `/folder/${name}`,
    filesystem: {} as FileSystem,
    isFile: true,
    isDirectory: false,
    file(success, failure) {
      if (result instanceof DOMException) failure?.(result);
      else success(result);
    },
    getParent() {},
  } as FileSystemFileEntry;
}

function folderEntry(children: FileSystemEntry[]): FileSystemDirectoryEntry {
  let reads = 0;
  return {
    name: "folder",
    fullPath: "/folder",
    filesystem: {} as FileSystem,
    isFile: false,
    isDirectory: true,
    createReader: () => ({
      readEntries(success: FileSystemEntriesCallback) {
        success(reads++ === 0 ? children : []);
      },
    }),
    getParent() {},
  } as FileSystemDirectoryEntry;
}

describe("traverseFolder", () => {
  it("keeps readable files and reports a file that cannot be opened", async () => {
    const good = fileEntry(
      "good.pdf",
      new File(["%PDF-1.4"], "good.pdf", { type: "application/pdf" }),
    );
    const unreadable = fileEntry("locked.pdf", new DOMException("permission denied"));

    const result = await traverseFolder(folderEntry([good, unreadable]));

    expect(result.files.map((entry) => entry.file.name)).toEqual(["good.pdf"]);
    expect(result.skipped).toEqual(["folder/locked.pdf: permission denied"]);
  });
});

describe("collectDroppedItems", () => {
  it("keeps readable top-level files when another entry cannot be opened", async () => {
    const good = fileEntry(
      "good.pdf",
      new File(["%PDF-1.4"], "good.pdf", { type: "application/pdf" }),
    );
    const unreadable = fileEntry("locked.pdf", new DOMException("permission denied"));
    const items = {
      0: { kind: "file", webkitGetAsEntry: () => good, getAsFile: () => null },
      1: { kind: "file", webkitGetAsEntry: () => unreadable, getAsFile: () => null },
      length: 2,
    } as unknown as DataTransferItemList;

    const result = await collectDroppedItems(items);

    expect(result.files.map((entry) => entry.file.name)).toEqual(["good.pdf"]);
    expect(result.inaccessible).toEqual(["locked.pdf"]);
  });
});
