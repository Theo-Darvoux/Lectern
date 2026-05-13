import { describe, it, expect, vi, beforeEach } from "vitest";
import { apiRequest, getClientId, ApiError } from "./api-client";
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

describe("api-client", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  describe("getClientId", () => {
    it("generates and persists a client id", () => {
      const id1 = getClientId();
      expect(id1).toBeDefined();
      expect(localStorage.getItem("wikint_client_id")).toBe(id1);
      
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

    it("throws ApiError on failure", async () => {
      vi.mocked(fetch).mockResolvedValue({
        ok: false,
        status: 400,
        json: async () => ({ detail: "Bad Request" }),
      } as Response);

      await expect(apiRequest("/test")).rejects.toThrow(ApiError);
    });
  });
});
