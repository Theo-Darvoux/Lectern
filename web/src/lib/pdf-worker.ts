// The pdf.js worker is served from /public so it has a stable, predictable URL
// that works correctly on hard refreshes in Next.js.
//
// DO NOT use `new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url)`.
// Next.js resolves import.meta.url at build time to the chunk's URL, which
// changes between builds. On a hard refresh the browser requests the *old*
// chunk URL, gets a 404, and pdf.js falls back to its fake-worker mode which
// requires `globalThis.pdfjsLib` — causing the "globalThis.pdfjsLib is
// undefined" crash.
//
// The file is copied to public/ by the `prebuild` / `postinstall` script in
// package.json (cp node_modules/pdfjs-dist/build/pdf.worker.min.mjs public/).
const PDF_WORKER_URL = "/pdf.worker.min.mjs";

export function createPdfWorker(): Worker {
    return new Worker(PDF_WORKER_URL, { type: "module" });
}
