import { describe, expect, it, vi } from "vitest";

vi.mock("./file-utils", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./file-utils")>()),
  sniffFileType: vi.fn(async (file: File) => {
    if (file.name === "locked") throw new DOMException("permission denied");
    return null;
  }),
}));

import { prepareScannedFiles } from "./upload-preflight";

describe("prepareScannedFiles", () => {
  it("keeps readable siblings when MIME sniffing cannot read one file", async () => {
    const good = new File(["%PDF-1.4"], "good.pdf", { type: "application/pdf" });
    const locked = new File(["unknown"], "locked", { type: "application/octet-stream" });

    const result = await prepareScannedFiles([
      { file: good, relativePath: "folder/good.pdf" },
      { file: locked, relativePath: "folder/locked" },
    ]);

    expect(result.files.map((item) => item.relativePath)).toEqual(["folder/good.pdf"]);
    expect(result.unreadable).toEqual(["folder/locked"]);
  });
});
