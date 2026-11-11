"use client";

import React, { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { formatDistanceToNow } from "date-fns";
import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleDashed,
  FileText,
  GitPullRequest,
  MessageSquare,
  Quote,
  XCircle,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { MaterialCard } from "@/components/home/material-card";
import { getMaterialBrowsePath } from "@/components/home/file-type-display";
import {
  PROFILE_MATERIAL_GRID,
  toProfileMaterialDetail,
} from "@/components/profile/profile-material";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useQueryOutcome } from "@/hooks/use-query-outcome";
import { apiFetch } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import { AnonymousBadge } from "@/components/pr/anonymous-badge";
import { useDateLocale } from "@/hooks/use-date-locale";

interface ContributionItem {
  id: string;
  title?: string;
  description?: string | null;
  body?: string;
  type?: string;
  status?: string;
  slug?: string;
  created_at?: string;
  updated_at?: string;
  material_id?: string;
  material_title?: string | null;
  material_slug?: string | null;
  directory_id?: string | null;
  directory_path?: string | null;
  download_count?: number;
  total_views?: number;
  like_count?: number;
  is_liked?: boolean;
  is_favourited?: boolean;
  metadata?: Record<string, unknown>;
  author?: { id: string; display_name: string | null; avatar_url: string | null } | null;
  is_anonymous?: boolean;
}

interface PaginatedContributions {
  items: ContributionItem[];
  total: number;
  page: number;
  pages: number;
}

interface ContributionListProps {
  userId: string;
  type: "prs" | "materials" | "annotations";
  onReady?: () => void;
  onError?: () => void;
}

function getPRVisuals(status: string | undefined) {
  const normalized = status?.toLowerCase();
  if (normalized === "open") {
    return {
      icon: CircleDashed,
      preview: "from-emerald-500/18 via-emerald-500/8 to-background",
      iconClass: "bg-emerald-500/12 text-emerald-600 dark:text-emerald-400",
      badge: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
      labelKey: "statusOpen",
    };
  }
  if (normalized === "merged" || normalized === "approved") {
    return {
      icon: CheckCircle2,
      preview: "from-violet-500/18 via-violet-500/8 to-background",
      iconClass: "bg-violet-500/12 text-violet-600 dark:text-violet-400",
      badge: "bg-violet-500/12 text-violet-700 dark:text-violet-300",
      labelKey: "statusMerged",
    };
  }
  if (normalized === "closed" || normalized === "rejected") {
    return {
      icon: XCircle,
      preview: "from-rose-500/18 via-rose-500/8 to-background",
      iconClass: "bg-rose-500/12 text-rose-600 dark:text-rose-400",
      badge: "bg-rose-500/12 text-rose-700 dark:text-rose-300",
      labelKey: "statusClosed",
    };
  }
  return {
    icon: GitPullRequest,
    preview: "from-sky-500/18 via-sky-500/8 to-background",
    iconClass: "bg-sky-500/12 text-sky-600 dark:text-sky-400",
    badge: "bg-sky-500/12 text-sky-700 dark:text-sky-300",
    labelKey: null,
  };
}

function PRCard({ item }: { item: ContributionItem }) {
  const t = useTranslations("Profile");
  const dateLocale = useDateLocale();
  const visuals = getPRVisuals(item.status);
  const Icon = visuals.icon;
  const timeAgo = item.created_at
    ? formatDistanceToNow(new Date(item.created_at), { addSuffix: true, locale: dateLocale })
    : null;
  const statusLabel = visuals.labelKey
    ? t(visuals.labelKey as "statusOpen" | "statusMerged" | "statusClosed")
    : item.status;

  return (
    <Link
      href={`/pull-requests/${item.id}`}
      className="group block rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
    >
      <article className="flex h-full flex-col overflow-hidden rounded-xl border bg-card shadow-sm transition-[border-color,box-shadow] group-hover:border-primary/20 group-hover:shadow-md">
        <div className={cn("relative flex aspect-4/3 items-center justify-center overflow-hidden bg-linear-to-br", visuals.preview)}>
          <div className={cn("flex h-14 w-14 items-center justify-center rounded-2xl", visuals.iconClass)}>
            <Icon className="h-7 w-7" />
          </div>
          {statusLabel && (
            <span className={cn("absolute right-2 top-2 rounded px-2 py-1 text-[10px] font-semibold", visuals.badge)}>
              {statusLabel}
            </span>
          )}
          {item.is_anonymous && <AnonymousBadge className="absolute left-2 top-2 bg-background/80" />}
        </div>
        <div className="flex min-w-0 flex-1 flex-col p-2.5 sm:p-3">
          <p className="line-clamp-2 text-[13px] font-medium leading-snug text-foreground sm:text-sm">
            {item.title ?? item.id}
          </p>
          <div className="mt-auto flex items-center gap-1.5 pt-3 text-[11px] text-muted-foreground">
            <span className="font-mono">#{item.id.slice(0, 8)}</span>
            {timeAgo && <><span aria-hidden="true">·</span><span className="truncate">{timeAgo}</span></>}
          </div>
        </div>
      </article>
    </Link>
  );
}

function AnnotationCard({ item }: { item: ContributionItem }) {
  const t = useTranslations("Profile");
  const dateLocale = useDateLocale();
  const body = item.body ?? item.title ?? item.id;
  const timeAgo = item.created_at
    ? formatDistanceToNow(new Date(item.created_at), { addSuffix: true, locale: dateLocale })
    : null;

  const slug = item.material_slug ?? item.slug;
  const href = slug
    ? `${getMaterialBrowsePath({ directory_path: item.directory_path ?? null, slug })}?annotation=${item.id}`
    : `/browse?annotation=${item.id}`;

  return (
    <Link
      href={href}
      className="group block rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      data-annotation-card
    >
      <article className="flex h-full flex-col overflow-hidden rounded-xl border bg-card shadow-sm transition-[border-color,box-shadow] group-hover:border-primary/20 group-hover:shadow-md">
        <div className="relative flex aspect-4/3 flex-col justify-between overflow-hidden bg-linear-to-br from-amber-500/14 via-amber-500/5 to-background p-4">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-amber-500/12 text-amber-600 dark:text-amber-400">
            <Quote className="h-4 w-4" />
          </div>
          <p className="line-clamp-4 text-sm leading-relaxed text-foreground/80">{body}</p>
        </div>
        <div className="flex min-w-0 flex-1 flex-col p-2.5 sm:p-3">
          {item.material_title && (
            <p className="line-clamp-1 text-[13px] font-medium leading-snug text-foreground sm:text-sm group-hover:text-primary transition-colors">
              {item.material_title}
            </p>
          )}
          <div className="mt-auto flex items-center justify-between gap-2 pt-2 text-[11px] text-muted-foreground">
            <span className="font-medium">{t("annotation")}</span>
            {timeAgo && <span className="truncate">{timeAgo}</span>}
          </div>
        </div>
      </article>
    </Link>
  );
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

export function ContributionList({ userId, type, onReady, onError }: ContributionListProps) {
  const t = useTranslations("Profile");
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [turningPage, setTurningPage] = useState(false);
  const pageQuery = (requestedPage: number) => ({
    queryKey: ["users", userId, "contributions", type, requestedPage],
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      apiFetch<PaginatedContributions>(
        `/users/${userId}/contributions?${new URLSearchParams({ page: String(requestedPage), limit: "8", type })}`,
        { signal },
      ),
  });
  const query = useQuery(pageQuery(page));
  useQueryOutcome(query, onReady, onError);
  const items = query.data?.items ?? [];
  const pages = query.data?.pages ?? 1;
  const total = query.data?.total ?? 0;
  const loading = query.isFetching || turningPage;

  // Load the target page before switching, so a failure keeps the current page visible.
  const goToPage = async (requestedPage: number) => {
    setTurningPage(true);
    try {
      await queryClient.fetchQuery(pageQuery(requestedPage));
      setPage(requestedPage);
    } catch {
      onError?.();
    } finally {
      setTurningPage(false);
    }
  };

  if (query.isPending) {
    return (
      <div className={PROFILE_MATERIAL_GRID} data-contribution-grid>
        {Array.from({ length: 8 }, (_, index) => <SkeletonCard key={index} />)}
      </div>
    );
  }

  if (items.length === 0) {
    const empty = {
      prs: { icon: GitPullRequest, label: t("noContributionsYet") },
      materials: { icon: FileText, label: t("noMaterialsYet") },
      annotations: { icon: MessageSquare, label: t("noAnnotationsYet") },
    }[type];
    const EmptyIcon = empty.icon;
    return (
      <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-20 text-center">
        <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-muted">
          <EmptyIcon className="h-5 w-5 text-muted-foreground/50" />
        </div>
        <p className="text-sm font-medium text-muted-foreground">{empty.label}</p>
      </div>
    );
  }

  const totalLabel = type === "prs"
    ? t("totalContributions", { count: total })
    : type === "annotations"
      ? t("totalAnnotations", { count: total })
      : t("totalMaterials", { count: total });

  return (
    <div>
      <div className={cn(PROFILE_MATERIAL_GRID, loading && "opacity-60")} data-contribution-grid aria-busy={loading}>
        {items.map((item) => {
          if (type === "materials") {
            return <MaterialCard key={item.id} material={toProfileMaterialDetail(item)} />;
          }
          if (type === "annotations") {
            return <AnnotationCard key={item.id} item={item} />;
          }
          return <PRCard key={item.id} item={item} />;
        })}
      </div>

      <div className="mt-5 flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between" data-profile-pagination>
        <p className="text-xs text-muted-foreground">{totalLabel}</p>
        {pages > 1 && (
          <div className="flex items-center gap-2 self-end sm:self-auto">
            <Button
              variant="outline"
              size="icon"
              disabled={page <= 1 || loading}
              onClick={() => void goToPage(page - 1)}
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
              disabled={page >= pages || loading}
              onClick={() => void goToPage(page + 1)}
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
