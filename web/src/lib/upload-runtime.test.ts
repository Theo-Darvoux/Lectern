import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiRequest } from "@/lib/api-client";
import { trackExistingUpload, uploadFile } from "@/lib/upload-client";
import { useUploadQueue, type QueueItem } from "@/lib/upload-queue";
import {
  attachUploadFile,
  pauseUpload,
  removeUpload,
  restartUpload,
  resumeProcessingAfterReload,
  scheduleUpload,
} from "@/lib/upload-runtime";

vi.mock("@/lib/api-client", () => ({
  apiRequest: vi.fn(async () => ({})),
  ApiError: class extends Error {
    status: number;
    error_code?: string;
    constructor(status: number, message: string, error_code?: string) {
      super(message);
      this.status = status;
      this.error_code = error_code;
    }
  },
}));

vi.mock("@/lib/upload-client", () => ({
  uploadFile: vi.fn(),
  trackExistingUpload: vi.fn(),
  logicalFileSize: (result: { size: number }) => result.size,
}));

const t = (key: string) => key;

const RESULT = {
  file_key: "cas/a",
  size: 10,
  original_size: 10,
  mime_type: "application/pdf",
  content_encoding: null,
  correctedName: "Doc.pdf",
  wasCompressed: false,
};

type Options = NonNullable<Parameters<typeof uploadFile>[1]>;

interface Running {
  options: Options;
  finish: () => void;
}

let running: Map<string, Running>;

function row(clientId: string, patch: Partial<QueueItem> = {}): QueueItem {
  return {
    clientId,
    uploadId: `upload-${clientId}`,
    fileName: "doc.pdf",
    fileSize: 1,
    fileMimeType: "application/pdf",
    title: "Doc",
    status: "pending",
    progress: 0,
    processingStatus: "",
    targetDirPath: "",
    ...patch,
  };
}

function enqueue(ids: string[]) {
  useUploadQueue.getState().addItems(ids.map((id) => row(id)));
  for (const id of ids) {
    attachUploadFile(id, new File(["x"], "doc.pdf", { type: "application/pdf" }));
    scheduleUpload(id, t);
  }
}

const statusOf = (id: string) => useUploadQueue.getState().items.find((i) => i.clientId === id)?.status;
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("upload runtime", () => {
  beforeEach(async () => {
    useUploadQueue.getState().addItems([row("reset", { status: "done" })]);
    useUploadQueue.getState().clearAll();
    await flush();
    vi.clearAllMocks();
    running = new Map();
    vi.mocked(uploadFile).mockImplementation((_file, options = {}) => {
      return new Promise((resolve, reject) => {
        const id = options.uploadId!.replace("upload-", "");
        running.set(id, { options, finish: () => resolve(RESULT) });
        options.signal?.addEventListener("abort", () => reject(new Error("Upload cancelled")));
      });
    });
  });

  it("runs at most four transfers", () => {
    enqueue(["a", "b", "c", "d", "e"]);
    expect([...running.keys()]).toEqual(["a", "b", "c", "d"]);
    expect(statusOf("e")).toBe("pending");
  });

  it("does not strand later files when queued files are removed", async () => {
    enqueue(["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]);
    for (const id of ["e", "f", "g", "h"]) removeUpload(id);

    for (const id of ["a", "b", "c", "d"]) running.get(id)!.finish();
    await flush();

    expect(running.has("i")).toBe(true);
    expect(running.has("j")).toBe(true);
  });

  it("starts the next file once bytes are stored, while processing continues", () => {
    enqueue(["a", "b", "c", "d", "e"]);

    running.get("a")!.options.onTransferred!("quarantine/a");

    expect(running.has("e")).toBe(true);
    expect(useUploadQueue.getState().items[0].quarantineKey).toBe("quarantine/a");
  });

  it("frees exactly one slot when a paused file ends", async () => {
    enqueue(["a", "b", "c", "d", "e", "f"]);

    pauseUpload("a");
    await flush();

    expect(statusOf("a")).toBe("paused");
    expect(running.has("e")).toBe(true);
    expect(running.has("f")).toBe(false);
  });

  it("resumes a paused file through the normal queue with its session", async () => {
    enqueue(["a"]);
    useUploadQueue.getState().updateItem("a", { tusUrl: "http://t/1" });
    pauseUpload("a");
    await flush();
    running.clear();

    scheduleUpload("a", t);

    expect(running.get("a")?.options.tusUrl).toBe("http://t/1");
    expect(statusOf("a")).toBe("uploading");
  });

  it("does not delete the server session when pausing", async () => {
    enqueue(["a"]);
    useUploadQueue.getState().updateItem("a", { multipartUploadId: "mp-1" });
    pauseUpload("a");
    await flush();
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it.each([
    [{ tusUrl: "http://t/1" }, "http://t/1"],
    [{ multipartUploadId: "mp-1" }, "/upload/presigned-multipart/mp-1"],
    [{}, "/upload/upload-a"],
  ])("cancels the server resources of a removed row %#", async (patch, url) => {
    enqueue(["a"]);
    useUploadQueue.getState().updateItem("a", patch);

    removeUpload("a");
    await flush();

    expect(apiRequest).toHaveBeenCalledWith(url, expect.objectContaining({ method: "DELETE" }));
  });

  it("does not contact the server for a row that never started", () => {
    enqueue(["a", "b", "c", "d", "e"]);
    removeUpload("e");
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it("retries a failed row as a new attempt without its old admission", () => {
    useUploadQueue.getState().addItems([
      row("a", { status: "error", uploadId: "upload-a", uploadGroupId: "g", quarantineKey: "q", tusUrl: "http://t/1" }),
    ]);
    attachUploadFile("a", new File(["x"], "doc.pdf"));

    restartUpload("a", t);

    const item = useUploadQueue.getState().items[0];
    expect(item.uploadId).not.toBe("upload-a");
    expect(item.uploadGroupId).toBeUndefined();
    expect(item.quarantineKey).toBeUndefined();
    expect(item.tusUrl).toBe("http://t/1");
  });

  it("marks a failed transfer with a translated message", async () => {
    vi.mocked(uploadFile).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    enqueue(["a"]);
    await flush();

    expect(useUploadQueue.getState().items[0]).toMatchObject({
      status: "error",
      error: "errors.network",
    });
  });

  it("reconnects to processing after a reload without the file", async () => {
    vi.mocked(trackExistingUpload).mockResolvedValueOnce(RESULT);
    useUploadQueue.getState().addItems([row("a", { status: "uploading", quarantineKey: "quarantine/a" })]);

    resumeProcessingAfterReload(t);
    await flush();

    expect(trackExistingUpload).toHaveBeenCalledWith("quarantine/a", expect.anything());
    expect(statusOf("a")).toBe("done");
  });
});
