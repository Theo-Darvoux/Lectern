// Bundler-resolved URL of the pdf.js worker.
//
// pdf.js v4 ships an ES-module-only worker and always instantiates it as
// `new Worker(url, { type: "module" })`. Pointing `workerSrc` at a file
// hand-copied into /public (e.g. "/pdf.worker.min.js") leaves its *version* and
// served *content-type* to drift away from the installed `pdfjs-dist`; when the
// worker can't be created, pdf.js drops into its main-thread "fake worker"
// fallback (`await import(workerSrc)`), which throws
// `globalThis.pdfjsLib is undefined`.
//
// Resolving through the bundler pins the worker to the installed library version
// and emits it under /_next/static with the correct JavaScript MIME type, so
// both the real and fallback worker paths load cleanly.
export const pdfWorkerSrc = new URL(
    "pdfjs-dist/build/pdf.worker.min.mjs",
    import.meta.url,
).toString();
