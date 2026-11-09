import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  uploadFile,
  logicalFileSize,
  getUploadConfig,
  beginUploadGroup,
  uploadLimitMbForMime,
} from "./upload-client";
import { compressImageIfNeeded } from "./file-utils";
import { apiRequest, ensureFreshAccessToken, ApiError } from "./api-client";
import * as tus from "tus-js-client";

vi.mock("./api-client", () => ({
  apiRequest: vi.fn(),
  ensureFreshAccessToken: vi.fn(async () => undefined),
  getClientId: vi.fn(() => "test-client-id"),
  API_BASE: "http://test-api",
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

vi.mock("./sse-client", () => ({
  subscribeToSSE: vi.fn(() => ({ close: vi.fn() })),
}));

vi.mock("./auth-tokens", () => ({
  getAccessToken: vi.fn(() => "test-token"),
}));

vi.mock("./file-utils", () => ({
  compressImageIfNeeded: vi.fn(),
}));

vi.mock("tus-js-client", () => ({
  Upload: vi.fn().mockImplementation(function (this: Record<string, unknown>, _file: File, options: Record<string, (...args: unknown[]) => void>) {
    this.url = "http://test-api/upload/tus/session";
    this.abort = vi.fn(async () => undefined);
    this.start = vi.fn(() => {
      options.onAfterResponse({}, { getHeader: () => "tus-key" });
      options.onSuccess();
    });
  }),
}));

interface FakeXhr {
  url: string;
  headers: Record<string, string>;
  body: unknown;
  aborted: boolean;
}

type XhrReply = (xhr: FakeXhr) => { status: number; body?: string; etag?: string } | "network" | "hang";

let xhrs: FakeXhr[];
let replyToXhr: XhrReply;

function installXhr() {
  xhrs = [];
  global.XMLHttpRequest = vi.fn().mockImplementation(function (this: any) {
    const record: FakeXhr = { url: "", headers: {}, body: null, aborted: false };
    xhrs.push(record);
    this.upload = {};
    this.open = vi.fn((_method: string, url: string) => {
      record.url = url;
    });
    this.setRequestHeader = vi.fn((name: string, value: string) => {
      record.headers[name] = value;
    });
    this.getResponseHeader = vi.fn(() => this.etag ?? null);
    this.abort = vi.fn(() => {
      record.aborted = true;
      this.onabort?.();
    });
    this.send = vi.fn((body: unknown) => {
      record.body = body;
      const reply = replyToXhr(record);
      if (reply === "hang") return;
      queueMicrotask(() => {
        if (reply === "network") {
          this.onerror?.();
          return;
        }
        this.status = reply.status;
        this.responseText = reply.body ?? "";
        this.etag = reply.etag;
        this.onload?.();
      });
    });
  }) as any;
}

function statusResponse(status = "clean") {
  return respondJson({
    status,
    error_code: status === "failed" ? "ERR_SCAN_UNAVAILABLE" : undefined,
    detail: status === "failed" ? "scanner down" : undefined,
    result: status === "clean"
      ? { file_key: "final-key", size: 100, original_size: 100, mime_type: "application/pdf" }
      : undefined,
  });
}

const DIRECT_OK = {
  status: 202,
  body: JSON.stringify({ upload_id: "u", file_key: "direct-key", status: "pending", size: 1, mime_type: "application/pdf" }),
};

function fileOfSize(size: number) {
  const file = new File(["x"], "doc.pdf", { type: "application/pdf" });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

const CONFIG = {
  allowed_extensions: [".pdf"],
  allowed_mimetypes: ["application/pdf"],
  max_file_size_mb: 100,
  max_size_mb_by_mime: {},
  recommended_path: "direct",
  direct_threshold_mb: 10,
};

let apiRoutes: Record<string, () => Response | Promise<Response>>;

function respondJson(value: unknown) {
  return { json: async () => value } as unknown as Response;
}

describe("beginUploadGroup", () => {
  it("requests one bounded admission for the batch", async () => {
    vi.mocked(apiRequest).mockResolvedValueOnce(respondJson({ group_id: "group-1", max_files: 275, expires_in: 172800 }));

    await expect(beginUploadGroup(275)).resolves.toEqual({
      group_id: "group-1",
      max_files: 275,
      expires_in: 172800,
    });
    expect(apiRequest).toHaveBeenCalledWith("/upload/groups", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ file_count: 275 }),
    }));
  });
});

describe("upload-client: uploadFile", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    installXhr();
    replyToXhr = () => DIRECT_OK;
    apiRoutes = {};
    vi.mocked(compressImageIfNeeded).mockImplementation(async (file) => ({ file, compressed: false }));
    vi.mocked(apiRequest).mockImplementation(async (url: unknown) => {
      const path = String(url);
      for (const [prefix, respond] of Object.entries(apiRoutes)) {
        if (path.includes(prefix)) return respond();
      }
      if (path.includes("/upload/config")) return respondJson(CONFIG);
      if (path.includes("/upload/status/")) return statusResponse();
      return respondJson({});
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("sends small files through the direct endpoint with their admission IDs", async () => {
    const result = await uploadFile(fileOfSize(1024), {
      uploadId: "982fbdc1-34ed-46df-a42a-86a9dad8ad74",
      uploadGroupId: "9652c70f-baa7-4ad9-9708-3ec8d5d9832c",
    });

    expect(xhrs).toHaveLength(1);
    expect(xhrs[0].url).toBe("http://test-api/upload");
    expect(xhrs[0].headers).toMatchObject({
      "X-Upload-ID": "982fbdc1-34ed-46df-a42a-86a9dad8ad74",
      "X-Upload-Group-ID": "9652c70f-baa7-4ad9-9708-3ec8d5d9832c",
      Authorization: "Bearer test-token",
    });
    expect(result.file_key).toBe("final-key");
    expect(ensureFreshAccessToken).toHaveBeenCalled();
  });

  it("retries a dropped direct upload with the same upload ID", async () => {
    let attempt = 0;
    replyToXhr = () => (attempt++ === 0 ? "network" : DIRECT_OK);

    await uploadFile(fileOfSize(1024), { uploadId: "11111111-1111-4111-8111-111111111111" });

    expect(xhrs.map((xhr) => xhr.headers["X-Upload-ID"])).toEqual([
      "11111111-1111-4111-8111-111111111111",
      "11111111-1111-4111-8111-111111111111",
    ]);
  }, 10_000);

  it("does not retry a rejected file and keeps its error code", async () => {
    replyToXhr = () => ({
      status: 400,
      body: JSON.stringify({ detail: "Too big", error_code: "ERR_FILE_TOO_LARGE" }),
    });

    await expect(uploadFile(fileOfSize(1024))).rejects.toMatchObject({
      status: 400,
      error_code: "ERR_FILE_TOO_LARGE",
    });
    expect(xhrs).toHaveLength(1);
  });

  it("reports the transfer before waiting for processing", async () => {
    const order: string[] = [];
    vi.mocked(apiRequest).mockImplementation(async (url: unknown) => {
      if (String(url).includes("/upload/status/")) {
        order.push("watch");
        return statusResponse();
      }
      return respondJson(CONFIG);
    });

    await uploadFile(fileOfSize(1024), { onTransferred: () => order.push("transferred") });

    expect(order).toEqual(["transferred", "watch"]);
  });

  it("carries the terminal failure code from processing", async () => {
    apiRoutes["/upload/status/"] = () => statusResponse("failed");

    await expect(uploadFile(fileOfSize(1024))).rejects.toMatchObject({
      status: 500,
      error_code: "ERR_SCAN_UNAVAILABLE",
    });
  });

  it("uses parallel multipart above the direct threshold", async () => {
    const eightMiB = 8 * 1024 * 1024;
    apiRoutes["/upload/presigned-multipart/init"] = () => respondJson({
      quarantine_key: "mp-key",
      upload_id: "mp-1",
      s3_multipart_id: "s3",
      parts: [
        { part_number: 1, size: eightMiB, url: "http://s3/1" },
        { part_number: 2, size: eightMiB, url: "http://s3/2" },
      ],
    });
    replyToXhr = (xhr) => ({ status: 200, etag: `"${xhr.url.slice(-1)}"` });
    const started = vi.fn();

    await uploadFile(fileOfSize(2 * eightMiB), { onMultipartStarted: started });

    expect(started).toHaveBeenCalledWith("mp-1");
    const complete = vi.mocked(apiRequest).mock.calls.find(([url]) => String(url).endsWith("/complete"));
    expect(JSON.parse(String(complete?.[1]?.body))).toEqual({
      upload_id: "mp-1",
      parts: [{ PartNumber: 1, ETag: '"1"' }, { PartNumber: 2, ETag: '"2"' }],
    });
  });

  it("resumes multipart by sending only the missing parts", async () => {
    const eightMiB = 8 * 1024 * 1024;
    apiRoutes["/upload/presigned-multipart/mp-1/resume"] = () => respondJson({
      quarantine_key: "mp-key",
      upload_id: "mp-1",
      s3_multipart_id: "s3",
      parts: [
        { part_number: 1, size: eightMiB, etag: '"stored"' },
        { part_number: 2, size: eightMiB, url: "http://s3/2" },
      ],
    });
    replyToXhr = () => ({ status: 200, etag: '"new"' });

    await uploadFile(fileOfSize(2 * eightMiB), { multipartUploadId: "mp-1" });

    expect(xhrs.map((xhr) => xhr.url)).toEqual(["http://s3/2"]);
    const complete = vi.mocked(apiRequest).mock.calls.find(([url]) => String(url).endsWith("/complete"));
    expect(JSON.parse(String(complete?.[1]?.body)).parts).toEqual([
      { PartNumber: 1, ETag: '"stored"' },
      { PartNumber: 2, ETag: '"new"' },
    ]);
  });

  it("starts a new session when the old one cannot be resumed", async () => {
    const eightMiB = 8 * 1024 * 1024;
    apiRoutes["/resume"] = () => {
      throw new ApiError(409, "already failed");
    };
    apiRoutes["/upload/presigned-multipart/init"] = () => respondJson({
      quarantine_key: "mp-key",
      upload_id: "mp-2",
      s3_multipart_id: "s3",
      parts: [
        { part_number: 1, size: eightMiB, url: "http://s3/1" },
        { part_number: 2, size: eightMiB, url: "http://s3/2" },
      ],
    });
    replyToXhr = () => ({ status: 200, etag: '"x"' });
    const started = vi.fn();

    await uploadFile(fileOfSize(2 * eightMiB), { multipartUploadId: "mp-1", onMultipartStarted: started });

    expect(started).toHaveBeenCalledWith("mp-2");
  });

  it("cancels sibling parts when one part fails, and keeps the session", async () => {
    const eightMiB = 8 * 1024 * 1024;
    apiRoutes["/upload/presigned-multipart/init"] = () => respondJson({
      quarantine_key: "mp-key",
      upload_id: "mp-1",
      s3_multipart_id: "s3",
      parts: [
        { part_number: 1, size: eightMiB, url: "http://s3/fail" },
        { part_number: 2, size: eightMiB, url: "http://s3/hang" },
      ],
    });
    replyToXhr = (xhr) => (xhr.url.endsWith("fail") ? { status: 403 } : "hang");

    await expect(uploadFile(fileOfSize(2 * eightMiB))).rejects.toMatchObject({ status: 403 });

    expect(xhrs.find((xhr) => xhr.url.endsWith("hang"))?.aborted).toBe(true);
    const calls = vi.mocked(apiRequest).mock.calls;
    expect(calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
  });

  it("falls back to tus when multipart is disabled", async () => {
    apiRoutes["/upload/presigned-multipart/init"] = () => {
      throw new ApiError(501, "disabled");
    };

    await uploadFile(fileOfSize(16 * 1024 * 1024));

    expect(tus.Upload).toHaveBeenCalledOnce();
    const options = vi.mocked(tus.Upload).mock.calls[0][1]!;
    const retry = (status: number) =>
      options.onShouldRetry!({ originalResponse: { getStatus: () => status } } as never, 0, options as never);
    expect(retry(429)).toBe(true);
    expect(retry(409)).toBe(true);
    expect(retry(400)).toBe(false);
  });

  it("does not resume a stored session with recompressed bytes", async () => {
    const compressed = fileOfSize(1024);
    vi.mocked(compressImageIfNeeded).mockResolvedValueOnce({ file: compressed, compressed: true });
    const image = new File(["x"], "photo.png", { type: "image/png" });

    const result = await uploadFile(image, { tusUrl: "http://test-api/upload/tus/old" });

    expect(tus.Upload).not.toHaveBeenCalled();
    expect(xhrs).toHaveLength(1);
    expect(result.wasCompressed).toBe(true);
  });

  it("stops without reporting a failure when aborted", async () => {
    replyToXhr = () => "hang";
    const controller = new AbortController();

    const pending = uploadFile(fileOfSize(1024), { signal: controller.signal });
    await vi.waitFor(() => expect(xhrs).toHaveLength(1));
    controller.abort();

    await expect(pending).rejects.toThrow("Upload cancelled");
    expect(xhrs).toHaveLength(1);
  });

  // Keep last: the first blocked bucket disables multipart for the rest of the page.
  it("falls back to tus when bucket CORS hides the ETag", async () => {
    const eightMiB = 8 * 1024 * 1024;
    apiRoutes["/upload/presigned-multipart/init"] = () => respondJson({
      quarantine_key: "mp-key",
      upload_id: "mp-1",
      s3_multipart_id: "s3",
      parts: [
        { part_number: 1, size: eightMiB, url: "http://s3/1" },
        { part_number: 2, size: eightMiB, url: "http://s3/2" },
      ],
    });
    replyToXhr = () => ({ status: 200 });

    await uploadFile(fileOfSize(2 * eightMiB));

    expect(tus.Upload).toHaveBeenCalledOnce();
    expect(apiRequest).toHaveBeenCalledWith(
      "/upload/presigned-multipart/mp-1",
      expect.objectContaining({ method: "DELETE" }),
    );

    vi.mocked(apiRequest).mockClear();
    await uploadFile(fileOfSize(2 * eightMiB));
    const calls = vi.mocked(apiRequest).mock.calls.map(([url]) => String(url));
    expect(calls.some((url) => url.includes("presigned-multipart"))).toBe(false);
  });
});

describe("logicalFileSize", () => {
  it("returns size when content_encoding is null", () => {
    expect(logicalFileSize({ size: 500, original_size: 1000, content_encoding: null })).toBe(500);
  });

  it("returns original_size when content_encoding is gzip", () => {
    expect(logicalFileSize({ size: 300, original_size: 1000, content_encoding: "gzip" })).toBe(1000);
  });

  it("returns size for unknown encodings (not gzip)", () => {
    expect(logicalFileSize({ size: 400, original_size: 800, content_encoding: "br" })).toBe(400);
  });
});

describe("getUploadConfig", () => {
  const mockConfig = {
    allowed_extensions: [".pdf", ".png"],
    allowed_mimetypes: ["application/pdf", "image/png"],
    max_file_size_mb: 50,
    max_size_mb_by_mime: { "application/pdf": 200, "image/png": 25 },
    recommended_path: "direct" as const,
    direct_threshold_mb: 10,
  };

  it("fetches once, caches within TTL, and re-fetches after expiry", async () => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.advanceTimersByTime(10 * 60 * 1000);
    vi.mocked(apiRequest).mockResolvedValue(respondJson(mockConfig));

    const result = await getUploadConfig();
    expect(result).toEqual(mockConfig);
    const callsAfterFirst = vi.mocked(apiRequest).mock.calls.length;

    await getUploadConfig();
    expect(vi.mocked(apiRequest).mock.calls.length).toBe(callsAfterFirst);

    vi.advanceTimersByTime(6 * 60 * 1000);
    await getUploadConfig();
    expect(vi.mocked(apiRequest).mock.calls.length).toBe(callsAfterFirst + 1);

    vi.useRealTimers();
  });

  it("uses MIME-specific limits and retains the global fallback", () => {
    expect(uploadLimitMbForMime(mockConfig, "application/pdf", 100)).toBe(200);
    expect(uploadLimitMbForMime(mockConfig, "image/png", 100)).toBe(25);
    expect(uploadLimitMbForMime(mockConfig, "application/octet-stream", 100)).toBe(50);
    expect(uploadLimitMbForMime(null, "application/pdf", 100)).toBe(100);
  });
});
