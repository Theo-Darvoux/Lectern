"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { apiFetchWithResponse } from "@/lib/api-client";
import { subscribeToSSE } from "@/lib/sse-client";
import { PRCard } from "./pr-card";
import { PRCommitGraph } from "./pr-commit-graph";
import { type PullRequestOut } from "@/components/home/types";
import { usePRStore, useAuthStore } from "@/lib/stores";
import {
  Inbox,
  CheckCircle2,
  XCircle,
  Ban,
  ChevronLeft,
  ChevronRight,
  AlertCircle,
  RefreshCw,
  Search,
  X,
  Sparkles,
  GitPullRequest,
  Layers,
  FilePlus,
  Folder,
  ArrowUpDown,
  SearchX,
  User,
  List,
  GitCommitHorizontal,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

type StatusFilter = "open" | "approved" | "rejected" | "cancelled" | null;
type OpFilter = "all" | "create_material" | "edit_material" | "delete_material" | "create_directory" | "move_item" | "revert";
type SortOption = "newest" | "oldest" | "changes";

const PAGE_SIZE = 20;

export function PRList() {
  const t = useTranslations("PRs");
  const user = useAuthStore((state) => state.user);
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);

  const TABS: { value: StatusFilter; labelKey: string; icon: React.ElementType }[] =
    [
      { value: null, labelKey: "all", icon: Layers },
      { value: "open", labelKey: "pending", icon: Inbox },
      { value: "approved", labelKey: "approved", icon: CheckCircle2 },
      { value: "rejected", labelKey: "rejected", icon: XCircle },
      { value: "cancelled", labelKey: "cancelled", icon: Ban },
    ];

  const [prs, setPrs] = useState<PullRequestOut[]>([]);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [page, setPage] = useState(1);
  const [filterStatus, setFilterStatus] = useState<StatusFilter>(null);
  const [filterScope, setFilterScope] = useState<"all" | "mine">("all");
  const [filterOpType, setFilterOpType] = useState<OpFilter>("all");
  const [sortBy, setSortBy] = useState<SortOption>("newest");
  const [viewMode, setViewMode] = useState<"list" | "graph">("graph");
  const [searchQuery, setSearchQuery] = useState("");
  const [totalCount, setTotalCount] = useState<number | null>(null);

  // Status counts for tab badges and hero stats
  const [counts, setCounts] = useState<Record<string, number | null>>({
    open: null,
    approved: null,
    rejected: null,
    cancelled: null,
    mine: null,
  });

  // Bumped by SSE events or retries
  const [refreshKey, setRefreshKey] = useState(0);
  const userId = user?.id;

  const fetchCounts = useCallback(() => {
    const statuses = ["open", "approved", "rejected", "cancelled"] as const;
    const fetchStatuses = statuses.map((s) =>
      apiFetchWithResponse<PullRequestOut[]>(
        `/pull-requests?status=${s}&page=1&limit=1`,
      ).then((res) => {
        const total = res.response.headers.get("X-Total-Count");
        return total ? parseInt(total, 10) : 0;
      }),
    );

    // If user is logged in, also fetch count for "mine"
    const fetchMine = userId
      ? apiFetchWithResponse<PullRequestOut[]>(
          `/pull-requests?author_id=${userId}&page=1&limit=1`,
        )
          .then((res) => {
            const total = res.response.headers.get("X-Total-Count");
            return total ? parseInt(total, 10) : 0;
          })
          .catch(() => null)
      : Promise.resolve(null);

    Promise.allSettled([...fetchStatuses, fetchMine]).then((results) => {
      const next: Record<string, number | null> = {};
      statuses.forEach((s, i) => {
        const r = results[i];
        next[s] = r.status === "fulfilled" ? r.value : null;
      });
      const mineRes = results[statuses.length];
      next.mine = mineRes.status === "fulfilled" ? mineRes.value : null;

      setCounts(next);
      if (next["open"] !== null) {
        usePRStore.getState().setOpenPRCount(next["open"]);
      }
    });
  }, [userId]);

  useEffect(() => {
    fetchCounts();
  }, [fetchCounts, refreshKey]);

  useEffect(() => {
    let active = true;
    Promise.resolve().then(() => {
      if (active) {
        setLoading(true);
        setError(false);
      }
    });

    const params = new URLSearchParams();
    params.set("page", String(page));
    params.set("limit", String(PAGE_SIZE));
    if (filterStatus) params.set("status", filterStatus);
    if (filterScope === "mine" && user?.id) params.set("author_id", user.id);

    apiFetchWithResponse<PullRequestOut[]>(`/pull-requests?${params}`)
      .then(({ data, response }) => {
        if (!active) return;
        setPrs(data);
        setHasLoaded(true);
        const total = response.headers.get("X-Total-Count");
        setTotalCount(total ? parseInt(total, 10) : null);
      })
      .catch(() => {
        if (active) setError(true);
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [page, filterStatus, filterScope, refreshKey, user?.id]);

  // Subscribe to SSE
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
    if (status === filterStatus) return;
    setLoading(true);
    setError(false);
    setFilterStatus(status);
    setPage(1);
  };

  const changePage = (nextPage: number) => {
    setLoading(true);
    setError(false);
    setPage(nextPage);
  };

  const retry = () => {
    setLoading(true);
    setError(false);
    setRefreshKey((key) => key + 1);
  };

  // Client-side filtering for Search, Operation Type, and Sorting
  const filteredAndSortedPrs = useMemo(() => {
    let list = [...prs];

    // Filter by search query (title, author display name, #id)
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim().replace(/^#/, "");
      list = list.filter((pr) => {
        const titleMatch = pr.title.toLowerCase().includes(q);
        const authorMatch = pr.author?.display_name?.toLowerCase().includes(q);
        const idMatch = pr.id.toLowerCase().includes(q);
        return titleMatch || authorMatch || idMatch;
      });
    }

    // Filter by operation type
    if (filterOpType !== "all") {
      list = list.filter((pr) => {
        if (filterOpType === "revert") {
          return pr.type === "revert" || Boolean(pr.reverts_pr_id) || Boolean(pr.reverted_by_pr_id);
        }
        if (filterOpType === "create_directory") {
          return (
            pr.summary_types?.some((st) => st.includes("directory")) ||
            pr.type.includes("directory")
          );
        }
        return (
          pr.summary_types?.includes(filterOpType) ||
          pr.type === filterOpType
        );
      });
    }

    // Sort
    if (sortBy === "oldest") {
      list.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
    } else if (sortBy === "changes") {
      list.sort((a, b) => {
        const aCount = a.summary_types?.length || 1;
        const bCount = b.summary_types?.length || 1;
        return bCount - aCount;
      });
    } else {
      // Default: newest first
      list.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    }

    return list;
  }, [prs, searchQuery, filterOpType, sortBy]);

  const hasActiveFilters = searchQuery.trim() !== "" || filterOpType !== "all" || filterScope !== "all";

  const clearAllFilters = () => {
    setSearchQuery("");
    setFilterOpType("all");
    setFilterScope("all");
  };

  const emptyStatusMessage = filterStatus
    ? t("noContributions", {
        status: t(TABS.find((tab) => tab.value === filterStatus)?.labelKey as any).toLowerCase(),
      })
    : t("noContributionsYet");

  return (
    <div className="space-y-6">
      {/* ── 1. Hero Section ─────────────────────────────── */}
      <section className="rounded-2xl border bg-card p-5 shadow-xs sm:p-6">
        <div className="grid gap-6 md:grid-cols-[minmax(0,1fr)_19rem] md:items-center">
          {/* Left Column: Title, Eyebrow, Description & CTAs */}
          <div className="space-y-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary shadow-xs">
              <GitPullRequest className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">
                {t("eyebrow")}
              </p>
              <h1 className="mt-1 text-2xl font-bold tracking-tight sm:text-3xl lg:text-4xl">
                {t("heroTitle")}
              </h1>
            </div>
            <p className="max-w-xl text-sm leading-relaxed text-muted-foreground">
              {t("heroDescription")}
            </p>
            <div className="pt-1 flex items-center gap-3">
              <Button asChild size="sm" className="gap-2 shadow-xs">
                <Link href="/browse">
                  <Folder className="h-4 w-4" />
                  {t("browseLibrary")}
                </Link>
              </Button>
            </div>
          </div>

          {/* Right Column: Live Stats / Overview Widget */}
          <div className="rounded-2xl border bg-background/80 p-4.5 backdrop-blur-xs shadow-xs space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {t("contributions")}
              </span>
              <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                Live
              </span>
            </div>

            <div className="grid grid-cols-2 gap-3 pt-1">
              <div className="rounded-xl border bg-muted/30 p-2.5">
                <span className="text-[11px] text-muted-foreground block truncate">
                  {t("statsPending")}
                </span>
                <span className="text-2xl font-bold tracking-tight text-foreground tabular-nums">
                  {counts.open ?? "—"}
                </span>
              </div>
              <div className="rounded-xl border bg-muted/30 p-2.5">
                <span className="text-[11px] text-muted-foreground block truncate">
                  {t("statsApproved")}
                </span>
                <span className="text-2xl font-bold tracking-tight text-foreground tabular-nums">
                  {counts.approved ?? "—"}
                </span>
              </div>
            </div>

            {isAuthenticated && counts.mine !== null && (
              <button
                type="button"
                onClick={() => {
                  setFilterScope(filterScope === "mine" ? "all" : "mine");
                  setPage(1);
                }}
                className={cn(
                  "w-full flex items-center justify-between p-2 rounded-xl text-xs transition-colors border text-left",
                  filterScope === "mine"
                    ? "bg-primary/10 border-primary/30 text-primary font-medium"
                    : "bg-muted/20 hover:bg-muted/40 border-border/50 text-muted-foreground hover:text-foreground",
                )}
              >
                <span className="inline-flex items-center gap-1.5">
                  <User className="h-3.5 w-3.5" />
                  {t("statsMine")}
                </span>
                <span className="font-bold tabular-nums text-foreground">
                  {counts.mine}
                </span>
              </button>
            )}
          </div>
        </div>
      </section>

      {/* ── 2. Filter & Navigation Bar ─────────────────── */}
      <section className="space-y-3.5">
        {/* Status Segmented Tabs */}
        <div className="flex items-center gap-1.5 overflow-x-auto pb-1 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
          {TABS.map(({ value, labelKey, icon: Icon }) => {
            const active = filterStatus === value;
            const allCount =
              counts.open !== null &&
              counts.approved !== null &&
              counts.rejected !== null &&
              counts.cancelled !== null
                ? counts.open + counts.approved + counts.rejected + counts.cancelled
                : null;
            const count = value === null ? allCount : counts[value];

            return (
              <Button
                key={labelKey}
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => switchTab(value)}
                className={cn(
                  "relative gap-2 rounded-xl px-3.5 py-2 text-sm font-medium transition-all shrink-0 border border-transparent",
                  active
                    ? "bg-background text-foreground shadow-xs border-border/60 font-semibold"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/40",
                )}
                aria-pressed={active}
              >
                <Icon
                  className={cn(
                    "h-4 w-4",
                    active
                      ? value === "open"
                        ? "text-emerald-500"
                        : value === "approved"
                          ? "text-purple-500"
                          : value === "cancelled"
                            ? "text-muted-foreground"
                            : value === "rejected"
                              ? "text-rose-500"
                              : "text-primary"
                      : "opacity-70",
                  )}
                />
                <span>{t(labelKey as any)}</span>
                {count !== null && count > 0 && (
                  <span
                    className={cn(
                      "rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums leading-none",
                      active
                        ? "bg-primary/10 text-primary"
                        : "bg-muted text-muted-foreground",
                    )}
                  >
                    {count > 99 ? "99+" : count}
                  </span>
                )}
              </Button>
            );
          })}
        </div>

        {/* Search & Secondary Filter Toolbar */}
        <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between">
          {/* Search Input */}
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
            <Input
              type="text"
              placeholder={t("searchPlaceholder")}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-9 pr-8 h-9 rounded-xl bg-card border-border/60 text-sm focus-visible:ring-primary/30"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery("")}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground p-0.5 rounded-md"
                aria-label={t("clearSearch")}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          {/* Filter Dropdowns */}
          <div className="flex items-center gap-2 flex-wrap">
            {/* Scope Filter (when authenticated) */}
            {isAuthenticated && (
              <Select
                value={filterScope}
                onValueChange={(val: "all" | "mine") => {
                  setFilterScope(val);
                  setPage(1);
                }}
              >
                <SelectTrigger className="h-9 w-auto min-w-[130px] rounded-xl text-xs font-medium border-border/60 bg-card">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("filterAll")}</SelectItem>
                  <SelectItem value="mine">{t("filterMine")}</SelectItem>
                </SelectContent>
              </Select>
            )}

            {/* Operation Type Filter */}
            <Select
              value={filterOpType}
              onValueChange={(val: OpFilter) => setFilterOpType(val)}
            >
              <SelectTrigger className="h-9 w-auto min-w-[140px] rounded-xl text-xs font-medium border-border/60 bg-card">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("allTypes")}</SelectItem>
                <SelectItem value="create_material">{t("filterDocuments")}</SelectItem>
                <SelectItem value="edit_material">{t("filterEdits")}</SelectItem>
                <SelectItem value="delete_material">{t("filterDeletions")}</SelectItem>
                <SelectItem value="create_directory">{t("filterFolders")}</SelectItem>
                <SelectItem value="move_item">{t("filterMoves")}</SelectItem>
                <SelectItem value="revert">{t("filterReverts")}</SelectItem>
              </SelectContent>
            </Select>

            {/* Sort Options */}
            <Select
              value={sortBy}
              onValueChange={(val: SortOption) => setSortBy(val)}
            >
              <SelectTrigger className="h-9 w-auto min-w-[130px] rounded-xl text-xs font-medium border-border/60 bg-card">
                <div className="flex items-center gap-1.5">
                  <ArrowUpDown className="h-3.5 w-3.5 text-muted-foreground" />
                  <SelectValue />
                </div>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="newest">{t("sortNewest")}</SelectItem>
                <SelectItem value="oldest">{t("sortOldest")}</SelectItem>
                <SelectItem value="changes">{t("sortChanges")}</SelectItem>
              </SelectContent>
            </Select>
            {/* View Mode Toggle: Graph vs List */}
            <div className="flex items-center rounded-xl border border-border/60 bg-card p-0.5 shadow-2xs">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setViewMode("graph")}
                className={cn(
                  "h-8 px-2.5 rounded-lg text-xs font-medium gap-1.5 transition-colors",
                  viewMode === "graph"
                    ? "bg-muted text-foreground font-semibold shadow-2xs"
                    : "text-muted-foreground hover:text-foreground",
                )}
                aria-label={t("viewGraph")}
              >
                <GitCommitHorizontal className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">{t("viewGraph")}</span>
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setViewMode("list")}
                className={cn(
                  "h-8 px-2.5 rounded-lg text-xs font-medium gap-1.5 transition-colors",
                  viewMode === "list"
                    ? "bg-muted text-foreground font-semibold shadow-2xs"
                    : "text-muted-foreground hover:text-foreground",
                )}
                aria-label={t("viewList")}
              >
                <List className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">{t("viewList")}</span>
              </Button>
            </div>
          </div>
        </div>

        {/* Active Filter Chips */}
        {hasActiveFilters && (
          <div className="flex items-center gap-2 flex-wrap pt-1 text-xs text-muted-foreground">
            <span>{t("showingCount", { count: filteredAndSortedPrs.length, total: prs.length })}</span>
            {searchQuery && (
              <Badge variant="secondary" className="gap-1 text-[11px] h-6 font-normal">
                &quot;{searchQuery}&quot;
                <button
                  type="button"
                  onClick={() => setSearchQuery("")}
                  className="hover:text-foreground"
                >
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            )}
            {filterScope === "mine" && (
              <Badge variant="secondary" className="gap-1 text-[11px] h-6 font-normal">
                {t("filterMine")}
                <button
                  type="button"
                  onClick={() => setFilterScope("all")}
                  className="hover:text-foreground"
                >
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            )}
            {filterOpType !== "all" && (
              <Badge variant="secondary" className="gap-1 text-[11px] h-6 font-normal">
                {filterOpType}
                <button
                  type="button"
                  onClick={() => setFilterOpType("all")}
                  className="hover:text-foreground"
                >
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={clearAllFilters}
              className="h-6 px-2 text-[11px] text-muted-foreground hover:text-foreground"
            >
              {t("clearFilters")}
            </Button>
          </div>
        )}
      </section>

      {/* ── 3. Content List & States ───────────────────── */}
      {error && (
        <div className="flex flex-col gap-3 rounded-2xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive sm:flex-row sm:items-center">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <p className="min-w-0 flex-1">{t("loadError")}</p>
          <Button
            variant="outline"
            size="sm"
            className="shrink-0 gap-2"
            onClick={retry}
          >
            <RefreshCw className="h-3.5 w-3.5" />
            {t("retry")}
          </Button>
        </div>
      )}

      {loading && !hasLoaded ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full rounded-xl" />
          ))}
        </div>
      ) : error && !hasLoaded ? null : prs.length === 0 ? (
        /* Empty status tab state */
        <div
          className={cn(
            "flex flex-col items-center justify-center rounded-2xl border border-dashed py-16 px-4 text-center text-muted-foreground bg-card/40",
            loading && "opacity-60 transition-opacity",
          )}
          aria-busy={loading}
        >
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-muted/60 text-muted-foreground/60 mb-3">
            <Sparkles className="h-6 w-6" />
          </div>
          <p className="text-base font-semibold text-foreground">{emptyStatusMessage}</p>
          <p className="text-xs mt-1 max-w-sm text-muted-foreground">
            {filterStatus === "open"
              ? t("stageChangesToCreate")
              : t("contributionsAppearHere")}
          </p>
          <Button asChild variant="outline" size="sm" className="mt-4 gap-2">
            <Link href="/browse">
              <Folder className="h-4 w-4" />
              {t("browseLibrary")}
            </Link>
          </Button>
        </div>
      ) : filteredAndSortedPrs.length === 0 ? (
        /* Filter / Search yielded 0 results */
        <div
          className="flex flex-col items-center justify-center rounded-2xl border border-dashed py-16 px-4 text-center text-muted-foreground bg-card/40"
          aria-busy={loading}
        >
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-muted/60 text-muted-foreground/60 mb-3">
            <SearchX className="h-6 w-6" />
          </div>
          <p className="text-base font-semibold text-foreground">{t("noResultsMatching")}</p>
          <p className="text-xs mt-1 max-w-sm text-muted-foreground">
            {t("noResultsDesc")}
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={clearAllFilters}
            className="mt-4"
          >
            {t("clearFilters")}
          </Button>
        </div>
      ) : viewMode === "graph" ? (
        /* Graph View Mode */
        <div
          className={cn(loading && "opacity-60 transition-opacity")}
          aria-busy={loading}
        >
          <PRCommitGraph prs={filteredAndSortedPrs} loading={loading} />
        </div>
      ) : (
        /* List View Mode */
        <div
          className={cn(
            "space-y-2.5",
            loading && "opacity-60 transition-opacity",
          )}
          aria-busy={loading}
        >
          {filteredAndSortedPrs.map((pr) => (
            <PRCard key={pr.id} pr={pr} />
          ))}
        </div>
      )}

      {/* ── 4. Pagination ──────────────────────────────── */}
      {prs.length > 0 && (
        <div className="flex items-center justify-between pt-2 border-t border-border/50">
          <Button
            variant="outline"
            size="sm"
            className="gap-1 rounded-xl text-xs text-muted-foreground hover:text-foreground"
            disabled={page === 1 || loading}
            onClick={() => changePage(page - 1)}
          >
            <ChevronLeft className="h-4 w-4" />
            {t("newer")}
          </Button>
          <span className="text-xs font-medium tabular-nums text-muted-foreground">
            {t("page", { page })}
          </span>
          <Button
            variant="outline"
            size="sm"
            className="gap-1 rounded-xl text-xs text-muted-foreground hover:text-foreground"
            disabled={
              loading ||
              (totalCount !== null
                ? page * PAGE_SIZE >= totalCount
                : prs.length < PAGE_SIZE)
            }
            onClick={() => changePage(page + 1)}
          >
            {t("older")}
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}
    </div>
  );
}

