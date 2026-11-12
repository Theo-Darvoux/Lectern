"use client";

import { useCallback, useEffect, useState, useRef } from "react";
import { getMaterialFileUrl } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import { getFileTypeStyle } from "./file-type-display";
import { LanguageLogo } from "./language-logo";
import type { MaterialDetail, MaterialVersionInfo } from "./types";
import { Loader2 } from "lucide-react";
import { useInView } from "@/hooks/use-in-view";
import { MIME_QCM } from "@/lib/file-utils";
import { getCodeLanguage, type CodeLanguage } from "@/lib/code-languages";
import { createPdfWorker, loadPdfjs } from "@/lib/pdf-worker";
import {
  getMaterialThumbnail,
  refreshMaterialThumbnail,
  subscribeMaterialThumbnail,
} from "@/lib/material-preview-source";

import { isExternalUrl, getInternalBrowsePath } from "@/lib/url-utils";
import { fetchBrowsePath } from "@/lib/browse-prefetch";

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
    // Each thumbnail gets its own module worker, handed to getDocument explicitly
    // (rather than the global GlobalWorkerOptions.workerSrc string). pdf.js's
    // workerSrc path keeps a module-global "worker disabled" flag that a single
    // failed worker flips for the whole page, poisoning every later PDF with the
    // ESM-incompatible fake worker (`globalThis.pdfjsLib is undefined`). An
    // explicit per-document worker bypasses that flag. We own its teardown.
    let doc: Awaited<ReturnType<typeof import("pdfjs-dist").getDocument>["promise"]> | undefined;
    let rawWorker: Worker | undefined;
    let pdfWorker: import("pdfjs-dist").PDFWorker | undefined;
    const dispose = () => {
      const d = doc, pw = pdfWorker, rw = rawWorker;
      doc = pdfWorker = rawWorker = undefined;
      // Let the document tear down (it tells the worker to wind up) before we
      // destroy the wrapper and terminate the worker, so an in-flight teardown
      // message doesn't race the terminate() and surface as console noise.
      Promise.resolve(d?.destroy()).finally(() => {
        pw?.destroy();
        rw?.terminate();
      });
    };
    (async () => {
      try {
        const pdfjs = await loadPdfjs();
        if (cancelled) return;

        rawWorker = createPdfWorker();
        pdfWorker = pdfjs.PDFWorker.fromPort({ port: rawWorker });
        const task = pdfjs.getDocument({ url, worker: pdfWorker });
        doc = await task.promise;
        if (cancelled) return;
        const page = await doc.getPage(1);
        const canvas = canvasRef.current;
        if (cancelled || !canvas) return;
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        const baseViewport = page.getViewport({ scale: 1 });
        const scale = (width || 300) / baseViewport.width;
        const viewport = page.getViewport({ scale: scale * dpr });
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        canvas.style.width = "100%";
        await page.render({ canvasContext: ctx, viewport }).promise;
        if (cancelled) return;
        onReady();
      } catch (err) {
        if (!cancelled && (err as Error)?.name !== "AbortException") onError();
      } finally {
        // The first page is rasterised onto the canvas; the doc + worker are no
        // longer needed, so release them immediately rather than holding a worker
        // per visible thumbnail until unmount.
        dispose();
      }
    })();
    return () => { cancelled = true; dispose(); };
  }, [url, width, onReady, onError]);

  return <canvas ref={canvasRef} className="block w-full" />;
}

// A signed worker/presigned URL can transiently fail to load (cold edge cache,
// brief 5xx, flaky connection) and an <img> that errors never re-attempts on its
// own. The first failure re-signs the URL (it may have expired in a long-lived
// listing); later ones retry with backoff, cache-busting the request so the
// browser doesn't replay its cached failure. The extra query param is safe: the
// worker only verifies `token` and strips the query string from its cache key.
const MAX_IMG_RETRIES = 3;

interface PreviewSource {
  url: string;
  /** "webp": generated thumbnail. "fallback": the original image, video or PDF. */
  type: "webp" | "fallback";
}

/**
 * Preview embedded by the listing that returned this material.
 * undefined: not resolved there (search results, link targets), so ask the API.
 * null: the file has no preview.
 */
function embeddedPreview(version: MaterialVersionInfo | null | undefined): PreviewSource | null | undefined {
  if (!version?.thumbnail_type) return undefined;
  if (version.thumbnail_type === "none" || !version.thumbnail_url) return null;
  return { url: version.thumbnail_url, type: version.thumbnail_type };
}

function withQuery(url: string, key: string, value: number): string {
  return `${url}${url.includes("?") ? "&" : "?"}${key}=${value}`;
}

function canHover(event: React.PointerEvent): boolean {
  return event.pointerType === "mouse" || event.pointerType === "pen";
}

/** Generic card for text-like files: the language's color and logo. */
function CodeCard({ language, title, ext }: { language: CodeLanguage; title: string; ext: string }) {
  const watermarkSize =
    language.mark.length <= 2
      ? "text-5xl @min-[160px]:text-7xl @min-[260px]:text-[100px]"
      : language.mark.length <= 4
        ? "text-3xl @min-[160px]:text-5xl @min-[260px]:text-7xl"
        : "text-xl @min-[160px]:text-4xl @min-[260px]:text-5xl";
  const label = language.name ?? (ext ? `.${ext}` : language.mark);
  return (
    <div
      className={cn(
        "@container absolute inset-0 flex items-center justify-center overflow-hidden p-4 select-none",
        language.darkText ? "text-slate-900" : "text-white",
      )}
      style={{ backgroundColor: language.color }}
    >
      <div className="absolute inset-0 bg-linear-to-br from-white/10 to-black/20 pointer-events-none" />
      {language.icon ? (
        <>
          {/* Small cards: the logo alone, centered. */}
          <LanguageLogo icon={language.icon} className="relative size-1/2 max-h-16 max-w-16 @min-[120px]:hidden" />
          {/* Larger cards: an oversized faded logo behind the label and title. */}
          <LanguageLogo
            icon={language.icon}
            className="absolute -right-[12%] -bottom-[18%] hidden size-[70%] -rotate-12 opacity-20 pointer-events-none @min-[120px]:block"
          />
        </>
      ) : (
        <div
          className={cn(
            "absolute font-mono font-black leading-none tracking-tighter text-black/25 -rotate-12 pointer-events-none",
            watermarkSize,
          )}
        >
          {language.mark}
        </div>
      )}
      <div className="relative z-10 hidden w-full flex-col items-center text-center @min-[120px]:flex">
        {language.icon && (
          <LanguageLogo icon={language.icon} className="mb-2 size-8 @min-[180px]:size-10 drop-shadow-sm" />
        )}
        <span className="mb-1.5 font-mono text-[10px] font-bold uppercase tracking-widest opacity-80">{label}</span>
        <h3
          className={cn(
            "hidden max-w-[90%] text-sm font-bold leading-snug line-clamp-3 @min-[180px]:block @min-[260px]:text-base",
            !language.darkText && "drop-shadow-sm",
          )}
        >
          {title}
        </h3>
      </div>
      <div
        className={cn(
          "absolute inset-1.5 hidden rounded-md border pointer-events-none @min-[120px]:block",
          language.darkText ? "border-black/10" : "border-white/10",
        )}
      />
    </div>
  );
}

interface MaterialPreviewProps {
  material: MaterialDetail;
  className?: string;
  /** When true, defers loading until the card scrolls near the viewport. */
  lazy?: boolean;
}

export function MaterialPreview({ material, className, lazy }: MaterialPreviewProps) {
  const [source, setSource] = useState<PreviewSource | null>(null);
  const [loading, setLoading] = useState(false);
  const [videoLoaded, setVideoLoaded] = useState(false);
  const [hoverVideoUrl, setHoverVideoUrl] = useState<string | null>(null);
  const [hovering, setHovering] = useState(false);
  const [pdfReady, setPdfReady] = useState(false);
  const handlePdfReady = useCallback(() => setPdfReady(true), []);
  const handlePdfError = useCallback(() => setPdfReady(false), []);
  const [imgBust, setImgBust] = useState(0);
  const [cacheBust, setCacheBust] = useState(0);
  const [resolvedTargetMat, setResolvedTargetMat] = useState<MaterialDetail | null>(null);
  const imgAttemptRef = useRef(0);
  const urlRefreshedRef = useRef(false);
  const imgRetryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(300);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    setSource(null);
    setResolvedTargetMat(null);
    setVideoLoaded(false);
    setHoverVideoUrl(null);
    setPdfReady(false);
  }, [material.id]);

  const resolvedTargetId = resolvedTargetMat?.id;
  useEffect(() => {
    return subscribeMaterialThumbnail((id, ts) => {
      if (id === String(material.id) || (resolvedTargetId && id === String(resolvedTargetId))) {
        setCacheBust(ts || Date.now());
      }
    });
  }, [material.id, resolvedTargetId]);

  const inView = useInView(containerRef);
  // In lazy mode, gate loading on viewport intersection. In non-lazy mode we
  // always load — collapsing to a constant `true` so the fetch effect below does
  // NOT re-run when `inView` later flips false→true. A re-run would reset
  // `pdfReady` to false while the PDF URL stays unchanged (same URL), so the
  // canvas onReady never re-fires and a rendered PDF preview fades back to
  // opacity-0 ("renders then disappears").
  const shouldLoad = lazy ? inView : true;

  const targetUrl = String(
    (material.metadata as Record<string, unknown> | undefined)?.url ??
    (material.metadata as Record<string, unknown> | undefined)?.link ??
    ""
  ).trim();

  const isLink = material.type === "link" || !!targetUrl;
  const isExternal = isExternalUrl(targetUrl);
  const isInternal = isLink && !isExternal;
  const internalBrowsePath = isInternal ? getInternalBrowsePath(targetUrl) : null;
  const isLinkWithoutBrowsePath = isLink && !internalBrowsePath;

  const effectiveMat = resolvedTargetMat || material;
  const versionInfo = effectiveMat.current_version_info;
  const fileName = versionInfo?.file_name ?? "";
  const mimeType = versionInfo?.file_mime_type ?? "";
  const ext = fileName.includes(".") ? fileName.split(".").pop()!.toLowerCase() : "";

  const isVideo = mimeType.startsWith("video/") || /\.(mp4|webm)$/i.test(fileName);
  const isPDF = mimeType === "application/pdf" || fileName.toLowerCase().endsWith(".pdf");
  const isOffice = mimeType.includes("ms-") || mimeType.includes("officedocument") || /\.(docx|xlsx|pptx)$/i.test(fileName);
  const isQCM = mimeType === MIME_QCM || fileName.toLowerCase().endsWith(".qcm");
  // Links show their own card until the target resolves.
  const codeLanguage = !isQCM && (!isLink || resolvedTargetMat) ? getCodeLanguage(fileName, mimeType) : null;

  // Track container width for PDF canvas sizing — only needed when the PDF thumbnail will render.
  useEffect(() => {
    if (!isPDF || lazy) return;
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => {
      if (entry) setContainerWidth(entry.contentRect.width || 300);
    });
    ro.observe(el);
    setContainerWidth(el.clientWidth || 300);
    return () => ro.disconnect();
  }, [isPDF, lazy]);

  useEffect(() => {
    // In lazy mode, wait until the card is near the viewport before fetching.
    if (!shouldLoad || isLinkWithoutBrowsePath) return;

    let mounted = true;
    setPdfReady(false);
    urlRefreshedRef.current = false;

    async function fetchPreview() {
      let activeMat: MaterialDetail = material;

      // If this is an internal link pointing to a browse path, resolve target material first
      if (isLink && internalBrowsePath !== null) {
        try {
          const browseRes = (await fetchBrowsePath(internalBrowsePath)) as {
            type?: string;
            material?: MaterialDetail;
          } | null;
          if (!mounted) return;
          if (browseRes?.type !== "material" || !browseRes.material) return;
          activeMat = browseRes.material;
          setResolvedTargetMat(browseRes.material);
        } catch {
          return;
        }
      }

      const activeVersion = activeMat.current_version_info;
      const activeName = activeVersion?.file_name ?? "";
      const activeMime = activeVersion?.file_mime_type ?? "";
      // Text-like files and QCMs have a generic card and no thumbnail.
      if (activeMime === MIME_QCM || getCodeLanguage(activeName, activeMime)) return;

      // After a regeneration the listing's embedded URL is stale: ask the API.
      const embedded = cacheBust === 0 ? embeddedPreview(activeVersion) : undefined;
      if (embedded !== undefined) {
        setSource(embedded);
        return;
      }

      setLoading(true);
      try {
        const thumbnail = await getMaterialThumbnail(String(activeMat.id));
        if (mounted && thumbnail.url && thumbnail.thumbnailType) {
          setSource({ url: thumbnail.url, type: thumbnail.thumbnailType });
        }
      } catch {
        // 404: the file type has no preview; the icon card stays.
      } finally {
        if (mounted) setLoading(false);
      }
    }

    const timer = setTimeout(fetchPreview, 100);
    return () => {
      mounted = false;
      clearTimeout(timer);
    };
    // Keyed on the id and the derived flags: parents pass a fresh `material`
    // object on each render, and refetching for the same id would flash the preview.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    material.id,
    shouldLoad,
    cacheBust,
    isLink,
    internalBrowsePath,
    isLinkWithoutBrowsePath,
  ]);

  // Reset the image-retry counter whenever the source URL changes, and clear any
  // pending retry timer on unmount.
  const sourceUrl = source?.url ?? null;
  useEffect(() => {
    imgAttemptRef.current = 0;
    setImgBust(0);
    return () => {
      if (imgRetryTimer.current) {
        clearTimeout(imgRetryTimer.current);
        imgRetryTimer.current = null;
      }
    };
  }, [sourceUrl]);

  const handleImgError = () => {
    const activeId = String(effectiveMat.id);
    if (!urlRefreshedRef.current) {
      urlRefreshedRef.current = true;
      refreshMaterialThumbnail(activeId)
        .then((thumbnail) => {
          if (thumbnail.thumbnailType && thumbnail.url !== sourceUrl) {
            setSource({ url: thumbnail.url, type: thumbnail.thumbnailType });
          } else {
            handleImgError();
          }
        })
        .catch(() => setSource(null));
      return;
    }
    if (imgRetryTimer.current || imgAttemptRef.current >= MAX_IMG_RETRIES) return;
    const delay = 500 * 2 ** imgAttemptRef.current;
    imgRetryTimer.current = setTimeout(() => {
      imgRetryTimer.current = null;
      imgAttemptRef.current += 1;
      setImgBust(imgAttemptRef.current); // bump cache-buster → forces <img> reload
    }, delay);
  };

  const imgSrc = (() => {
    if (!sourceUrl) return null;
    let finalUrl = sourceUrl;
    if (cacheBust > 0) finalUrl = withQuery(finalUrl, "_t", cacheBust);
    if (imgBust > 0) finalUrl = withQuery(finalUrl, "_r", imgBust);
    return finalUrl;
  })();

  const effectiveType = resolvedTargetMat
    ? resolvedTargetMat.type
    : isLink
      ? (isInternal ? "internal_link" : "link")
      : material.type;

  const { gradient, iconColorClass, Icon, style } = getFileTypeStyle(
    resolvedTargetMat ? fileName : (isLink ? null : fileName),
    resolvedTargetMat ? mimeType : (isLink ? null : mimeType),
    effectiveType,
  );

  // Videos with a generated thumbnail play on hover: the file URL is only
  // requested the first time the pointer enters. Without one, the original
  // file is already the source and doubles as the poster frame.
  const videoSrc = isVideo ? (source?.type === "fallback" ? source.url : hoverVideoUrl) : null;
  const hasWebpPoster = source?.type === "webp";

  const handlePointerEnter = (event: React.PointerEvent) => {
    if (!isVideo || !canHover(event)) return;
    setHovering(true);
    if (hasWebpPoster && !hoverVideoUrl) {
      getMaterialFileUrl(String(effectiveMat.id))
        .then((url) => setHoverVideoUrl(url))
        .catch(() => {});
    } else {
      videoRef.current?.play().catch(() => {});
    }
  };

  const handlePointerLeave = () => {
    if (!isVideo) return;
    setHovering(false);
    const video = videoRef.current;
    if (!video) return;
    video.pause();
    if (!hasWebpPoster) seekToPosterFrame(video);
  };

  const seekToPosterFrame = (video: HTMLVideoElement) => {
    const duration = video.duration;
    if (isFinite(duration) && duration > 0) {
      video.currentTime = Math.max(Math.min(duration * 0.1, 2), 0.5);
    }
  };

  const handleVideoLoaded = () => {
    const video = videoRef.current;
    if (!video) return;
    if (hasWebpPoster) {
      if (hovering) video.play().catch(() => {});
    } else {
      seekToPosterFrame(video);
    }
    setVideoLoaded(true);
  };

  // An <img> renders the generated WebP for every type, and the original file
  // for images. Fallback videos use <video>; fallback PDFs use pdf.js.
  const showAsImg = !!source && (source.type === "webp" || (!isVideo && !isPDF));

  // PDF fallback: raw PDF file returned by server → render first page with pdf.js.
  // Disabled in lazy/grid mode — instantiating pdf.js per card is too expensive.
  const showAsPdf = !!source && isPDF && source.type === "fallback" && !lazy;

  const showVideo = !!videoSrc && videoLoaded && (!hasWebpPoster || hovering);

  const showContent =
    showAsImg ||
    showAsPdf ||
    showVideo ||
    !!codeLanguage ||
    isQCM;

  return (
    <div
      ref={containerRef}
      className={cn(
        "relative w-full h-full flex items-center justify-center overflow-hidden bg-linear-to-br",
        gradient,
        className
      )}
      style={style}
      onPointerEnter={handlePointerEnter}
      onPointerLeave={handlePointerLeave}
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

      {/* ── Generated WebP thumbnail or native image ─────────────────────── */}
      {showAsImg && imgSrc && (
        /* eslint-disable-next-line @next/next/no-img-element */
        <img
          src={imgSrc}
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
            url={source.url}
            width={containerWidth}
            onReady={handlePdfReady}
            onError={handlePdfError}
          />
          {/* Gradient overlay so it blends into the card gradient */}
          <div className="absolute inset-x-0 bottom-0 h-1/3 bg-gradient-to-t from-black/30 to-transparent pointer-events-none" />
        </div>
      )}

      {/* ── Video: poster frame without a thumbnail, hover playback with one ── */}
      {videoSrc && (
        <video
          ref={videoRef}
          src={videoSrc}
          muted
          loop
          playsInline
          preload="metadata"
          onLoadedData={handleVideoLoaded}
          className={cn(
            "absolute inset-0 h-full w-full object-cover transition-opacity duration-500",
            showVideo ? "opacity-100" : "opacity-0"
          )}
        />
      )}

      {/* ── Generic card for code, markup and plain text ─────────────────── */}
      {codeLanguage && <CodeCard language={codeLanguage} title={effectiveMat.title || material.title} ext={ext} />}

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
      {loading && !source && (
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
