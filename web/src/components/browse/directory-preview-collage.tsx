"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { File } from "lucide-react";
import { getMaterialThumbnail, subscribeMaterialThumbnail } from "@/lib/material-preview-source";
import { useInView } from "@/hooks/use-in-view";

type CellInfo = { url: string } | null | false;

/** A preview the listing already signed (null url: no generated WebP). */
export interface PreviewThumbnail {
  material_id: string;
  url: string | null;
}

interface DirectoryPreviewCollageProps {
  materialIds: string[];
  /** Signed URLs sent with the listing. Without them (or once one fails to
   *  load, e.g. an expired signature) each cell asks for its thumbnail. */
  thumbnails?: PreviewThumbnail[] | null;
}

function withCacheBust(url: string, cacheBust: number): string {
  if (cacheBust <= 0) return url;
  return `${url}${url.includes("?") ? "&" : "?"}_t=${cacheBust}`;
}

export function DirectoryPreviewCollage({ materialIds, thumbnails }: DirectoryPreviewCollageProps) {
  // Keyed by content so a new array with the same ids does not refetch.
  const idsKey = materialIds.slice(0, 4).join(",");
  const ids = useMemo(() => (idsKey ? idsKey.split(",") : []), [idsKey]);
  const [fetchedCells, setFetchedCells] = useState<CellInfo[]>(Array(ids.length).fill(null));
  const [failedCells, setFailedCells] = useState<Set<number>>(() => new Set());
  const [inlineFailed, setInlineFailed] = useState(false);
  const [cacheBust, setCacheBust] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const visible = useInView(ref);

  const inline = useMemo(() => {
    if (!thumbnails || inlineFailed) return null;
    const byId = new Map(thumbnails.map((thumb) => [thumb.material_id, thumb.url]));
    // A listing signed before a preview changed lacks the new id: fetch instead.
    if (!ids.every((id) => byId.has(id))) return null;
    return ids.map((id): CellInfo => {
      const url = byId.get(id);
      return url ? { url: withCacheBust(url, cacheBust) } : false;
    });
  }, [thumbnails, inlineFailed, ids, cacheBust]);

  useEffect(() => {
    return subscribeMaterialThumbnail((id, ts) => {
      if (ids.includes(id)) {
        setCacheBust(ts || Date.now());
      }
    });
  }, [ids]);

  useEffect(() => {
    if (inline || !visible || ids.length === 0) return;
    let cancelled = false;
    setFetchedCells(Array(ids.length).fill(null));
    Promise.all(
      ids.map((id) =>
        getMaterialThumbnail(id)
          .then((info): CellInfo => {
            // A "fallback" may be a PDF or video, which an <img> cannot show.
            if (!info || info.thumbnailType !== "webp") return false;
            return { url: withCacheBust(info.url, cacheBust) };
          })
          .catch((): CellInfo => false),
      ),
    ).then((results) => {
      if (!cancelled) setFetchedCells(results);
    });
    return () => {
      cancelled = true;
    };
  }, [inline, visible, ids, cacheBust]);

  const cells = inline ?? fetchedCells;
  const gridClass = ids.length === 1 ? "grid-cols-1" : "grid-cols-2";

  return (
    <div ref={ref} className={`grid ${gridClass} gap-0.5 w-full h-full`}>
      {ids.map((id, i) => {
        const cell = failedCells.has(i) ? false : cells[i];
        return (
          <div key={id} className="relative overflow-hidden">
            {cell === null || cell === undefined ? (
              <div className="w-full h-full animate-pulse bg-white/10" />
            ) : cell === false ? (
              <div className="w-full h-full flex items-center justify-center bg-white/5">
                <File className="h-6 w-6 text-white/40" />
              </div>
            ) : (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={cell.url}
                alt=""
                className="w-full h-full object-cover"
                draggable={false}
                loading="lazy"
                onError={() => {
                  if (inline) setInlineFailed(true);
                  else setFailedCells((prev) => new Set(prev).add(i));
                }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
