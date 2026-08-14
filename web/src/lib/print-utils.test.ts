import { afterEach, describe, expect, it, vi } from "vitest";
import { printInIframe } from "./print-utils";

describe("print-utils", () => {
  afterEach(() => {
    document.querySelectorAll("iframe").forEach((iframe) => iframe.remove());
    vi.useRealTimers();
  });

  it("treats the print title as text instead of HTML", () => {
    vi.useFakeTimers();
    const maliciousTitle =
      'notes</title><script id="injected">window.__printInjected = true</script>';

    printInIframe("<p>safe content</p>", { title: maliciousTitle });

    const iframe = document.querySelector("iframe");
    expect(iframe).not.toBeNull();

    const doc = iframe?.contentDocument;
    expect(doc).toBeTruthy();
    expect(doc?.title).toBe(maliciousTitle);
    expect(doc?.querySelector("#injected")).toBeNull();
    expect(doc?.body.textContent).toContain("safe content");
  });
});
