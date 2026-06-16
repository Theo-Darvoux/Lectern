"use client";

import { useCallback, useEffect, useState, useRef } from "react";
import { apiFetchRetry } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import { getFileTypeStyle } from "./file-type-display";
import type { MaterialDetail } from "./types";
import { Loader2 } from "lucide-react";
import { MarkdownRenderer } from "../viewers/markdown-renderer";
import { useInView } from "@/hooks/use-in-view";
import { MIME_QCM } from "@/lib/file-utils";
import { pdfWorkerSrc } from "@/lib/pdf-worker";

// Renders the first page of a PDF to a canvas via pdf.js. The engine (~1MB of
// JS) is imported lazily inside the effect, so it only loads when a card
// actually needs a raw-PDF fallback preview (grid/lazy mode never mounts this).
function PdfThumbnailCanvas({
  url,
  width,
  onReady,
  onError,
}: {
  url: string;
  width: number;
  onReady: () => void;
  onError: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let cancelled = false;
    let cleanup: (() => void) | undefined;
    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        if (cancelled) return;
        pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerSrc;
        const task = pdfjs.getDocument({ url });
        const doc = await task.promise;
        if (cancelled) { doc.destroy(); return; }
        const page = await doc.getPage(1);
        const canvas = canvasRef.current;
        if (cancelled || !canvas) { doc.destroy(); return; }
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        const baseViewport = page.getViewport({ scale: 1 });
        const scale = (width || 300) / baseViewport.width;
        const viewport = page.getViewport({ scale: scale * dpr });
        const ctx = canvas.getContext("2d");
        if (!ctx) { doc.destroy(); return; }
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        canvas.style.width = "100%";
        await page.render({ canvasContext: ctx, viewport }).promise;
        if (cancelled) { doc.destroy(); return; }
        onReady();
        cleanup = () => doc.destroy();
      } catch (err) {
        if (!cancelled && (err as Error)?.name !== "AbortException") onError();
      }
    })();
    return () => { cancelled = true; cleanup?.(); };
  }, [url, width, onReady, onError]);

  return <canvas ref={canvasRef} className="block w-full" />;
}

// Concurrency-limited thumbnail fetcher. On first paint of a grid view, every
// visible card hits /thumbnail simultaneously — 20-30 parallel requests + image
// decode storms cause severe FPS drops. Queue to a small parallelism cap.
const MAX_CONCURRENT_THUMBNAILS = 4;

// A signed worker/presigned URL can transiently fail to load (cold edge cache,
// brief 5xx, flaky connection) and an <img> that errors never re-attempts on its
// own. Retry a bounded number of times with backoff, cache-busting the request
// so the browser doesn't replay its cached failure. The extra query param is
// safe: the worker only verifies `token` and strips the query string from its
// cache key, so it neither breaks signature validation nor pollutes the cache.
const MAX_IMG_RETRIES = 3;
let inflightThumbnails = 0;
const thumbnailQueue: Array<() => void> = [];

function withThumbnailSlot<T>(task: () => Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const run = () => {
      inflightThumbnails++;
      task()
        .then(resolve, reject)
        .finally(() => {
          inflightThumbnails--;
          const next = thumbnailQueue.shift();
          if (next) next();
        });
    };
    if (inflightThumbnails < MAX_CONCURRENT_THUMBNAILS) run();
    else thumbnailQueue.push(run);
  });
}

interface MaterialPreviewProps {
  material: MaterialDetail;
  className?: string;
  /** When true, defers loading until the card scrolls near the viewport. */
  lazy?: boolean;
}

/** Whether the thumbnail API returned a real generated WebP or a raw-file fallback. */
type ThumbnailType = "webp" | "fallback" | null;

export function MaterialPreview({ material, className, lazy }: MaterialPreviewProps) {
  const [url, setUrl] = useState<string | null>(null);
  const [thumbnailType, setThumbnailType] = useState<ThumbnailType>(null);
  const [loading, setLoading] = useState(false);
  const [videoLoaded, setVideoLoaded] = useState(false);
  const [textPreview, setTextPreview] = useState<string | null>(null);
  const [pdfReady, setPdfReady] = useState(false);
  const handlePdfReady = useCallback(() => setPdfReady(true), []);
  const handlePdfError = useCallback(() => setPdfReady(false), []);
  const [imgBust, setImgBust] = useState(0);
  const imgAttemptRef = useRef(0);
  const imgRetryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(300);
  const videoRef = useRef<HTMLVideoElement>(null);

  const inView = useInView(containerRef);
  // In lazy mode, gate loading on viewport intersection. In non-lazy mode we
  // always load — collapsing to a constant `true` so the fetch effect below does
  // NOT re-run when `inView` later flips false→true. A re-run would reset
  // `pdfReady` to false while the PDF URL stays unchanged (same URL), so the
  // canvas onReady never re-fires and a rendered PDF preview fades back to
  // opacity-0 ("renders then disappears").
  const shouldLoad = lazy ? inView : true;

  const versionInfo = material.current_version_info;
  const fileName = versionInfo?.file_name ?? "";
  const mimeType = versionInfo?.file_mime_type ?? "";

  const isImage = mimeType.startsWith("image/") || /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(fileName);
  const isVideo = mimeType.startsWith("video/") || /\.(mp4|webm|avi|mkv|mov)$/i.test(fileName);
  const isMarkdown = mimeType === "text/markdown" || /\.(md|markdown)$/i.test(fileName);
  const isText = (mimeType.startsWith("text/") || /\.(txt|py|js|ts|json)$/i.test(fileName)) && !isMarkdown;
  const isPDF = mimeType === "application/pdf" || fileName.toLowerCase().endsWith(".pdf");
  const isOffice = mimeType.includes("ms-") || mimeType.includes("officedocument") || /\.(docx|xlsx|pptx)$/i.test(fileName);
  const isQCM = mimeType === MIME_QCM || fileName.toLowerCase().endsWith(".qcm");

  // Track container width for PDF canvas sizing — only needed when the PDF thumbnail will render.
  useEffect(() => {
    if (!isPDF || lazy) return;
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      if (entry) setContainerWidth(entry.contentRect.width || 300);
    });
    ro.observe(el);
    setContainerWidth(el.clientWidth || 300);
    return () => ro.disconnect();
  }, [isPDF, lazy]);

  useEffect(() => {
    // In lazy mode, wait until the card is near the viewport before fetching.
    if (!shouldLoad) return;

    let mounted = true;
    setLoading(true);
    setPdfReady(false);

    async function fetchPreview() {
      try {
        // 1. Try the /thumbnail endpoint first.
        //    It returns { url, thumbnail_type: "webp" | "fallback" }.
        try {
          const thumbData = await withThumbnailSlot(() =>
            apiFetchRetry<{ url: string; thumbnail_type: ThumbnailType }>(
              `/materials/${material.id}/thumbnail`
            )
          );
          if (mounted && thumbData && thumbData.url) {
            setUrl(thumbData.url);
            setThumbnailType(thumbData.thumbnail_type ?? "webp");
            setLoading(false);
            return;
          }
        } catch {
          // Thumbnail not available — fall through to inline fallback
          console.debug("No server thumbnail available, falling back to inline source.");
        }

        if (!mounted) return;

        // 2. Fallback: fetch direct inline URL for native client-side rendering
        const isMediaOrText = isImage || isVideo || isText || isMarkdown;
        if (!isMediaOrText) {
          setLoading(false);
          return;
        }

        const data = await withThumbnailSlot(() =>
          apiFetchRetry<{ url: string }>(`/materials/${material.id}/inline`)
        );
        if (!mounted) return;

        if (data && data.url) {
          setUrl(data.url);
          setThumbnailType(null);  // plain inline URL

          // Fetch text snippet for text/markdown files
          if (isText || isMarkdown) {
          try {
            const res = await fetch(data.url);
            const contentEncoding = res.headers.get("Content-Encoding");
            let text = "";
            if (contentEncoding === "gzip" || contentEncoding?.includes("gzip")) {
              if (res.body && typeof DecompressionStream !== "undefined") {
                const decompressedStream = res.body.pipeThrough(new DecompressionStream("gzip"));
                const decompressedResponse = new Response(decompressedStream);
                text = await decompressedResponse.text();
              } else {
                text = await res.text();
              }
            } else {
              const blob = await res.blob();
              const buffer = await blob.arrayBuffer();
              const arr = new Uint8Array(buffer);
              if (arr.length >= 2 && arr[0] === 0x1f && arr[1] === 0x8b && typeof DecompressionStream !== "undefined") {
                const stream = new Blob([buffer]).stream();
                const decompressedStream = stream.pipeThrough(new DecompressionStream("gzip"));
                const decompressedResponse = new Response(decompressedStream);
                text = await decompressedResponse.text();
              } else {
                text = new TextDecoder().decode(buffer);
              }
            }
            if (mounted) setTextPreview(text.slice(0, 1000));
          } catch {
            // ignore
          }
        }
      }
    } catch {
        // ignore
      } finally {
        if (mounted) setLoading(false);
      }
    }

    const timer = setTimeout(fetchPreview, 100);
    return () => {
      mounted = false;
      clearTimeout(timer);
    };
  }, [material.id, isText, isImage, isVideo, isMarkdown, isPDF, shouldLoad]);

  // Reset the image-retry counter whenever the source URL changes, and clear any
  // pending retry timer on unmount.
  useEffect(() => {
    imgAttemptRef.current = 0;
    setImgBust(0);
    return () => {
      if (imgRetryTimer.current) {
        clearTimeout(imgRetryTimer.current);
        imgRetryTimer.current = null;
      }
    };
  }, [url]);

  const handleImgError = () => {
    if (imgRetryTimer.current || imgAttemptRef.current >= MAX_IMG_RETRIES) return;
    const delay = 500 * 2 ** imgAttemptRef.current;
    imgRetryTimer.current = setTimeout(() => {
      imgRetryTimer.current = null;
      imgAttemptRef.current += 1;
      setImgBust(imgAttemptRef.current); // bump cache-buster → forces <img> reload
    }, delay);
  };

  const imgSrc =
    url && imgBust > 0 ? `${url}${url.includes("?") ? "&" : "?"}_r=${imgBust}` : url;

  const { gradient, iconColorClass, Icon } = getFileTypeStyle(fileName, mimeType);

  const handleVideoLoaded = () => {
    if (videoRef.current) {
      const duration = videoRef.current.duration;
      const seekTime = Math.min(duration * 0.1, 2);
      videoRef.current.currentTime = Math.max(seekTime, 0.5);
      setVideoLoaded(true);
    }
  };

  // An image URL that should render as <img>:
  //   - "webp"     → real generated WebP thumbnail (always render as <img>, even for videos/PDFs)
  //   - "fallback" → raw file returned by the server; only renderable for non-video, non-PDF types
  //   - null       → came from the /inline fallback path (isImage must be true)
  const showAsImg =
    url &&
    (thumbnailType === "webp" ||
      (!isVideo && !isPDF && (thumbnailType === "fallback" || (thumbnailType === null && isImage))));

  // PDF fallback: raw PDF file returned by server → render first page with pdf.js.
  // Disabled in lazy/grid mode — instantiating pdf.js per card is too expensive.
  const showAsPdf = url && isPDF && thumbnailType === "fallback" && !lazy;

  // PDF with a real generated WebP thumbnail → just use <img>
  const showPdfWebp = url && isPDF && thumbnailType === "webp";

  const showContent =
    showAsImg ||
    showAsPdf ||
    showPdfWebp ||
    (url && isVideo && videoLoaded) ||
    (url && isText && textPreview) ||
    (url && isMarkdown && textPreview) ||
    isQCM;

  return (
    <div
      ref={containerRef}
      className={cn(
        "relative w-full h-full flex items-center justify-center overflow-hidden bg-linear-to-br",
        gradient,
        className
      )}
    >
      {/* ── Decorative "Paper Stack" for Documents (until a preview loads) ── */}
      {(isPDF || isOffice) && !showContent && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="absolute h-24 w-18 bg-white/10 rounded-sm rotate-6 translate-x-1" />
          <div className="absolute h-24 w-18 bg-white/5 rounded-sm -rotate-3 -translate-x-1" />
        </div>
      )}

      {/* ── Background Icon ─────────────────────────────────────────────── */}
      <Icon
        className={cn(
          "h-12 w-12 z-10",
          // In lazy/grid mode skip CSS filters and transform transitions — each
          // filter forces its own compositor layer and animating them on 50+
          // simultaneously visible cards tanks scroll/hover FPS. Keep a plain
          // opacity transition so the icon fades out in sync with the image
          // fade-in; this only plays when a thumbnail loads, not on every frame.
          lazy
            ? "transition-opacity duration-300"
            : "transition-[opacity,transform,filter] duration-500 drop-shadow-xl",
          iconColorClass,
          showContent
            ? lazy ? "opacity-0 scale-75" : "opacity-0 scale-75 blur-sm"
            : "opacity-90 scale-100",
        )}
      />

      {/* ── Real WebP thumbnail or native image ─────────────────────────── */}
      {(showAsImg || showPdfWebp) && (
        /* eslint-disable-next-line @next/next/no-img-element */
        <img
          src={imgSrc!}
          alt={material.title}
          className={cn(
            "absolute inset-0 h-full w-full object-cover",
            lazy ? "animate-in fade-in duration-300" : "animate-in fade-in zoom-in-95 duration-700",
          )}
          loading="lazy"
          decoding="async"
          onError={handleImgError}
        />
      )}

      {/* ── PDF first-page preview via pdf.js canvas (fallback URL = raw PDF) ── */}
      {showAsPdf && (
        <div
          className={cn(
            "absolute inset-0 overflow-hidden bg-white pointer-events-none select-none",
            "animate-in fade-in zoom-in-95 duration-700",
            pdfReady ? "opacity-100" : "opacity-0"
          )}
        >
          <PdfThumbnailCanvas
            url={url!}
            width={containerWidth}
            onReady={handlePdfReady}
            onError={handlePdfError}
          />
          {/* Gradient overlay so it blends into the card gradient */}
          <div className="absolute inset-x-0 bottom-0 h-1/3 bg-gradient-to-t from-black/30 to-transparent pointer-events-none" />
        </div>
      )}

      {/* ── Native Video Preview ─────────────────────────────────────────── */}
      {url && isVideo && (
        <video
          ref={videoRef}
          src={url}
          muted
          loop
          playsInline
          preload="metadata"
          onLoadedData={handleVideoLoaded}
          className={cn(
            "absolute inset-0 h-full w-full object-cover transition-opacity duration-700",
            videoLoaded ? "opacity-100" : "opacity-0"
          )}
          onMouseEnter={(e) => e.currentTarget.play().catch(() => {})}
          onMouseLeave={(e) => {
            e.currentTarget.pause();
            const duration = e.currentTarget.duration;
            if (isFinite(duration) && duration > 0) {
              const seekTime = Math.min(duration * 0.1, 2);
              e.currentTarget.currentTime = Math.max(seekTime, 0.5);
            }
          }}
        />
      )}

      {/* ── Text Snippet Preview (Code/Txt) ─────────────────────────────── */}
      {thumbnailType === null && isText && textPreview && (
        <div className="absolute inset-0 p-4 font-mono text-[10px] leading-relaxed text-white/80 overflow-hidden select-none animate-in fade-in slide-in-from-bottom-2 duration-700 bg-black/5">
          <div className="line-clamp-10 whitespace-pre-wrap opacity-60">
            {textPreview}
          </div>
          <div className="absolute inset-x-0 bottom-0 h-16 bg-linear-to-t from-black/20 to-transparent" />
        </div>
      )}

      {/* ── Hifi Markdown Preview Card (full viewer only) ───────────────── */}
      {thumbnailType === null && isMarkdown && textPreview && !lazy && (
        <div className="absolute inset-0 p-3 overflow-hidden select-none animate-in fade-in slide-in-from-bottom-2 duration-700 origin-top">
          <div className="scale-[0.55] origin-top opacity-60 group-hover:opacity-100 group-hover:scale-[0.58] transition-[opacity,transform] duration-500">
            <MarkdownRenderer
              content={textPreview}
              previewMode={true}
              className="text-white prose-invert"
            />
          </div>
        </div>
      )}

      {/* ── Markdown grid card: plain text snippet (avoids rehype pipeline) ─ */}
      {thumbnailType === null && isMarkdown && textPreview && lazy && (
        <div className="absolute inset-0 p-4 font-mono text-[10px] leading-relaxed text-white/80 overflow-hidden select-none animate-in fade-in slide-in-from-bottom-2 duration-700 bg-black/5">
          <div className="line-clamp-10 whitespace-pre-wrap opacity-60">
            {textPreview}
          </div>
          <div className="absolute inset-x-0 bottom-0 h-16 bg-linear-to-t from-black/20 to-transparent" />
        </div>
      )}

      {/* ── QCM HTML Preview Card ─────────────────────────────────────────── */}
      {isQCM && (
        <div className="absolute inset-0 bg-violet-800 flex items-center justify-center p-4 overflow-hidden select-none animate-in fade-in duration-300">
          {/* Watermark */}
          <div className="absolute font-black text-6xl sm:text-7xl md:text-[100px] leading-none text-violet-950/40 -rotate-12 select-none pointer-events-none tracking-tighter">
            QCM
          </div>
          
          <div className="relative z-10 flex flex-col items-center justify-center w-full h-full text-center">
            <span className="text-[9px] sm:text-[10px] font-bold tracking-widest text-violet-300 uppercase mb-1.5 sm:mb-2 opacity-90">
              Questionnaire
            </span>
            <h3 className="text-white font-bold text-sm sm:text-base md:text-lg line-clamp-3 leading-snug max-w-[90%] drop-shadow-sm">
              {material.title || "QCM"}
            </h3>
          </div>
          
          {/* Inner border/glass effect */}
          <div className="absolute inset-1.5 sm:inset-2 border border-white/10 rounded-md sm:rounded-lg pointer-events-none" />
        </div>
      )}

      {/* ── Loading Overlay ──────────────────────────────────────────────── */}
      {loading && !url && !textPreview && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/5">
          <Loader2 className="h-6 w-6 animate-spin text-white/40" />
        </div>
      )}

      {/* Hover shine effect removed in lazy/grid mode — animating an opacity layer
          over a gradient on every card during hover transitions caused paint storms. */}
      {!lazy && (
        <div className="absolute inset-0 bg-linear-to-tr from-white/0 via-white/5 to-white/0 opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none" />
      )}
    </div>
  );
}
