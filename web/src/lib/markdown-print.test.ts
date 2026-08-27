import { describe, expect, it } from "vitest";

import {
  getMarkdownPdfTitle,
  prepareMarkdownForPrint,
  waitForMarkdownRender,
} from "./markdown-print";

describe("prepareMarkdownForPrint", () => {
  it("exports every collapsible callout expanded without changing the viewer", () => {
    const viewer = document.createElement("div");
    viewer.innerHTML = `
      <h1>Course notes</h1>
      <details class="callout callout-warning">
        <summary>Read this</summary>
        <div class="callout-content">Hidden explanation</div>
      </details>
      <details class="appendix">
        <summary>Optional appendix</summary>
        <p>Keep my authored state</p>
      </details>
    `;

    const exported = prepareMarkdownForPrint(viewer);
    const exportDocument = new DOMParser().parseFromString(exported, "text/html");

    expect(exportDocument.querySelector("details.callout")?.hasAttribute("open")).toBe(true);
    expect(exportDocument.querySelector("details.appendix")?.hasAttribute("open")).toBe(false);
    expect(exportDocument.body.textContent).toContain("Hidden explanation");
    expect(viewer.querySelector("details")?.hasAttribute("open")).toBe(false);
  });

  it("omits viewer-only annotation overlays from the exported document", () => {
    const viewer = document.createElement("div");
    viewer.innerHTML = `
      <p>Important text</p>
      <div class="annotation-highlight" data-thread-id="thread-1">overlay</div>
    `;

    const exported = prepareMarkdownForPrint(viewer);
    const exportDocument = new DOMParser().parseFromString(exported, "text/html");

    expect(exportDocument.body.textContent).toContain("Important text");
    expect(exportDocument.querySelector(".annotation-highlight")).toBeNull();
  });

  it("keeps the viewer typography wrapper so its rendered styles can be reused", () => {
    const viewer = document.createElement("div");
    viewer.className = "prose prose-sm max-w-none";
    viewer.innerHTML = "<p>Styled notes</p>";

    const exported = prepareMarkdownForPrint(viewer);
    const exportDocument = new DOMParser().parseFromString(exported, "text/html");

    expect(exportDocument.querySelector(".prose.prose-sm")?.textContent).toContain("Styled notes");
  });

  it("eagerly loads images in the off-screen PDF document", () => {
    const viewer = document.createElement("div");
    viewer.innerHTML = '<img src="diagram.png" alt="Diagram" loading="lazy">';

    const exported = prepareMarkdownForPrint(viewer);
    const exportDocument = new DOMParser().parseFromString(exported, "text/html");

    expect(exportDocument.querySelector("img")?.getAttribute("loading")).toBe("eager");
  });

  it("uses a PDF-friendly document title instead of retaining the Markdown extension", () => {
    expect(getMarkdownPdfTitle("Lecture notes.markdown")).toBe("Lecture notes");
    expect(getMarkdownPdfTitle("README.md")).toBe("README");
    expect(getMarkdownPdfTitle("Course outline")).toBe("Course outline");
  });

  it("waits for async Markdown content before taking the export snapshot", async () => {
    const viewer = document.createElement("div");
    viewer.innerHTML = '<span data-markdown-export-pending="image">Loading image</span>';

    const ready = waitForMarkdownRender(viewer);
    viewer.querySelector("[data-markdown-export-pending]")?.remove();

    await expect(ready).resolves.toBeUndefined();
  });
});
