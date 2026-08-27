/**
 * Builds an isolated copy of rendered Markdown for printing/PDF export.
 * Interactive viewer state must never leak into or be changed by the export.
 */
export function prepareMarkdownForPrint(viewer: HTMLElement): string {
  const clone = viewer.cloneNode(true) as HTMLElement;

  clone.querySelectorAll("details.callout").forEach((details) => {
    details.setAttribute("open", "");
  });

  clone.querySelectorAll(".annotation-highlight").forEach((overlay) => overlay.remove());
  clone.querySelectorAll("img").forEach((image) => image.setAttribute("loading", "eager"));

  return clone.outerHTML;
}

export function getMarkdownPdfTitle(fileName: string): string {
  return fileName.replace(/\.(?:md|markdown)$/i, "") || "Document";
}

const PENDING_MARKDOWN_SELECTOR = "[data-markdown-export-pending]";

/** Waits for async image/diagram renderers, with a bounded fallback for failures. */
export function waitForMarkdownRender(viewer: HTMLElement, timeoutMs = 10_000): Promise<void> {
  if (!viewer.querySelector(PENDING_MARKDOWN_SELECTOR)) return Promise.resolve();

  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      observer.disconnect();
      clearTimeout(timeout);
      resolve();
    };
    const observer = new MutationObserver(() => {
      if (!viewer.querySelector(PENDING_MARKDOWN_SELECTOR)) finish();
    });
    const timeout = setTimeout(finish, timeoutMs);

    observer.observe(viewer, { childList: true, subtree: true });
  });
}

/** Print stylesheet mirroring the Markdown viewer in a paper-friendly light theme. */
export const MARKDOWN_PDF_CSS = `
  @page { size: A4; margin: 16mm 18mm 18mm; }

  html {
    color-scheme: light;
    background: #ffffff;
    print-color-adjust: exact;
    -webkit-print-color-adjust: exact;
  }
  body {
    max-width: 820px;
    margin: 0 auto;
    padding: 0;
    color: #18181b;
    background: #ffffff;
    font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 10.5pt;
    line-height: 1.65;
  }
  h1, h2, h3, h4, h5, h6 {
    color: #18181b;
    font-weight: 650;
    line-height: 1.25;
    break-after: avoid-page;
  }
  h1 { margin: 0 0 0.8em; padding-bottom: 0.32em; border-bottom: 1px solid #e4e4e7; font-size: 2em; }
  h2 { margin: 1.6em 0 0.65em; padding-bottom: 0.25em; border-bottom: 1px solid #e4e4e7; font-size: 1.5em; }
  h3 { margin: 1.45em 0 0.55em; font-size: 1.25em; }
  h4 { margin: 1.3em 0 0.5em; font-size: 1.08em; }
  h5, h6 { margin: 1.2em 0 0.45em; font-size: 1em; }
  p { margin: 0.75em 0; orphans: 3; widows: 3; }
  a { color: #2563eb; text-decoration: none; }
  strong { font-weight: 650; }
  ul, ol { margin: 0.75em 0; padding-left: 1.75em; }
  li { margin: 0.25em 0; }
  li > p { margin: 0.25em 0; }
  hr { margin: 1.6em 0; border: 0; border-top: 1px solid #d4d4d8; }
  blockquote {
    margin: 1em 0;
    padding: 0.15em 1em;
    color: #52525b;
    border-left: 4px solid #d4d4d8;
    font-style: italic;
  }
  mark { padding: 0 0.15em; border-radius: 2px; color: #713f12; background: #fef08a; }

  div:has(> table) { overflow: visible !important; }
  table { width: 100%; margin: 1em 0; border-collapse: collapse; font-size: 0.92em; }
  thead { display: table-header-group; }
  tr { break-inside: avoid; }
  th, td { padding: 0.5em 0.7em; border: 1px solid #d4d4d8; text-align: left; vertical-align: top; }
  th { color: #27272a; background: #f4f4f5; font-weight: 650; }
  tr:nth-child(even) td { background: #fafafa; }

  img, svg { max-width: 100%; height: auto; }
  img { border-radius: 0.45rem; break-inside: avoid-page; }
  svg { color: currentColor; }

  code {
    padding: 0.12em 0.32em;
    color: #27272a;
    background: #f4f4f5;
    border: 1px solid #e4e4e7;
    border-radius: 0.25rem;
    font-family: "Geist Mono", "SFMono-Regular", Consolas, monospace;
    font-size: 0.875em;
  }
  pre {
    margin: 1em 0;
    padding: 0.85em 1em;
    overflow: visible;
    color: #27272a;
    background: #f4f4f5;
    border: 1px solid #e4e4e7;
    border-radius: 0.45rem;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    break-inside: avoid-page;
  }
  pre code { padding: 0; background: transparent; border: 0; font-size: 0.82em; }
  .hljs { color: #24292e; }
  .hljs-comment, .hljs-quote { color: #6a737d; font-style: italic; }
  .hljs-keyword, .hljs-selector-tag, .hljs-subst { color: #d73a49; }
  .hljs-string, .hljs-doctag, .hljs-attr, .hljs-addition { color: #032f62; }
  .hljs-number, .hljs-literal, .hljs-variable, .hljs-template-variable, .hljs-link { color: #005cc5; }
  .hljs-title, .hljs-section, .hljs-selector-id { color: #6f42c1; font-weight: 650; }
  .hljs-type, .hljs-class .hljs-title { color: #6f42c1; }
  .hljs-built_in, .hljs-builtin-name { color: #005cc5; }
  .hljs-meta, .hljs-symbol { color: #e36209; }
  .hljs-deletion { color: #b31d28; background: #ffeef0; }
  .hljs-emphasis { font-style: italic; }
  .hljs-strong { font-weight: 650; }

  .callout {
    --callout-color: #64748b;
    margin: 1em 0;
    padding: 0.8em 0.95em;
    color: #27272a;
    background: #f8fafc;
    background: color-mix(in srgb, var(--callout-color) 8%, white);
    border: 1px solid color-mix(in srgb, var(--callout-color) 35%, white);
    border-left: 4px solid var(--callout-color);
    border-radius: 0.5rem;
    box-shadow: 0 1px 2px rgb(0 0 0 / 0.05);
    break-inside: avoid-page;
  }
  .callout-info { --callout-color: #3b82f6; }
  .callout-abstract { --callout-color: #0891b2; }
  .callout-tip { --callout-color: #059669; }
  .callout-success { --callout-color: #16a34a; }
  .callout-question { --callout-color: #ca8a04; }
  .callout-warning { --callout-color: #ea580c; }
  .callout-failure { --callout-color: #dc2626; }
  .callout-danger { --callout-color: #b91c1c; }
  .callout-example { --callout-color: #7c3aed; }
  .callout-quote { --callout-color: #71717a; }
  .callout-header { display: flex; align-items: center; gap: 0.5em; color: var(--callout-color); font-weight: 700; }
  .callout-header svg { width: 1em; height: 1em; flex: none; }
  .callout-content { margin-top: 0.5em; padding: 0 0.15em; color: #27272a; }
  .callout-content > :first-child { margin-top: 0; }
  .callout-content > :last-child { margin-bottom: 0; }
  .callout summary { list-style: none; }
  .callout summary::-webkit-details-marker, .callout summary::marker { display: none; }
  .callout summary .callout-header > svg:first-child { transform: rotate(90deg); }

  .katex-display { display: flex !important; justify-content: center; overflow: visible; break-inside: avoid-page; }

  @media print {
    body { max-width: none; }
    a { color: #1d4ed8; }
  }
`;
