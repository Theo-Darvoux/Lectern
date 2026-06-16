// pdf.js (v4) worker setup.
//
// pdf.js v4 ships an ES-module-only worker, instantiated as
// `new Worker(url, { type: "module" })`. We resolve its URL through the bundler
// rather than hand-copying a file into /public: that pins the worker to the
// installed `pdfjs-dist` version and emits it under /_next/static with a
// content hash (and, given a server that maps .mjs to a JS MIME type, the
// correct content-type for a module worker).
const pdfWorkerUrl = new URL(
    "pdfjs-dist/build/pdf.worker.min.mjs",
    import.meta.url,
);

// Build the worker explicitly and hand it to `getDocument({ worker })` instead
// of setting the `GlobalWorkerOptions.workerSrc` *string* and letting pdf.js
// spawn the worker itself. This is deliberate: pdf.js's internal worker setup
// keeps a *module-global* `isWorkerDisabled` flag. The first time a worker it
// spawned emits an `error` event — a momentary bad response, a wrong .mjs MIME
// type, a transient network blip on a cold page load — pdf.js flips that flag
// and permanently falls back, for the rest of the page's life, to its
// main-thread "fake worker". For the ESM build that fake worker throws
// `globalThis.pdfjsLib is undefined`. Because the flag is global, a single
// failure in *any* PDF (e.g. a thumbnail) poisons *every* later PDF, which is
// why the failure looked route-dependent: a warm session that already spawned
// one good worker stayed fine, while a cold reload straight onto a viewer did
// not. Supplying our own worker port routes through
// `PDFWorker._initializeFromPort`, which never consults that flag.
//
// Each call returns a fresh worker. The caller owns its lifecycle and must
// `terminate()` it (and `destroy()` the wrapping `PDFWorker`) on cleanup. A
// dedicated worker per document also keeps concurrent documents isolated —
// destroying one (e.g. a thumbnail scrolling out of view) never tears down a
// worker another document is still using.
export function createPdfWorker(): Worker {
    return new Worker(pdfWorkerUrl, { type: "module" });
}
