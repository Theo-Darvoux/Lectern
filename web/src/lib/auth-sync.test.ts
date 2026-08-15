import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getAccessToken, decodeToken } from "./auth-tokens";
import { registerTokenRefreshCallback } from "./api-client";
import { initAuthSync } from "./auth-sync";

vi.mock("./api-client", () => ({
  lockedRefresh: vi.fn(),
  registerTokenRefreshCallback: vi.fn(),
  apiFetch: vi.fn(),
  isRetriableError: vi.fn(() => false),
}));

vi.mock("./auth-tokens", () => ({
  clearAccessToken: vi.fn(),
  decodeToken: vi.fn(),
  getAccessToken: vi.fn(),
  hasAuthHint: vi.fn(() => false),
  setAccessToken: vi.fn(),
}));

vi.mock("./stores", () => ({
  useAuthStore: {
    getState: () => ({
      setUser: vi.fn(),
      logout: vi.fn(),
    }),
  },
}));

class MockBroadcastChannel {
  onmessage: ((event: MessageEvent) => void) | null = null;

  constructor(public readonly name: string) {}

  postMessage = vi.fn();
  close = vi.fn();
}

describe("auth-sync", () => {
  let cleanup: (() => void) | undefined;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    Object.defineProperty(globalThis, "BroadcastChannel", {
      configurable: true,
      writable: true,
      value: MockBroadcastChannel,
    });
    vi.mocked(getAccessToken).mockReturnValue("existing-token");
    vi.mocked(decodeToken).mockReturnValue({
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
  });

  afterEach(() => {
    cleanup?.();
    cleanup = undefined;
    vi.useRealTimers();
  });

  it("restores proactive refresh scheduling from an existing token on init", () => {
    cleanup = initAuthSync();

    expect(registerTokenRefreshCallback).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(1);

    cleanup();
    cleanup = undefined;
    expect(vi.getTimerCount()).toBe(0);
  });
});
