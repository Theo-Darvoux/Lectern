import React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NotebookRenderer } from "./notebook-renderer";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

vi.mock("./async-material-image", () => ({
  // eslint-disable-next-line @next/next/no-img-element
  AsyncMaterialImage: ({ src, alt }: { src: string; alt: string }) => <img src={src} alt={alt} />,
}));

vi.mock("./mermaid", () => ({
  Mermaid: () => null,
}));

vi.mock("./callout", () => ({
  Callout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

describe("NotebookRenderer", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
  });

  it("renders markdown and code cells from a Jupyter notebook", async () => {
    const content = JSON.stringify({
      nbformat: 4,
      nbformat_minor: 5,
      metadata: { kernelspec: { language: "python" } },
      cells: [
        { cell_type: "markdown", metadata: {}, source: ["# Analysis\n", "Some notes"] },
        { cell_type: "code", execution_count: 3, metadata: {}, source: ["x = 1\n", "x + 1"], outputs: [] },
      ],
    });

    await act(async () => root.render(<NotebookRenderer content={content} />));

    expect(host.querySelector("h1")?.textContent).toBe("Analysis");
    expect(host.textContent).toContain("Some notes");
    expect(host.textContent).toContain("x = 1\nx + 1");
    expect(host.textContent).toContain("[3]");
  });

  it("renders common text, error, and image outputs without executing rich HTML", async () => {
    const content = JSON.stringify({
      nbformat: 4,
      cells: [{
        cell_type: "code",
        execution_count: 1,
        source: "print('hello')",
        outputs: [
          { output_type: "stream", text: ["\u001b[32mhello\u001b[0m\n"] },
          { output_type: "execute_result", data: { "text/plain": ["2"] } },
          { output_type: "display_data", data: { "image/png": "aGVsbG8=" } },
          { output_type: "error", ename: "ValueError", evalue: "bad input", traceback: ["Traceback", "\u001b[31mValueError: bad input\u001b[0m"] },
          { output_type: "display_data", data: { "text/html": "<script>window.hacked = true</script>" } },
        ],
      }],
    });

    await act(async () => root.render(<NotebookRenderer content={content} />));

    expect(host.textContent).toContain("hello");
    expect(host.textContent).toContain("2");
    expect(host.querySelector(".text-destructive")?.textContent).toBe("Traceback\nValueError: bad input");
    expect(host.textContent).not.toContain("[32m");
    expect(host.querySelector("img")?.getAttribute("src")).toBe("data:image/png;base64,aGVsbG8=");
    expect(host.querySelector("script")).toBeNull();
    expect(host.textContent).not.toContain("window.hacked");
  });

  it("shows an invalid-notebook state for malformed or non-notebook JSON", async () => {
    await act(async () => root.render(<NotebookRenderer content="{not json" />));
    expect(host.textContent).toContain("invalid");

    await act(async () => root.render(<NotebookRenderer content='{"kind":"document"}' />));
    expect(host.textContent).toContain("invalid");

    await act(async () => root.render(<NotebookRenderer content='{"cells":[]}' />));
    expect(host.textContent).toContain("invalid");
  });

  it("renders raster images embedded as Markdown-cell attachments", async () => {
    const content = JSON.stringify({
      nbformat: 4,
      cells: [{
        cell_type: "markdown",
        source: "![plot](attachment:plot.png)",
        attachments: { "plot.png": { "image/png": "aGVsbG8=" } },
      }],
    });

    await act(async () => root.render(<NotebookRenderer content={content} />));
    await act(async () => Promise.resolve());

    expect(host.querySelector("img[alt='plot']")?.getAttribute("src"))
      .toBe("data:image/png;base64,aGVsbG8=");
  });

  it("applies syntax highlighting to code cells", async () => {
    const content = JSON.stringify({
      nbformat: 4,
      metadata: { language_info: { name: "python" } },
      cells: [
        { cell_type: "code", execution_count: 1, source: "def add(a, b):\n    return a + b", outputs: [] },
      ],
    });

    await act(async () => root.render(<NotebookRenderer content={content} />));

    const codeEl = host.querySelector("code.hljs");
    expect(codeEl).not.toBeNull();
    expect(codeEl?.querySelector(".hljs-keyword")?.textContent).toBe("def");
  });
});
