"use client";

import Link from "next/link";
import { FileText, FolderTree, GitPullRequest, Sparkles } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { SectionHeader } from "./section-header";
import type { HomeStats } from "./types";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

interface StatTileProps {
  icon: React.ReactNode;
  value: number;
  label: string;
  href: string;
}

function StatTile({ icon, value, label, href }: StatTileProps) {
  return (
    <Link
      href={href}
      className="flex flex-col gap-1 rounded-xl border bg-card p-3 shadow-sm transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span className="text-muted-foreground">{icon}</span>
      <span className="text-xl font-bold leading-none tabular-nums">
        {value.toLocaleString()}
      </span>
      <span className="text-[11px] leading-tight text-muted-foreground">
        {label}
      </span>
    </Link>
  );
}

export function StatsCard({
  stats,
  isLoading = false,
  hasLoaded,
}: {
  stats: HomeStats | undefined;
  isLoading?: boolean;
  hasLoaded?: boolean;
}) {
  const t = useTranslations("Home");
  const loaded = hasLoaded ?? (Boolean(stats) || !isLoading);

  return (
    <section aria-label={t("yourStats")}>
      <SectionHeader
        title={t("yourStats")}
        icon={<Sparkles className="h-4 w-4" />}
      />
      <div
        className={cn("mt-3 grid grid-cols-2 gap-3", isLoading && stats && "opacity-60 transition-opacity")}
        aria-busy={isLoading}
      >
        {!loaded || !stats ? (
          Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-[88px] rounded-xl" />
          ))
        ) : (
          <>
            <StatTile
              icon={<FileText className="h-4 w-4" />}
              value={stats.total_materials}
              label={t("statMaterials")}
              href="/browse"
            />
            <StatTile
              icon={<FolderTree className="h-4 w-4" />}
              value={stats.total_directories}
              label={t("statDirectories")}
              href="/browse"
            />
            <StatTile
              icon={<GitPullRequest className="h-4 w-4" />}
              value={stats.open_prs}
              label={t("statOpenPrs")}
              href="/pull-requests"
            />
            <StatTile
              icon={<Sparkles className="h-4 w-4" />}
              value={stats.my_contributions}
              label={t("statMyContributions")}
              href="/profile"
            />
          </>
        )}
      </div>
    </section>
  );
}
