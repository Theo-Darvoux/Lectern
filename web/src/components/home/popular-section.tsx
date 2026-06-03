"use client";

import Link from "next/link";
import { useState } from "react";
import { Flame, ArrowRight } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { MaterialCard } from "./material-card";
import type { MaterialDetail } from "./types";
import { useTranslations } from "next-intl";

type Period = "today" | "14d";

interface PopularSectionProps {
  today: MaterialDetail[];
  fortnight: MaterialDetail[];
  isLoading?: boolean;
}

const MAX_CARDS = 8;
const GRID =
  "grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-3 2xl:grid-cols-4";

function SkeletonCard() {
  return (
    <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
      <Skeleton className="aspect-4/3 w-full rounded-none" />
      <div className="space-y-2 p-3">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-3.5 w-3/4" />
        <Skeleton className="h-3 w-1/2" />
      </div>
    </div>
  );
}

export function PopularSection({
  today,
  fortnight,
  isLoading = false,
}: PopularSectionProps) {
  const t = useTranslations("Home");
  const [period, setPeriod] = useState<Period>("today");

  const materials = period === "today" ? today : fortnight;
  const visible = materials.slice(0, MAX_CARDS);
  const seeAllHref = `/popular?period=${period}`;

  return (
    <section aria-label={t("popularTitle")}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-lg font-semibold leading-tight tracking-tight sm:text-xl">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-orange-500/10 text-orange-500">
            <Flame className="h-4 w-4" />
          </span>
          {t("popularTitle")}
        </h2>

        <div className="flex items-center gap-2">
          <Tabs value={period} onValueChange={(v) => setPeriod(v as Period)}>
            <TabsList className="h-8">
              <TabsTrigger value="today" className="text-xs">
                {t("periodToday")}
              </TabsTrigger>
              <TabsTrigger value="14d" className="text-xs">
                {t("periodFortnight")}
              </TabsTrigger>
            </TabsList>
          </Tabs>

          <Link
            href={seeAllHref}
            className="hidden shrink-0 items-center gap-1 rounded-md px-2 py-1 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground sm:inline-flex"
          >
            {t("seeAll")}
            <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </div>
      </div>

      <div className="mt-4">
        {isLoading ? (
          <div className={GRID}>
            {Array.from({ length: 4 }).map((_, i) => (
              <SkeletonCard key={i} />
            ))}
          </div>
        ) : visible.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed bg-muted/20 py-10 text-center">
            <Flame className="h-8 w-8 text-muted-foreground/30" />
            <p className="text-sm text-muted-foreground">{t("nothingHereYet")}</p>
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
