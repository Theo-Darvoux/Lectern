"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { useAuth } from "@/hooks/use-auth";
import { FeaturedSection } from "@/components/home/featured-section";
import { PopularSection } from "@/components/home/popular-section";
import { RecentPRsSection } from "@/components/home/recent-prs-section";
import { FavouritesSection } from "@/components/home/favourites-section";
import { useConfigStore } from "@/lib/stores";
import { Skeleton } from "@/components/ui/skeleton";
import { AlertCircle } from "lucide-react";
import type { HomeData } from "@/components/home/types";

import { useTranslations } from "next-intl";

function getGreetingKey(): "morning" | "afternoon" | "evening" {
  const hour = new Date().getHours();
  if (hour < 12) return "morning";
  if (hour < 18) return "afternoon";
  return "evening";
}

function WelcomeHeaderSkeleton() {
  return (
    <div className="space-y-2">
      <Skeleton className="h-8 w-72 sm:w-96" />
      <Skeleton className="h-5 w-52" />
    </div>
  );
}

export default function HomePage() {
  const t = useTranslations("Home");
  const { user } = useAuth();
  const { config } = useConfigStore();
  const [data, setData] = useState<HomeData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<HomeData>("/home")
      .then(setData)
      .catch((err: unknown) => {
        setError(
          err instanceof Error ? err.message : t("loadError"),
        );
      })
      .finally(() => setIsLoading(false));
  }, [t]);

  const greeting = t(`greetings.${getGreetingKey()}` as Parameters<typeof t>[0]);
  const displayName =
    user?.display_name ?? user?.email?.split("@")[0] ?? t("guest");

  return (
    <div className="w-full mx-auto max-w-7xl px-4 py-8 pb-24 sm:px-6 sm:pb-10 lg:px-8">
      {/* ── Welcome header ───────────────────────────────── */}
      <div className="mb-10">
        {isLoading && !data ? (
          <WelcomeHeaderSkeleton />
        ) : (
          <>
            <h1 className="text-2xl font-bold tracking-tight sm:text-3xl wrap-break-word min-w-0">
              {greeting}, {displayName}!{" "}
              <span role="img" aria-label="wave">
                👋
              </span>
            </h1>
            <p className="mt-1 text-muted-foreground">
              {t("whatsHappening", { siteName: config?.site_name || "" })}
            </p>
          </>
        )}
      </div>

      {/* ── Error banner ─────────────────────────────────── */}
      {error && (
        <div className="mb-8 flex items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p className="font-medium">{t("errorTitle")}</p>
            <p className="mt-0.5 text-destructive/80">{error}</p>
          </div>
        </div>
      )}

      {/* ── Featured ─────────────────────────────────────── */}
      {isLoading ? (
        <div className="mb-10">
          <Skeleton className="mb-4 h-6 w-32" />
          <Skeleton className="h-48 w-full rounded-xl sm:h-56" />
        </div>
      ) : (
        data?.featured &&
        data.featured.length > 0 && (
          <div className="mb-10">
            <FeaturedSection items={data.featured} />
          </div>
        )
      )}

      {/* ── Popular today ────────────────────────────────── */}
      <div className="mb-10">
        <PopularSection
          title={t("popularTodayTitle")}
          subtitle={t("popularTodaySubtitle")}
          materials={data?.popular_today ?? []}
          seeAllHref="/popular?period=today"
          isLoading={isLoading}
        />
      </div>

      {/* ── Popular last 14 days ─────────────────────────── */}
      <div className="mb-10">
        <PopularSection
          title={t("trendingFortnightTitle")}
          subtitle={t("trendingFortnightSubtitle")}
          materials={data?.popular_14d ?? []}
          seeAllHref="/popular?period=14d"
          isLoading={isLoading}
        />
      </div>

      {/* ── Recent contributions ─────────────────────────── */}
      <div className="mb-10">
        <RecentPRsSection prs={data?.recent_prs ?? []} isLoading={isLoading} />
      </div>

      {/* ── Favourites ───────────────────────────────────── */}
      {(isLoading ||
        (data?.recent_favourites && data.recent_favourites.length > 0)) && (
        <div className="mb-10">
          <FavouritesSection
            materials={data?.recent_favourites ?? []}
            isLoading={isLoading}
          />
        </div>
      )}
    </div>
  );
}
