"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { apiFetch } from "@/lib/api-client";
import { useAuth } from "@/hooks/use-auth";
import { isGuest } from "@/lib/guest";
import { FeaturedSection } from "@/components/home/featured-section";
import { PopularSection } from "@/components/home/popular-section";
import { RecentPRsSection } from "@/components/home/recent-prs-section";
import { MaterialGridSection } from "@/components/home/material-grid-section";
import { HeroBar } from "@/components/home/hero-bar";
import { RailFavourites } from "@/components/home/rail-favourites";
import { StatsCard } from "@/components/home/stats-card";
import { useConfigStore } from "@/lib/stores";
import { Skeleton } from "@/components/ui/skeleton";
import { AlertCircle, History, Sparkles, Clock } from "lucide-react";
import type { HomeData } from "@/components/home/types";
import { useTranslations } from "next-intl";

const DirectoryTreeSidebar = dynamic(
  () => import("@/components/browse/directory-tree-sidebar").then((m) => m.DirectoryTreeSidebar),
  { ssr: false, loading: () => null },
);

function getGreetingKey(): "morning" | "afternoon" | "evening" {
  const hour = new Date().getHours();
  if (hour < 12) return "morning";
  if (hour < 18) return "afternoon";
  return "evening";
}

export default function HomePage() {
  const t = useTranslations("Home");
  const { user } = useAuth();
  const config = useConfigStore((state) => state.config);
  const guest = isGuest(user);
  const [data, setData] = useState<HomeData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<HomeData>("/home")
      .then(setData)
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : t("loadError"));
      })
      .finally(() => setIsLoading(false));
  }, [t]);

  const greeting = t(`greetings.${getGreetingKey()}` as Parameters<typeof t>[0]);
  const displayName =
    user?.display_name ?? user?.email?.split("@")[0] ?? t("guest");

  const showContinue =
    isLoading || (data?.recently_viewed && data.recently_viewed.length > 0);

  return (
    <div className="flex h-full w-full overflow-hidden">
      <DirectoryTreeSidebar />
      <div className="flex-1 min-w-0 overflow-y-auto">
        <div className="w-full space-y-8 px-4 py-6 pb-24 sm:px-6 sm:pb-10 lg:px-8 2xl:px-10">
          {/* ── Hero bar ──────────────────────────────────────── */}
          <HeroBar
            greeting={greeting}
            displayName={displayName}
            subtitle={t("whatsHappening", { siteName: config?.site_name || "" })}
            isLoading={isLoading && !data}
            showContributorActions={!guest}
          />

          {/* ── Error banner ──────────────────────────────────── */}
          {error && (
            <div className="flex items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <p className="font-medium">{t("errorTitle")}</p>
                <p className="mt-0.5 text-destructive/80">{error}</p>
              </div>
            </div>
          )}

          {/* ── Featured (full-bleed) ─────────────────────────── */}
          {isLoading ? (
            <div>
              <Skeleton className="mb-4 h-6 w-32" />
              <Skeleton className="h-48 w-full rounded-xl sm:h-56" />
            </div>
          ) : (
            data?.featured &&
            data.featured.length > 0 && <FeaturedSection items={data.featured} />
          )}

          {/* ── Dashboard grid ────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-8 xl:grid-cols-3">
            {/* Main column */}
            <div className="space-y-10 xl:col-span-2">
              <PopularSection
                today={data?.popular_today ?? []}
                fortnight={data?.popular_14d ?? []}
                isLoading={isLoading}
              />

              <MaterialGridSection
                title={t("recentlyAddedTitle")}
                subtitle={t("recentlyAddedSubtitle")}
                icon={<Clock className="h-4 w-4" />}
                materials={data?.recently_added ?? []}
                isLoading={isLoading}
                seeAllHref="/browse"
                emptyText={t("nothingHereYet")}
                emptyIcon={<Sparkles className="h-8 w-8 text-muted-foreground/30" />}
              />

              {showContinue && (
                <MaterialGridSection
                  title={t("continueTitle")}
                  subtitle={t("continueSubtitle")}
                  icon={<History className="h-4 w-4" />}
                  materials={data?.recently_viewed ?? []}
                  isLoading={isLoading}
                  emptyText={t("nothingHereYet")}
                  emptyIcon={<History className="h-8 w-8 text-muted-foreground/30" />}
                />
              )}
            </div>

            {/* Right rail */}
            <aside className="space-y-8">
              {!guest && (
                <RailFavourites
                  materials={data?.recent_favourites ?? []}
                  isLoading={isLoading}
                />
              )}
              <RecentPRsSection prs={data?.recent_prs ?? []} isLoading={isLoading} />
              <StatsCard stats={data?.stats} isLoading={isLoading} />
            </aside>
          </div>
        </div>
      </div>
    </div>
  );
}
