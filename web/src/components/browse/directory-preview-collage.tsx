"use client";

import { useEffect, useRef, useState } from "react";
import { File } from "lucide-react";
import { getMaterialThumbnail } from "@/lib/material-preview-source";
import { useInView } from "@/hooks/use-in-view";

type CellInfo = { url: string; type: "webp" | "fallback" } | null | false;

interface DirectoryPreviewCollageProps {
  materialIds: string[];
}

export function DirectoryPreviewCollage({ materialIds }: DirectoryPreviewCollageProps) {
  const ids = materialIds.slice(0, 4);
  const [cells, setCells] = useState<CellInfo[]>(Array(ids.length).fill(null));
  const ref = useRef<HTMLDivElement>(null);
  const visible = useInView(ref);

  useEffect(() => {
    if (!visible || ids.length === 0) return;
    let cancelled = false;
    setCells(Array(ids.length).fill(null));
    Promise.all(
      ids.map((id) =>
        getMaterialThumbnail(id)
          .then((info): CellInfo =>
            info && info.thumbnailType
              ? { url: info.url, type: info.thumbnailType }
              : false,
          )
          .catch((): CellInfo => false),
      ),
    ).then((results) => {
      if (!cancelled) setCells(results);
    });
    return () => {
      cancelled = true;
    };
  }, [visible, ids.join(",")]); // ids is a derived primitive — joining is safe

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
                onError={
                  cell.type === "fallback"
                    ? () =>
                        setCells((prev) => {
                          const next = [...prev];
                          next[i] = false;
                          return next;
                        })
                    : undefined
                }
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
