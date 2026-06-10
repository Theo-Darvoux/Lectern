import { describe, it, expect, vi, beforeEach } from "vitest";
import { apiRequest, apiFetchRetry, isRetriableError, getClientId, ApiError } from "./api-client";
import { getAccessToken, setAccessToken, clearAccessToken } from "./auth-tokens";

// Mock auth-tokens
vi.mock("./auth-tokens", () => ({
  getAccessToken: vi.fn(),
  setAccessToken: vi.fn(),
  clearAccessToken: vi.fn(),
  decodeToken: vi.fn(() => ({ exp: Date.now() / 1000 + 3600 })),
}));

// Mock fetch
global.fetch = vi.fn();

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

describe("api-client", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  describe("getClientId", () => {
    it("generates and persists a client id", () => {
      const id1 = getClientId();
      expect(id1).toBeDefined();
      expect(localStorage.getItem("lectern_client_id")).toBe(id1);
      
      const id2 = getClientId();
      expect(id2).toBe(id1);
    });
  });

  describe("apiRequest", () => {
    it("adds Authorization header if token exists", async () => {
      vi.mocked(getAccessToken).mockReturnValue("test-token");
      vi.mocked(fetch).mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({}),
      } as Response);

      await apiRequest("/test");

      const [url, options] = vi.mocked(fetch).mock.calls[0];
      expect(url).toContain("/test");
      const headers = options?.headers as Headers;
      expect(headers.get("Authorization")).toBe("Bearer test-token");
      expect(headers.get("X-Client-ID")).toBeDefined();
    });

    it("handles 401 and refreshes token", async () => {
      vi.mocked(getAccessToken).mockReturnValue("old-token");
      
      // 1. Initial 401
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: false,
        status: 401,
      } as Response);

      // 2. Refresh call
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ access_token: "new-token" }),
      } as Response);

      // 3. Retry success
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ success: true }),
      } as Response);

      const res = await apiRequest("/test");
      const data = await res.json();

      expect(data.success).toBe(true);
      expect(setAccessToken).toHaveBeenCalledWith("new-token");
      expect(fetch).toHaveBeenCalledTimes(3);
      
      // Verify retry used the new token
      const lastCallHeaders = vi.mocked(fetch).mock.calls[2][1]?.headers as Headers;
      expect(lastCallHeaders.get("Authorization")).toBe("Bearer new-token");
    });

    it("handles 401 and fails on transient refresh errors without clearing session", async () => {
      vi.mocked(getAccessToken).mockReturnValue("old-token");

      // 1. Initial 401
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: false,
        status: 401,
      } as Response);

      // 2. Refresh call returns 502 Bad Gateway
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: false,
        status: 502,
        statusText: "Bad Gateway",
      } as Response);

      await expect(apiRequest("/test")).rejects.toThrow(ApiError);
      expect(clearAccessToken).not.toHaveBeenCalled();
    });

    it("handles 401 and clears session on permanent refresh errors", async () => {
      vi.mocked(getAccessToken).mockReturnValue("old-token");

      // 1. Initial 401
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: false,
        status: 401,
      } as Response);

      // 2. Refresh call returns 400 Bad Request
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: false,
        status: 400,
        statusText: "Bad Request",
      } as Response);

      await expect(apiRequest("/test")).rejects.toThrow(ApiError);
      expect(clearAccessToken).toHaveBeenCalled();
    });

    it("throws ApiError on failure", async () => {
      vi.mocked(fetch).mockResolvedValue({
        ok: false,
        status: 400,
        json: async () => ({ detail: "Bad Request" }),
      } as Response);

      await expect(apiRequest("/test")).rejects.toThrow(ApiError);
    });

    it("passes an AbortSignal to fetch when timeoutMs is set", async () => {
      vi.mocked(fetch).mockResolvedValue(jsonResponse(200, {}));

      await apiRequest("/test", { timeoutMs: 5000 });

      const opts = vi.mocked(fetch).mock.calls[0][1];
      expect(opts?.signal).toBeInstanceOf(AbortSignal);
    });

    it("does not attach a signal when neither timeoutMs nor signal is provided", async () => {
      vi.mocked(fetch).mockResolvedValue(jsonResponse(200, {}));

      await apiRequest("/test");

      const opts = vi.mocked(fetch).mock.calls[0][1];
      expect(opts?.signal).toBeUndefined();
    });
  });

  describe("isRetriableError", () => {
    it("treats fetch network failures (TypeError) as retriable", () => {
      expect(isRetriableError(new TypeError("Failed to fetch"))).toBe(true);
    });

    it("treats timeouts and aborts as retriable", () => {
      expect(isRetriableError(new DOMException("timed out", "TimeoutError"))).toBe(true);
      expect(isRetriableError(new DOMException("aborted", "AbortError"))).toBe(true);
    });

    it("treats 5xx and connection (0) errors as retriable", () => {
      expect(isRetriableError(new ApiError(500, "boom"))).toBe(true);
      expect(isRetriableError(new ApiError(503, "down"))).toBe(true);
      expect(isRetriableError(new ApiError(0, "net"))).toBe(true);
    });

    it("does not retry deterministic 4xx errors", () => {
      expect(isRetriableError(new ApiError(400, "bad"))).toBe(false);
      expect(isRetriableError(new ApiError(404, "missing"))).toBe(false);
      expect(isRetriableError(new ApiError(403, "forbidden"))).toBe(false);
    });

    it("does not retry arbitrary errors", () => {
      expect(isRetriableError(new Error("nope"))).toBe(false);
      expect(isRetriableError("just a string")).toBe(false);
      expect(isRetriableError(null)).toBe(false);
    });
  });

  describe("apiFetchRetry", () => {
    it("returns the parsed body without retrying on success", async () => {
      vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(200, { value: 42 }));

      const data = await apiFetchRetry<{ value: number }>("/x", { retryBaseDelayMs: 0 });

      expect(data).toEqual({ value: 42 });
      expect(fetch).toHaveBeenCalledTimes(1);
    });

    it("retries a transient 5xx then resolves", async () => {
      vi.mocked(fetch)
        .mockResolvedValueOnce(jsonResponse(503, { detail: "down" }))
        .mockResolvedValueOnce(jsonResponse(200, { ok: true }));

      const data = await apiFetchRetry<{ ok: boolean }>("/x", { retryBaseDelayMs: 0 });

      expect(data).toEqual({ ok: true });
      expect(fetch).toHaveBeenCalledTimes(2);
    });

    it("retries a network error then resolves", async () => {
      vi.mocked(fetch)
        .mockRejectedValueOnce(new TypeError("Failed to fetch"))
        .mockResolvedValueOnce(jsonResponse(200, { ok: true }));

      const data = await apiFetchRetry<{ ok: boolean }>("/x", { retryBaseDelayMs: 0 });

      expect(data).toEqual({ ok: true });
      expect(fetch).toHaveBeenCalledTimes(2);
    });

    it("does not retry a deterministic 4xx", async () => {
      vi.mocked(fetch).mockResolvedValue(jsonResponse(404, { detail: "missing" }));

      await expect(apiFetchRetry("/x", { retryBaseDelayMs: 0 })).rejects.toThrow(ApiError);
      expect(fetch).toHaveBeenCalledTimes(1);
    });

    it("gives up after the configured number of retries", async () => {
      vi.mocked(fetch).mockResolvedValue(jsonResponse(500, { detail: "boom" }));

      await expect(
        apiFetchRetry("/x", { retries: 2, retryBaseDelayMs: 0 }),
      ).rejects.toThrow(ApiError);
      // 1 initial attempt + 2 retries
      expect(fetch).toHaveBeenCalledTimes(3);
    });

    it("stops immediately when the caller's signal is already aborted", async () => {
      vi.mocked(fetch).mockRejectedValue(new TypeError("Failed to fetch"));
      const controller = new AbortController();
      controller.abort();

      await expect(
        apiFetchRetry("/x", { retries: 3, retryBaseDelayMs: 0, signal: controller.signal }),
      ).rejects.toThrow();
      expect(fetch).toHaveBeenCalledTimes(1);
    });
  });
});
