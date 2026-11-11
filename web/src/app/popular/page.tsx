"use client";

import { useMemo, useState, Suspense } from "react";
import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import { useSearchParams, useRouter } from "next/navigation";
import { Loader2, LayoutGrid, TrendingUp } from "lucide-react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { MaterialCard } from "@/components/home/material-card";
import { SectionHeader } from "@/components/home/section-header";
import { QueryError } from "@/components/query-error";
import { apiFetch } from "@/lib/api-client";
import { toast } from "sonner";
import { useTranslations } from "next-intl";
import { useConfigStore } from "@/lib/stores";
import type { MaterialDetail } from "@/components/home/types";
import { cn } from "@/lib/utils";

type Period = "today" | "14d";

const LIMIT = 20;

// ─────────────────────────────────────────────
// Skeleton grid while loading
// ─────────────────────────────────────────────
function SkeletonGrid() {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
      {Array.from({ length: 12 }).map((_, i) => (
        <div
          key={i}
          className="flex flex-col rounded-xl border bg-card shadow-sm overflow-hidden"
        >
          <Skeleton className="aspect-4/3 w-full rounded-none" />
          <div className="p-3 space-y-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-3.5 w-3/4" />
            <Skeleton className="h-3 w-1/2" />
            <div className="flex gap-3 pt-1">
              <Skeleton className="h-3 w-10" />
              <Skeleton className="h-3 w-10" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────
// Inner page — uses useSearchParams (needs Suspense)
// ─────────────────────────────────────────────
function PopularContent() {
  const t = useTranslations("Popular");
  const config = useConfigStore((state) => state.config);
  const searchParams = useSearchParams();
  const router = useRouter();

  const [period, setPeriod] = useState<Period>(
    () => (searchParams.get("period") as Period | null) ?? "today",
  );
  const query = useInfiniteQuery({
    queryKey: ["home", "popular", period],
    queryFn: ({ pageParam, signal }) =>
      apiFetch<MaterialDetail[]>(
        `/home/popular?period=${period}&limit=${LIMIT}&offset=${pageParam}`,
        { signal },
      ),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) =>
      lastPage.length === LIMIT ? allPages.reduce((count, page) => count + page.length, 0) : undefined,
    // Keep the previous period's cards dimmed while the new period loads.
    placeholderData: keepPreviousData,
  });
  const materials = useMemo(() => query.data?.pages.flat() ?? [], [query.data]);
  const isLoading = query.isPending || (query.isPlaceholderData && query.isFetching);
  const isLoadingMore = query.isFetchingNextPage;
  const hasMore = query.hasNextPage;

  const handlePeriodChange = (value: string) => {
    const newPeriod = value as Period;
    if (newPeriod === period) return;
    setPeriod(newPeriod);
    router.replace(`/popular?period=${newPeriod}`, { scroll: false });
  };

  const handleLoadMore = async () => {
    const result = await query.fetchNextPage();
    if (result.isFetchNextPageError) toast.error(t("loadError"));
  };

  const subtitle =
    period === "today"
      ? t("todaySubtitle")
      : t("last14DaysSubtitle");

  return (
    <div className="w-full mx-auto max-w-7xl px-4 py-8 pb-36 sm:px-6 sm:pb-10 lg:px-8">
      {/* ── Page header ─────────────────────────────── */}
      <div className="mb-6">
        <SectionHeader
          title={t("title")}
          subtitle={t("subtitle", { siteName: config?.site_name || "" })}
        />
      </div>

      {/* ── Period tabs ──────────────────────────────── */}
      <Tabs value={period} onValueChange={handlePeriodChange}>
        <TabsList>
          <TabsTrigger value="today" className="gap-1.5">
            <TrendingUp className="h-3.5 w-3.5" />
            {t("today")}
          </TabsTrigger>
          <TabsTrigger value="14d" className="gap-1.5">
            <LayoutGrid className="h-3.5 w-3.5" />
            {t("last14Days")}
          </TabsTrigger>
        </TabsList>
      </Tabs>

      {/* ── Subtitle ─────────────────────────────────── */}
      <p className="mt-4 mb-6 text-sm text-muted-foreground">{subtitle}</p>

      {/* ── Content ──────────────────────────────────── */}
      {query.isPending ? (
        <SkeletonGrid />
      ) : query.isError && !query.isFetchNextPageError ? (
        <QueryError message={t("loadError")} onRetry={() => void query.refetch()} className="py-20" />
      ) : materials.length === 0 ? (
        <div
          className={cn(
            "flex flex-col items-center justify-center gap-3 py-20 text-center",
            isLoading && "opacity-60 transition-opacity",
          )}
          aria-busy={isLoading}
        >
          <TrendingUp className="h-10 w-10 text-muted-foreground/30" />
          <div>
            <p className="font-medium text-muted-foreground">
              {t("noResults")}
            </p>
            <p className="mt-1 text-sm text-muted-foreground/70">
              {t("noResultsDesc")}
            </p>
          </div>
        </div>
      ) : (
        <div aria-busy={isLoading} className={cn(isLoading && "opacity-60 transition-opacity")}>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
            {materials.map((material) => (
              <MaterialCard
                key={material.id}
                material={material}
                className="w-full"
              />
            ))}
          </div>

          {/* ── Load more ──────────────────────────── */}
          {!isLoading && (hasMore || isLoadingMore) && (
            <div className="mt-10 flex justify-center">
              <Button
                variant="outline"
                size="sm"
                onClick={() => void handleLoadMore()}
                disabled={isLoadingMore}
                className="min-w-35"
              >
                {isLoadingMore ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    {t("loadingMore")}
                  </>
                ) : (
                  t("loadMore")
                )}
              </Button>
            </div>
          )}

          {/* ── End of results ─────────────────────── */}
          {!isLoading && !hasMore && materials.length > 0 && (
            <p className="mt-10 text-center text-sm text-muted-foreground">
              {t("allResultsSeen", { count: materials.length })}
            </p>
          )}
        </div>
      )}
      <div className="h-28 sm:hidden shrink-0 pointer-events-none" aria-hidden="true" />
    </div>
  );
}

// ─────────────────────────────────────────────
// Page export — wraps content in Suspense so
// useSearchParams() doesn't block static render
// ─────────────────────────────────────────────
export default function PopularPage() {
  return (
    <Suspense
      fallback={
        <div className="w-full mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <Skeleton className="mb-6 h-8 w-48" />
          <Skeleton className="mb-6 h-9 w-48 rounded-lg" />
          <SkeletonGrid />
        </div>
      }
    >
      <PopularContent />
    </Suspense>
  );
}
