"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
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
import { apiFetch } from "@/lib/api-client";
import { cn } from "@/lib/utils";

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
  const visuals = getPRVisuals(item.status);
  const Icon = visuals.icon;
  const timeAgo = item.created_at
    ? formatDistanceToNow(new Date(item.created_at), { addSuffix: true })
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
  const body = item.body ?? item.title ?? item.id;
  const timeAgo = item.created_at
    ? formatDistanceToNow(new Date(item.created_at), { addSuffix: true })
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
  const [items, setItems] = useState<ContributionItem[]>([]);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const onReadyRef = useRef(onReady);
  const onErrorRef = useRef(onError);

  useEffect(() => {
    onReadyRef.current = onReady;
    onErrorRef.current = onError;
  }, [onError, onReady]);

  const fetchContributions = useCallback(async (requestedPage: number) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(requestedPage),
        limit: "8",
        type,
      });
      const data = await apiFetch<PaginatedContributions>(
        `/users/${userId}/contributions?${params}`,
      );
      setItems(data.items);
      setHasLoaded(true);
      setPage(data.page);
      setPages(data.pages);
      setTotal(data.total);
      onReadyRef.current?.();
    } catch {
      // Keep the last successful page visible when a refresh fails.
      onErrorRef.current?.();
    } finally {
      setLoading(false);
    }
  }, [type, userId]);

  useEffect(() => {
    void fetchContributions(1);
  }, [fetchContributions]);

  if (loading && !hasLoaded) {
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
              onClick={() => void fetchContributions(page - 1)}
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
              onClick={() => void fetchContributions(page + 1)}
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
