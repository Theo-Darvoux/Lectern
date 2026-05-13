import { describe, it, expect, beforeEach } from "vitest";
import { getAccessToken, setAccessToken, clearAccessToken, hasAuthHint, decodeToken } from "./auth-tokens";

describe("auth-tokens", () => {
  beforeEach(() => {
    clearAccessToken();
    localStorage.clear();
  });

  it("manages in-memory token and localstorage hint", () => {
    expect(getAccessToken()).toBeNull();
    expect(hasAuthHint()).toBe(false);

    setAccessToken("test-token");
    expect(getAccessToken()).toBe("test-token");
    expect(hasAuthHint()).toBe(true);

    clearAccessToken();
    expect(getAccessToken()).toBeNull();
    expect(hasAuthHint()).toBe(false);
  });

  it("decodes JWT tokens correctly", () => {
    // Mock JWT payload: {"exp": 12345} -> eyJleHAiOjEyMzQ1fQ==
    const token = "header.eyJleHAiOjEyMzQ1fQ==.signature";
    const decoded = decodeToken(token);
    expect(decoded?.exp).toBe(12345);
  });

  it("returns null for invalid tokens", () => {
    expect(decodeToken("invalid")).toBeNull();
    expect(decodeToken("header.invalid-payload.signature")).toBeNull();
  });
});
