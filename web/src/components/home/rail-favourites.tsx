"use client";

import Link from "next/link";
import { Bookmark, Eye, ThumbsUp } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { MaterialPreview } from "./material-preview";
import { getMaterialBrowsePath } from "./file-type-display";
import { SectionHeader } from "./section-header";
import type { MaterialDetail } from "./types";
import { useTranslations } from "next-intl";

const MAX_ITEMS = 6;

function FavouriteRow({ material }: { material: MaterialDetail }) {
  return (
    <Link
      href={getMaterialBrowsePath(material)}
      className="group flex items-center gap-3 rounded-lg p-2 transition-colors hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none"
    >
      <div className="relative h-12 w-12 shrink-0 overflow-hidden rounded-md border bg-muted">
        <MaterialPreview material={material} />
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium leading-snug group-hover:underline">
          {material.title}
        </p>
        <div className="mt-0.5 flex items-center gap-3 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <Eye className="h-3 w-3" />
            {material.total_views.toLocaleString()}
          </span>
          <span className="flex items-center gap-1">
            <ThumbsUp className="h-3 w-3" />
            {material.like_count.toLocaleString()}
          </span>
        </div>
      </div>
    </Link>
  );
}

export function RailFavourites({
  materials,
  isLoading = false,
}: {
  materials: MaterialDetail[];
  isLoading?: boolean;
}) {
  const t = useTranslations("Home");
  if (!isLoading && materials.length === 0) return null;

  const visible = materials.slice(0, MAX_ITEMS);

  return (
    <section aria-label={t("yourFavourites")}>
      <SectionHeader
        title={t("yourFavourites")}
        icon={<Bookmark className="h-4 w-4" />}
        seeAllHref="/profile"
        seeAllLabel={t("viewAll")}
      />
      <div className="mt-3 rounded-xl border bg-card p-1.5 shadow-sm">
        {isLoading
          ? Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="flex items-center gap-3 p-2">
                <Skeleton className="h-12 w-12 shrink-0 rounded-md" />
                <div className="flex-1 space-y-1.5">
                  <Skeleton className="h-3.5 w-3/4" />
                  <Skeleton className="h-3 w-1/3" />
                </div>
              </div>
            ))
          : visible.map((m) => <FavouriteRow key={m.id} material={m} />)}
      </div>
    </section>
  );
}
