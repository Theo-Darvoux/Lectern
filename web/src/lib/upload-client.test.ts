import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  uploadFile,
  uploadBatchZip,
  logicalFileSize,
  getUploadConfig,
  beginUploadGroup,
  uploadLimitMbForMime,
} from "./upload-client";
import { sha256File } from "./crypto-utils";
import { compressImageIfNeeded } from "./file-utils";
import { apiRequest } from "./api-client";

// Mock dependencies
vi.mock("./api-client", () => ({
  apiRequest: vi.fn(),
  getClientId: vi.fn(() => "test-client-id"),
  API_BASE: "http://test-api",
  ApiError: class extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

vi.mock("./auth-tokens", () => ({
  getAccessToken: vi.fn(() => "test-token"),
}));

vi.mock("./crypto-utils", () => ({
  sha256File: vi.fn(),
}));

vi.mock("./file-utils", () => ({
  compressImageIfNeeded: vi.fn(),
  formatFileSize: vi.fn(),
  getFileExtension: vi.fn(),
  getViewerType: vi.fn(),
}));

// Mock tus-js-client
vi.mock("tus-js-client", () => ({
  Upload: vi.fn().mockImplementation(() => ({
    start: vi.fn(),
  })),
}));

describe("beginUploadGroup", () => {
  it("requests one bounded admission for the folder", async () => {
    vi.mocked(apiRequest).mockResolvedValueOnce({
      json: async () => ({ group_id: "group-1", max_files: 275, expires_in: 172800 }),
    } as Response);

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

// Mock XMLHttpRequest
global.XMLHttpRequest = vi.fn().mockImplementation(function(this: any) {
  this.upload = {};
  this.open = vi.fn();
  this.setRequestHeader = vi.fn();
  this.send = vi.fn().mockImplementation(() => {
    setTimeout(() => {
      this.status = 200;
      if (this.onload) this.onload();
    }, 0);
  });
}) as any;

describe("upload-client: uploadFile", () => {
  const mockFile = new File(["test content"], "test.png", { type: "image/png" });

  beforeEach(() => {
    vi.clearAllMocks();
    
    // Robust mock implementation for apiRequest
    vi.mocked(apiRequest).mockImplementation(async (url: any) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      
      if (urlStr.includes("/upload/check-exists")) {
        return { json: async () => ({ exists: false }) } as any;
      }
      if (urlStr.includes("/upload/config")) {
        return {
          json: async () => ({
            allowed_extensions: [".png", ".svg", ".pdf"],
            allowed_mimetypes: ["image/png", "image/svg+xml", "application/pdf"],
            max_file_size_mb: 100,
            max_size_mb_by_mime: {},
            recommended_path: "direct",
            direct_threshold_mb: 10,
          }),
        } as any;
      }
      if (urlStr === "/upload") {
        return {
          json: async () => ({
            upload_id: "u-id",
            file_key: "q-key",
            status: "pending",
            size: mockFile.size,
            mime_type: mockFile.type,
          }),
        } as any;
      }
      if (urlStr.includes("/upload/init")) {
        return { json: async () => ({ 
          quarantine_key: "q-key", 
          upload_id: "u-id", 
          presigned_url: "p-url" 
        }) } as any;
      }
      if (urlStr.includes("/upload/complete")) {
        return { json: async () => ({ status: "pending" }) } as any;
      }
      if (urlStr.includes("/upload/events/")) {
        const encoder = new TextEncoder();
        const data = JSON.stringify({
          status: "clean",
          result: {
            file_key: "final-key",
            size: 100,
            original_size: 100,
            mime_type: "image/png",
          }
        });
        const stream = new ReadableStream({
          start(controller) {
            controller.enqueue(encoder.encode(`data: ${data}\n\n`));
            controller.close();
          }
        });
        return { body: stream } as any;
      }
      return { json: async () => ({}) } as any;
    });
  });

  it("re-calculates hash if file was compressed", async () => {
    vi.mocked(sha256File).mockResolvedValueOnce("original-hash");
    
    const compressedFile = new File(["compressed content"], "test.png", { type: "image/png" });
    vi.mocked(compressImageIfNeeded).mockResolvedValueOnce({
      file: compressedFile,
      compressed: true,
    });

    vi.mocked(sha256File).mockResolvedValueOnce("compressed-hash");

    const result = await uploadFile(mockFile);

    expect(sha256File).toHaveBeenCalledTimes(2);
    expect(sha256File).toHaveBeenNthCalledWith(1, mockFile, expect.any(Function), undefined);
    expect(sha256File).toHaveBeenNthCalledWith(2, compressedFile, undefined, undefined);
    
    expect(result.content_sha256).toBe("compressed-hash");
    expect(result.file_key).toBe("final-key");
  }, 10000); // Increased timeout

  it("does not re-calculate hash if file was not compressed", async () => {
    vi.mocked(sha256File).mockResolvedValueOnce("original-hash");

    vi.mocked(compressImageIfNeeded).mockResolvedValueOnce({
      file: mockFile,
      compressed: false,
    });

    const result = await uploadFile(mockFile);

    expect(sha256File).toHaveBeenCalledTimes(1);
    expect(sha256File).toHaveBeenCalledWith(mockFile, expect.any(Function), undefined);
    expect(result.content_sha256).toBe("original-hash");
  }, 10000);

  it("uses the backend-recommended direct endpoint for small files", async () => {
    vi.mocked(sha256File).mockResolvedValueOnce("original-hash");
    vi.mocked(compressImageIfNeeded).mockResolvedValueOnce({
      file: mockFile,
      compressed: false,
    });

    await uploadFile(mockFile);

    expect(apiRequest).toHaveBeenCalledWith(
      "/upload",
      expect.objectContaining({
        method: "POST",
        body: expect.any(FormData),
        timeoutMs: 120_000,
      }),
    );
    const calls = vi.mocked(apiRequest).mock.calls.map(([u]) => String(u));
    expect(calls.some((u) => u.includes("/upload/init"))).toBe(false);
    expect(calls.some((u) => u.includes("/upload/complete"))).toBe(false);
  }, 10000);

  it("sends the stable file and folder admission IDs to the upload endpoint", async () => {
    vi.mocked(sha256File).mockResolvedValueOnce("original-hash");
    vi.mocked(compressImageIfNeeded).mockResolvedValueOnce({
      file: mockFile,
      compressed: false,
    });

    await uploadFile(mockFile, {
      uploadId: "982fbdc1-34ed-46df-a42a-86a9dad8ad74",
      uploadGroupId: "9652c70f-baa7-4ad9-9708-3ec8d5d9832c",
    });

    expect(apiRequest).toHaveBeenCalledWith(
      "/upload",
      expect.objectContaining({
        headers: expect.objectContaining({
          "X-Upload-ID": "982fbdc1-34ed-46df-a42a-86a9dad8ad74",
          "X-Upload-Group-ID": "9652c70f-baa7-4ad9-9708-3ec8d5d9832c",
        }),
      }),
    );
  }, 10000);

  it("skips upload and returns result when dedup check says file exists", async () => {
    vi.mocked(sha256File).mockResolvedValueOnce("known-hash");

    vi.mocked(apiRequest).mockImplementation(async (url: any) => {
      const urlStr = String(url);
      if (urlStr.includes("/upload/check-exists")) {
        return { json: async () => ({ exists: true, file_key: "cached-key" }) } as any;
      }
      if (urlStr.includes("/upload/status/batch")) {
        return {
          json: async () => ({
            statuses: {
              "cached-key": {
                status: "clean",
                file_key: "cached-key",
                result: {
                  file_key: "cached-key",
                  size: 100,
                  original_size: 100,
                  mime_type: "image/png",
                  content_encoding: null,
                },
              },
            },
          }),
        } as any;
      }
      return { json: async () => ({}) } as any;
    });

    // forcePipeline: false is required to enable the dedup check path
    const result = await uploadFile(mockFile, { forcePipeline: false });

    const calls = vi.mocked(apiRequest).mock.calls.map(([u]) => String(u));
    expect(calls.some((u) => u.includes("/upload/init"))).toBe(false);
    expect(result.file_key).toBe("cached-key");
  }, 10000);
});

// ── logicalFileSize ──────────────────────────────────────────────────────────

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

  it("handles identical size and original_size", () => {
    expect(logicalFileSize({ size: 200, original_size: 200, content_encoding: null })).toBe(200);
  });
});

// ── getUploadConfig ──────────────────────────────────────────────────────────

describe("getUploadConfig", () => {
  const mockConfig = {
    allowed_extensions: [".pdf", ".png"],
    allowed_mimetypes: ["application/pdf", "image/png"],
    max_file_size_mb: 50,
    max_size_mb_by_mime: { "application/pdf": 200, "image/png": 25 },
    recommended_path: "direct" as const,
    direct_threshold_mb: 10,
    batch_max_zip_size_bytes: 500 * 1024 * 1024,
    batch_max_total_extracted_bytes: 2 * 1024 ** 3,
    batch_max_files: 200,
    batch_max_files_privileged: 2_000,
    batch_max_path_depth: 20,
  };

  // Run all three behaviours in a single test so module-level cache state
  // flows naturally from one assertion to the next without ordering issues.
  it("fetches once, caches within TTL, and re-fetches after expiry", async () => {
    vi.useFakeTimers();
    vi.clearAllMocks();

    // Advance past any cache left by previous tests so the first call fetches.
    vi.advanceTimersByTime(10 * 60 * 1000);

    vi.mocked(apiRequest).mockResolvedValue({
      json: async () => mockConfig,
    } as any);

    // First call: fetches from the network.
    const result = await getUploadConfig();
    expect(result).toEqual(mockConfig);
    const callsAfterFirst = vi.mocked(apiRequest).mock.calls.length;
    expect(callsAfterFirst).toBeGreaterThanOrEqual(1);

    // Second call immediately: uses the cache (no extra network call).
    await getUploadConfig();
    expect(vi.mocked(apiRequest).mock.calls.length).toBe(callsAfterFirst);

    // Advance past the 5-minute TTL.
    vi.advanceTimersByTime(6 * 60 * 1000);

    // Third call: cache expired, fetches again.
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

describe("uploadBatchZip", () => {
  it("retries a network failure with the same batch id", async () => {
    const originalXhr = global.XMLHttpRequest;
    const requestHeaders: Array<Record<string, string>> = [];
    let attempt = 0;

    global.XMLHttpRequest = vi.fn().mockImplementation(function(this: any) {
      this.upload = {};
      const headers: Record<string, string> = {};
      requestHeaders.push(headers);
      this.open = vi.fn();
      this.setRequestHeader = vi.fn((key: string, value: string) => {
        headers[key] = value;
      });
      this.abort = vi.fn();
      this.send = vi.fn(() => {
        if (attempt++ === 0) {
          queueMicrotask(() => this.onerror?.());
        } else {
          queueMicrotask(() => {
            this.status = 202;
            this.responseText = JSON.stringify({
              batch_id: "11111111-1111-4111-8111-111111111111",
              files: [],
              skipped: 0,
              errors: [],
            });
            this.onload?.();
          });
        }
      });
    }) as any;

    try {
      const result = await uploadBatchZip(new Blob(["PK\u0003\u0004"]), {
        uploadId: "11111111-1111-4111-8111-111111111111",
      });

      expect(result.batch_id).toBe("11111111-1111-4111-8111-111111111111");
      expect(requestHeaders).toHaveLength(2);
      expect(requestHeaders.map((headers) => headers["X-Upload-ID"])).toEqual([
        "11111111-1111-4111-8111-111111111111",
        "11111111-1111-4111-8111-111111111111",
      ]);
    } finally {
      global.XMLHttpRequest = originalXhr;
    }
  });

  it("rejects an archive larger than the advertised server limit before transfer", async () => {
    const archive = new Blob();
    Object.defineProperty(archive, "size", { value: 500 * 1024 * 1024 + 1 });

    await expect(uploadBatchZip(archive)).rejects.toMatchObject({ status: 413 });
  });
});
