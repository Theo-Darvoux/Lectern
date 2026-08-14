"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetchWithResponse } from "@/lib/api-client";
import { subscribeToSSE } from "@/lib/sse-client";
import { PRCard } from "./pr-card";
import { type PullRequestOut } from "@/components/home/types";
import { usePRStore } from "@/lib/stores";
import {
  Loader2,
  Inbox,
  CheckCircle2,
  XCircle,
  Ban,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTranslations } from "next-intl";

type StatusFilter = "open" | "approved" | "rejected" | "cancelled" | null;
const PAGE_SIZE = 20;

export function PRList() {
  const t = useTranslations("PRs");

  const TABS: { value: StatusFilter; labelKey: string; icon: React.ElementType }[] =
    [
      { value: "open", labelKey: "pending", icon: Inbox },
      { value: "approved", labelKey: "approved", icon: CheckCircle2 },
      { value: "rejected", labelKey: "rejected", icon: XCircle },
      { value: "cancelled", labelKey: "cancelled", icon: Ban },
    ];

  const [prs, setPrs] = useState<PullRequestOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [filterStatus, setFilterStatus] = useState<StatusFilter>("open");
  const [totalCount, setTotalCount] = useState<number | null>(null);

  // Lightweight counts for the tab badges
  const [counts, setCounts] = useState<Record<string, number | null>>({
    open: null,
    approved: null,
    rejected: null,
    cancelled: null,
  });

  // Bumped by SSE events to trigger refetches without changing page/filter.
  const [refreshKey, setRefreshKey] = useState(0);

  const fetchCounts = useCallback(() => {
    const statuses = ["open", "approved", "rejected", "cancelled"] as const;
    Promise.allSettled(
      statuses.map((s) =>
        apiFetchWithResponse<PullRequestOut[]>(
          `/pull-requests?status=${s}&page=1&limit=1`,
        ).then((res) => {
          const total = res.response.headers.get("X-Total-Count");
          return total ? parseInt(total, 10) : 0;
        }),
      ),
    ).then((results) => {
      const next: Record<string, number | null> = {};
      statuses.forEach((s, i) => {
        const r = results[i];
        next[s] = r.status === "fulfilled" ? r.value : null;
      });
      setCounts(next);
      if (next["open"] !== null) {
        usePRStore.getState().setOpenPRCount(next["open"]);
      }
    });
  }, []);

  useEffect(() => {
    fetchCounts();
  }, [fetchCounts, refreshKey]);

  useEffect(() => {
    let active = true;
    Promise.resolve().then(() => {
      if (active) setLoading(true);
    });

    const params = new URLSearchParams();
    params.set("page", String(page));
    params.set("limit", String(PAGE_SIZE));
    if (filterStatus) params.set("status", filterStatus);

    apiFetchWithResponse<PullRequestOut[]>(`/pull-requests?${params}`)
      .then(({ data, response }) => {
        if (!active) return;
        setPrs(data);
        const total = response.headers.get("X-Total-Count");
        setTotalCount(total ? parseInt(total, 10) : null);
      })
      .catch(() => {
        if (active) { setPrs([]); setTotalCount(null); }
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [page, filterStatus, refreshKey]);

  // Subscribe to per-user SSE for live PR list updates.
  useEffect(() => {
    const connection = subscribeToSSE({
      channel: "pull_requests",
      listeners: {
        pr_opened: () => setRefreshKey((k) => k + 1),
        pr_closed: () => setRefreshKey((k) => k + 1),
      },
      onResync: () => setRefreshKey((k) => k + 1),
      startupDelay: 50,
    });
    return () => connection.close();
  }, []);

  const switchTab = (status: StatusFilter) => {
    setFilterStatus(status);
    setPage(1);
  };

  const emptyMessage = filterStatus
    ? t("noContributions", { status: t(TABS.find(tab => tab.value === filterStatus)?.labelKey as any).toLowerCase() })
    : t("noContributionsYet");

  const EmptyIcon =
    filterStatus === "open"
      ? Inbox
      : filterStatus === "approved"
        ? CheckCircle2
        : filterStatus === "rejected"
          ? XCircle
          : filterStatus === "cancelled"
            ? Ban
            : Inbox;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">{t("contributions")}</h1>
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-1 border-b overflow-x-auto [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
        {TABS.map(({ value, labelKey, icon: Icon }) => {
          const active = filterStatus === value;
          const count = counts[value!];
          return (
            <button
              key={value}
              onClick={() => switchTab(value)}
              className={`group relative flex items-center gap-1.5 px-3 py-2 text-sm font-medium transition-colors shrink-0 ${
                active
                  ? "text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon
                className={`h-4 w-4 ${
                  active
                    ? value === "open"
                      ? "text-green-500"
                      : value === "approved"
                        ? "text-purple-500"
                        : value === "cancelled"
                          ? "text-muted-foreground"
                          : "text-red-500"
                    : ""
                }`}
              />
              {t(labelKey as any)}
              {count !== null && count > 0 && (
                <span
                  className={`ml-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-semibold leading-none ${
                    active
                      ? "bg-foreground/10 text-foreground"
                      : "bg-muted text-muted-foreground"
                  }`}
                >
                  {count > 99 ? "99+" : count}
                </span>
              )}
              {/* Active underline */}
              {active && (
                <span className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-foreground" />
              )}
            </button>
          );
        })}

        {/* "All" tab — right-aligned */}
        <button
          onClick={() => switchTab(null)}
          className={`relative ml-auto flex items-center gap-1.5 px-3 py-2 text-sm font-medium transition-colors shrink-0 ${
            filterStatus === null
              ? "text-foreground"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          {t("all")}
          {filterStatus === null && (
            <span className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-foreground" />
          )}
        </button>
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex justify-center py-16">
          <Loader2 className="animate-spin h-5 w-5 text-muted-foreground" />
        </div>
      ) : prs.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
          <EmptyIcon className="h-10 w-10 mb-3 opacity-30" />
          <p className="text-sm font-medium">{emptyMessage}</p>
          <p className="text-xs mt-1 opacity-70">
            {filterStatus === "open"
              ? t("stageChangesToCreate")
              : t("contributionsAppearHere")}
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-px rounded-lg border overflow-hidden">
          {prs.map((pr) => (
            <PRCard key={pr.id} pr={pr} />
          ))}
        </div>
      )}

      {/* Pagination */}
      {!loading && prs.length > 0 && (
        <div className="flex items-center justify-between pt-1">
          <Button
            variant="ghost"
            size="sm"
            className="gap-1 text-muted-foreground"
            disabled={page === 1}
            onClick={() => setPage((p) => p - 1)}
          >
            <ChevronLeft className="h-4 w-4" />
            {t("newer")}
          </Button>
          <span className="text-xs tabular-nums text-muted-foreground">
            {t("page", { page })}
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="gap-1 text-muted-foreground"
            disabled={totalCount !== null ? page * PAGE_SIZE >= totalCount : prs.length < PAGE_SIZE}
            onClick={() => setPage((p) => p + 1)}
          >
            {t("older")}
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}
    </div>
  );
}
