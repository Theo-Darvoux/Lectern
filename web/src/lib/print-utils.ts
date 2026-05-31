/** Viewer types that support printing. */
const PRINTABLE_VIEWERS = new Set([
  "pdf",
  "image",
  "code",
  "markdown",
  "office",
  "qcm",
]);

export function isPrintable(viewerType: string): boolean {
  return PRINTABLE_VIEWERS.has(viewerType);
}

interface PrintIframeOptions {
  /** Optional CSS to inject into the iframe document. */
  css?: string;
  /** Title for the printed page. */
  title?: string;
}

/**
 * Opens a hidden iframe, writes HTML content, triggers window.print(), then cleans up.
 */
export function printInIframe(content: string, options: PrintIframeOptions = {}): void {
  const iframe = document.createElement("iframe");
  iframe.style.position = "fixed";
  iframe.style.left = "-9999px";
  iframe.style.top = "-9999px";
  iframe.style.width = "0";
  iframe.style.height = "0";
  iframe.style.border = "none";
  document.body.appendChild(iframe);

  const cleanup = () => {
    setTimeout(() => {
      if (document.body.contains(iframe)) {
        document.body.removeChild(iframe);
      }
    }, 1000);
  };

  const doc = iframe.contentDocument || iframe.contentWindow?.document;
  if (!doc) { cleanup(); return; }

  doc.open();
  doc.write(`
    <!DOCTYPE html>
    <html>
      <head>
        <title>${options.title ?? "Print"}</title>
        <style>
          *, *::before, *::after { box-sizing: border-box; }
          body {
            margin: 0;
            padding: 20px;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
                         Roboto, Helvetica, Arial, sans-serif;
            color: #111;
            background: #fff;
          }
          img { max-width: 100%; height: auto; }
          pre { white-space: pre-wrap; word-break: break-word; }
          table { border-collapse: collapse; width: 100%; }
          th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: left; }
          ${options.css ?? ""}
          @media print {
            body { padding: 0; }
          }
        </style>
      </head>
      <body>${content}</body>
    </html>
  `);
  doc.close();

  iframe.contentWindow?.addEventListener("afterprint", cleanup);
  setTimeout(() => {
    iframe.contentWindow?.focus();
    iframe.contentWindow?.print();
  }, 300);
}
