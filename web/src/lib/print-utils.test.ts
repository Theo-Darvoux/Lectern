import { afterEach, describe, expect, it, vi } from "vitest";
import { printInIframe } from "./print-utils";

describe("print-utils", () => {
  afterEach(() => {
    document.querySelectorAll("iframe").forEach((iframe) => iframe.remove());
    document.querySelectorAll("[data-test-viewer-style]").forEach((style) => style.remove());
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

  it("waits for document images before opening the print dialog", async () => {
    vi.useFakeTimers();

    printInIframe('<img src="https://example.com/diagram.png" alt="Diagram">');

    const iframe = document.querySelector("iframe");
    const print = vi.fn();
    Object.defineProperty(iframe?.contentWindow, "print", { value: print });
    Object.defineProperty(iframe?.contentWindow, "focus", { value: vi.fn() });

    await vi.advanceTimersByTimeAsync(500);
    expect(print).not.toHaveBeenCalled();

    iframe?.contentDocument?.querySelector("img")?.dispatchEvent(new Event("load"));
    await vi.advanceTimersByTimeAsync(500);

    expect(print).toHaveBeenCalledOnce();
  });

  it("can reuse the application styles in the isolated print document", () => {
    const viewerStyle = document.createElement("style");
    viewerStyle.dataset.testViewerStyle = "true";
    viewerStyle.textContent = ".prose { color: rgb(24, 24, 27); }";
    document.head.appendChild(viewerStyle);

    printInIframe('<div class="prose">Styled notes</div>', { copyStyles: true });

    const iframeStyles = Array.from(
      document.querySelector("iframe")?.contentDocument?.querySelectorAll("style") ?? [],
    );
    expect(iframeStyles.some((style) => style.textContent?.includes(".prose"))).toBe(true);
  });
});
