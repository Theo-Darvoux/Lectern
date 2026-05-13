import { describe, it, expect, vi, beforeEach } from "vitest";
import { uploadFile } from "./upload-client";
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
  }, 10000); // Increased timeout
});
