"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { File } from "lucide-react";
import { getMaterialThumbnail, subscribeMaterialThumbnail } from "@/lib/material-preview-source";
import { useInView } from "@/hooks/use-in-view";

type CellInfo = { url: string } | null | false;

interface DirectoryPreviewCollageProps {
  materialIds: string[];
}

export function DirectoryPreviewCollage({ materialIds }: DirectoryPreviewCollageProps) {
  // Keyed by content so a new array with the same ids does not refetch.
  const idsKey = materialIds.slice(0, 4).join(",");
  const ids = useMemo(() => (idsKey ? idsKey.split(",") : []), [idsKey]);
  const [cells, setCells] = useState<CellInfo[]>(Array(ids.length).fill(null));
  const [cacheBust, setCacheBust] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const visible = useInView(ref);

  useEffect(() => {
    return subscribeMaterialThumbnail((id, ts) => {
      if (ids.includes(id)) {
        setCacheBust(ts || Date.now());
      }
    });
  }, [ids]);

  useEffect(() => {
    if (!visible || ids.length === 0) return;
    let cancelled = false;
    setCells(Array(ids.length).fill(null));
    Promise.all(
      ids.map((id) =>
        getMaterialThumbnail(id)
          .then((info): CellInfo => {
            // A "fallback" may be a PDF or video, which an <img> cannot show.
            if (!info || info.thumbnailType !== "webp") return false;
            let finalUrl = info.url;
            if (cacheBust > 0) {
              finalUrl = `${finalUrl}${finalUrl.includes("?") ? "&" : "?"}_t=${cacheBust}`;
            }
            return { url: finalUrl };
          })
          .catch((): CellInfo => false),
      ),
    ).then((results) => {
      if (!cancelled) setCells(results);
    });
    return () => {
      cancelled = true;
    };
  }, [visible, ids, cacheBust]);

  const gridClass = ids.length === 1 ? "grid-cols-1" : "grid-cols-2";

  return (
    <div ref={ref} className={`grid ${gridClass} gap-0.5 w-full h-full`}>
      {ids.map((id, i) => {
        const cell = cells[i];
        return (
          <div key={id} className="relative overflow-hidden">
            {cell === null ? (
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
                onError={() =>
                  setCells((prev) => {
                    const next = [...prev];
                    next[i] = false;
                    return next;
                  })
                }
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
