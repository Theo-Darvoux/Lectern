import React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useConfigStore, type PublicConfig } from "@/lib/stores";
import { MarkdownRenderer } from "./markdown-renderer";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("./async-material-image", () => ({
  AsyncMaterialImage: () => null,
}));

vi.mock("./mermaid", () => ({
  Mermaid: () => null,
}));

vi.mock("./callout", () => ({
  Callout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

describe("MarkdownRenderer document links", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    useConfigStore.setState({ config: null });
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    useConfigStore.setState({ config: null });
  });

  it("allows safe external links by default and opens web links in a new tab", async () => {
    await act(async () => {
      root.render(
        <MarkdownRenderer content="[Reference](https://example.com/notes) [Email](mailto:teacher@example.com)" />,
      );
    });

    const links = host.querySelectorAll("a");
    expect(links).toHaveLength(2);
    expect(links[0].getAttribute("href")).toBe("https://example.com/notes");
    expect(links[0].getAttribute("target")).toBe("_blank");
    expect(links[0].getAttribute("rel")).toBe("noopener noreferrer");
    expect(links[1].getAttribute("href")).toBe("mailto:teacher@example.com");
  });

  it("disables external links while preserving relative document links", async () => {
    useConfigStore.setState({
      config: { allow_external_document_links: false } as PublicConfig,
    });

    await act(async () => {
      root.render(
        <MarkdownRenderer content="[External](https://example.com/notes) [Internal](chapter-2.md)" />,
      );
    });

    expect(host.querySelector("a[href='https://example.com/notes']")).toBeNull();
    expect(host.textContent).toContain("External");
    expect(host.querySelector("a[href='chapter-2.md']")?.textContent).toBe("Internal");
  });
});
