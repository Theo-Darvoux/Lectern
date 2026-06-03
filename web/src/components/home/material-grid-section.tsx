"use client";

import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { MaterialCard } from "./material-card";
import { SectionHeader } from "./section-header";
import type { MaterialDetail } from "./types";

interface MaterialGridSectionProps {
  title: string;
  subtitle?: string;
  icon?: ReactNode;
  materials: MaterialDetail[];
  isLoading?: boolean;
  seeAllHref?: string;
  seeAllLabel?: string;
  emptyText: string;
  emptyIcon?: ReactNode;
  maxCards?: number;
  skeletonCount?: number;
}

function SkeletonCard() {
  return (
    <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
      <Skeleton className="aspect-4/3 w-full rounded-none" />
      <div className="space-y-2 p-3">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-3.5 w-3/4" />
        <Skeleton className="h-3 w-1/2" />
        <div className="flex gap-3 pt-1">
          <Skeleton className="h-3 w-10" />
          <Skeleton className="h-3 w-10" />
        </div>
      </div>
    </div>
  );
}

const GRID =
  "grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-3 2xl:grid-cols-4";

export function MaterialGridSection({
  title,
  subtitle,
  icon,
  materials,
  isLoading = false,
  seeAllHref,
  seeAllLabel,
  emptyText,
  emptyIcon,
  maxCards = 8,
  skeletonCount = 4,
}: MaterialGridSectionProps) {
  const visible = materials.slice(0, maxCards);

  return (
    <section aria-label={title}>
      <SectionHeader
        title={title}
        subtitle={subtitle}
        icon={icon}
        seeAllHref={seeAllHref}
        seeAllLabel={seeAllLabel}
      />

      <div className="mt-4">
        {isLoading ? (
          <div className={GRID}>
            {Array.from({ length: skeletonCount }).map((_, i) => (
              <SkeletonCard key={i} />
            ))}
          </div>
        ) : visible.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed bg-muted/20 py-10 text-center">
            {emptyIcon}
            <p className="text-sm text-muted-foreground">{emptyText}</p>
          </div>
        ) : (
          <div className={GRID}>
            {visible.map((material) => (
              <MaterialCard key={material.id} material={material} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
