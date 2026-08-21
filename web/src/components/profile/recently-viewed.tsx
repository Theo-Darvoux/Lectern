"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Clock } from "lucide-react";
import { useTranslations } from "next-intl";
import { MaterialCard } from "@/components/home/material-card";
import {
  PROFILE_MATERIAL_GRID,
  type ProfileMaterialSummary,
  toProfileMaterialDetail,
} from "@/components/profile/profile-material";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { apiFetch } from "@/lib/api-client";

const PAGE_SIZE = 8;

interface RecentMaterial extends ProfileMaterialSummary {
  title: string;
  slug: string;
  type: string;
  directory_id: string;
}

function SkeletonCard() {
  return (
    <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
      <Skeleton className="aspect-4/3 w-full rounded-none" />
      <div className="space-y-2 p-3">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-3 w-2/3" />
      </div>
    </div>
  );
}

export function RecentlyViewed() {
  const t = useTranslations("Profile");
  const [materials, setMaterials] = useState<RecentMaterial[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);

  useEffect(() => {
    apiFetch<RecentMaterial[]>("/users/me/recently-viewed")
      .then(setMaterials)
      .catch(() => setMaterials([]))
      .finally(() => setLoading(false));
  }, []);

  const pages = Math.max(1, Math.ceil(materials.length / PAGE_SIZE));
  const visibleMaterials = useMemo(
    () => materials.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE),
    [materials, page],
  );

  if (loading) {
    return (
      <div className={PROFILE_MATERIAL_GRID} data-recent-material-grid>
        {Array.from({ length: PAGE_SIZE }, (_, index) => <SkeletonCard key={index} />)}
      </div>
    );
  }

  if (materials.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-20 text-center">
        <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-muted">
          <Clock className="h-5 w-5 text-muted-foreground/50" />
        </div>
        <p className="text-sm font-medium text-muted-foreground">{t("noRecentHistory")}</p>
        <p className="mt-1 text-xs text-muted-foreground/80">{t("recentHistoryDesc")}</p>
      </div>
    );
  }

  return (
    <div>
      <div className={PROFILE_MATERIAL_GRID} data-recent-material-grid>
        {visibleMaterials.map((material) => (
          <MaterialCard
            key={material.id}
            material={toProfileMaterialDetail(material)}
          />
        ))}
      </div>

      <div className="mt-5 flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between" data-profile-pagination>
        <p className="text-xs text-muted-foreground">
          {t("totalMaterials", { count: materials.length })}
        </p>
        {pages > 1 && (
          <div className="flex items-center gap-2 self-end sm:self-auto">
            <Button
              variant="outline"
              size="icon"
              disabled={page <= 1}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              className="h-8 w-8"
              aria-label={t("previousPage")}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <span className="min-w-14 text-center text-xs font-medium tabular-nums text-muted-foreground">
              {page} / {pages}
            </span>
            <Button
              variant="outline"
              size="icon"
              disabled={page >= pages}
              onClick={() => setPage((current) => Math.min(pages, current + 1))}
              className="h-8 w-8"
              aria-label={t("nextPage")}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
