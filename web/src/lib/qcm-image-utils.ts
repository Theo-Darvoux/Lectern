// QCM image helpers — self-contained, embedded images.
//
// Images are stored in `QCMFile.images` (id → data URL) and referenced from the
// markdown text fields as `![alt](qcmimg:<id>)`. They are re-encoded through a
// canvas on the client, which downscales them and strips any non-pixel payload
// (EXIF, trailing data), so no server-side scanning of the raw upload is needed.

import {
  QCM_IMAGE_MAX_CHARS,
  QCM_IMAGE_MAX_DIMENSION,
  QCM_IMAGE_REF_PREFIX,
} from "./qcm-types";
import type { QCMFile } from "./qcm-types";

/** Thrown when an image cannot be embedded; `code` lets the UI pick a message. */
export class QcmImageError extends Error {
  code: "not-image" | "decode" | "too-large";
  constructor(code: QcmImageError["code"]) {
    super(code);
    this.name = "QcmImageError";
    this.code = code;
  }
}

export function generateQcmImageId(): string {
  const uuid =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID().replace(/-/g, "")
      : Math.random().toString(36).slice(2) + Date.now().toString(36);
  return `img_${uuid.slice(0, 12)}`;
}

/** Build the markdown ref token for an image id. */
export function qcmImageRef(id: string): string {
  return `${QCM_IMAGE_REF_PREFIX}${id}`;
}

/**
 * Resolve a markdown image `src` to something renderable.
 * - `qcmimg:<id>` → the embedded data URL (or null if missing)
 * - `data:` / `http(s):` → passed through unchanged
 * - anything else → null (QCM images are self-contained)
 */
export function resolveQcmImageSrc(
  src: string | undefined,
  images: Record<string, string> | undefined,
): string | null {
  if (!src) return null;
  if (src.startsWith(QCM_IMAGE_REF_PREFIX)) {
    const id = src.slice(QCM_IMAGE_REF_PREFIX.length);
    return images?.[id] ?? null;
  }
  if (src.startsWith("data:image/")) return src;
  if (src.startsWith("http://") || src.startsWith("https://")) return src;
  return null;
}

/**
 * `urlTransform` for react-markdown that preserves QCM image refs.
 *
 * react-markdown's default `urlTransform` sanitizes URLs and strips any
 * unrecognized protocol — including our custom `qcmimg:` scheme — to an empty
 * string before the `img` renderer runs. Returning `qcmimg:` (and embedded
 * `data:image/`) URLs unchanged lets {@link resolveQcmImageSrc} resolve them.
 */
export function qcmImageUrlTransform(
  url: string,
  key: string,
  defaultTransform: (url: string) => string,
): string {
  if (url.startsWith(QCM_IMAGE_REF_PREFIX)) return url;
  if (key === "src" && url.startsWith("data:image/")) return url;
  return defaultTransform(url);
}

const REF_RE = /qcmimg:([A-Za-z0-9_-]+)/g;

/** Every image id referenced from the QCM's text fields. */
export function collectReferencedQcmImageIds(qcm: QCMFile): Set<string> {
  const ids = new Set<string>();
  const scan = (text: string | undefined) => {
    if (!text) return;
    for (const m of text.matchAll(REF_RE)) ids.add(m[1]);
  };
  for (const ch of qcm.chapters) {
    for (const q of ch.questions) {
      scan(q.text);
      scan(q.explanation);
      for (const a of q.answers) scan(a.text);
    }
  }
  return ids;
}

/** Drop embedded images no longer referenced by any text field. */
export function pruneQcmImages(qcm: QCMFile): QCMFile {
  if (!qcm.images || Object.keys(qcm.images).length === 0) {
    const { images: _omit, ...rest } = qcm;
    return rest;
  }
  const used = collectReferencedQcmImageIds(qcm);
  const kept: Record<string, string> = {};
  for (const [id, data] of Object.entries(qcm.images)) {
    if (used.has(id)) kept[id] = data;
  }
  if (Object.keys(kept).length === 0) {
    const { images: _omit, ...rest } = qcm;
    return rest;
  }
  return { ...qcm, images: kept };
}

function loadImageElement(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new QcmImageError("decode"));
    };
    img.src = url;
  });
}

/**
 * Validate, downscale and re-encode an image file into a `data:` URL suitable
 * for embedding in a QCM. PNG/transparent sources keep PNG; everything else is
 * encoded as JPEG, dropping quality as needed to stay under the size cap.
 */
export async function processQcmImageFile(file: File): Promise<string> {
  if (!file.type.startsWith("image/")) throw new QcmImageError("not-image");

  const img = await loadImageElement(file);
  const natural = Math.max(img.naturalWidth, img.naturalHeight) || 1;
  const scale = Math.min(1, QCM_IMAGE_MAX_DIMENSION / natural);
  const width = Math.max(1, Math.round(img.naturalWidth * scale));
  const height = Math.max(1, Math.round(img.naturalHeight * scale));

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new QcmImageError("decode");
  ctx.drawImage(img, 0, 0, width, height);

  // Keep transparency for PNG-like sources; otherwise prefer compact JPEG.
  const keepPng = file.type === "image/png" || file.type === "image/gif" || file.type === "image/webp";
  let dataUrl = canvas.toDataURL(keepPng ? "image/png" : "image/jpeg", 0.85);

  // PNG that overflows the budget falls back to JPEG.
  if (dataUrl.length > QCM_IMAGE_MAX_CHARS && keepPng) {
    dataUrl = canvas.toDataURL("image/jpeg", 0.85);
  }
  // Step quality down until it fits.
  let quality = 0.85;
  while (dataUrl.length > QCM_IMAGE_MAX_CHARS && quality > 0.4) {
    quality -= 0.15;
    dataUrl = canvas.toDataURL("image/jpeg", quality);
  }
  if (dataUrl.length > QCM_IMAGE_MAX_CHARS) throw new QcmImageError("too-large");

  return dataUrl;
}
