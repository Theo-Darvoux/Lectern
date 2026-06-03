"use client";

import { useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api-client";

interface ThumbnailInfo {
  url: string;
  thumbnail_type: "webp" | "fallback";
}

interface DirectoryPreviewCollageProps {
  materialIds: string[];
}

export function DirectoryPreviewCollage({ materialIds }: DirectoryPreviewCollageProps) {
  const ids = materialIds.slice(0, 4);
  const [urls, setUrls] = useState<(string | null)[]>(Array(ids.length).fill(null));
  const [visible, setVisible] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) setVisible(true);
      },
      { rootMargin: "150px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!visible || ids.length === 0) return;
    let cancelled = false;
    Promise.all(
      ids.map((id) =>
        apiFetch<ThumbnailInfo>(`/materials/${id}/thumbnail`)
          .then((info) => info.url)
          .catch(() => null),
      ),
    ).then((results) => {
      if (!cancelled) setUrls(results);
    });
    return () => {
      cancelled = true;
    };
  }, [visible, ids.join(",")]); // ids is a derived primitive — joining is safe

  const gridClass = ids.length === 1 ? "grid-cols-1" : "grid-cols-2";

  return (
    <div ref={ref} className={`grid ${gridClass} gap-0.5 w-full h-full`}>
      {ids.map((id, i) => (
        <div key={id} className="relative overflow-hidden">
          {urls[i] ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={urls[i]!}
              alt=""
              className="w-full h-full object-cover"
              draggable={false}
            />
          ) : (
            <div className="w-full h-full animate-pulse bg-white/10" />
          )}
        </div>
      ))}
    </div>
  );
}
