"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUpDown,
  Bell,
  Check,
  CheckCheck,
  CheckSquare,
  Filter,
  Layers,
  Loader2,
  Search,
  Square,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { NotificationItemCard } from "@/components/notifications/notification-item-card";
import { NotificationEmptyState } from "@/components/notifications/notification-empty-state";
import { useNotificationStore } from "@/lib/stores";
import {
  fetchNotifications,
  fetchUnreadCount,
  getNotificationCategory,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationCategory,
  type NotificationItem,
} from "@/lib/notifications";
import { toast } from "sonner";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 30;

type Filter = "all" | "unread" | "read";
type SortOption = "newest" | "oldest";

interface NotificationGroup {
  label: string;
  items: NotificationItem[];
}

export default function NotificationsPage() {
  const t = useTranslations("Notifications");
  const tCommon = useTranslations("Common");

  // Data states
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [totalServerCount, setTotalServerCount] = useState<number | null>(null);

  // Filters & Controls
  const [filter, setFilter] = useState<Filter>("all");
  const [categoryFilter, setCategoryFilter] = useState<NotificationCategory>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [sortBy, setSortBy] = useState<SortOption>("newest");

  // Multi-selection state
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [selectionMode, setSelectionMode] = useState(false);

  const refreshRequestRef = useRef(0);
  const unreadCount = useNotificationStore((state) => state.unreadCount);
  const setUnreadCount = useNotificationStore((state) => state.setUnreadCount);
  const decrement = useNotificationStore((state) => state.decrement);

  const loadPage = useCallback(
    async (targetPage: number, currentFilter: Filter) => {
      let readParam: boolean | undefined = undefined;
      if (currentFilter === "unread") readParam = false;
      if (currentFilter === "read") readParam = true;

      return fetchNotifications({
        page: targetPage,
        limit: PAGE_SIZE,
        read: readParam,
      });
    },
    [],
  );

  const refresh = useCallback(
    async (currentFilter: Filter) => {
      const requestId = ++refreshRequestRef.current;
      setLoading(true);
      setLoadingMore(false);
      try {
        const [data, count] = await Promise.all([
          loadPage(1, currentFilter),
          fetchUnreadCount(),
        ]);
        if (requestId !== refreshRequestRef.current) return;
        setNotifications(data.items);
        setTotalServerCount(data.total);
        setHasLoaded(true);
        setHasMore(data.page < data.pages);
        setPage(data.page);
        setUnreadCount(count);
      } catch {
        if (requestId !== refreshRequestRef.current) return;
        toast.error(t("loadError"));
      } finally {
        if (requestId === refreshRequestRef.current) setLoading(false);
      }
    },
    [loadPage, setUnreadCount, t],
  );

  useEffect(() => {
    refresh(filter);
  }, [refresh, filter]);

  const loadMore = async () => {
    const requestId = refreshRequestRef.current;
    setLoadingMore(true);
    try {
      const data = await loadPage(page + 1, filter);
      if (requestId !== refreshRequestRef.current) return;
      setNotifications((prev) => [...prev, ...data.items]);
      setHasMore(data.page < data.pages);
      setPage(data.page);
    } catch {
      if (requestId !== refreshRequestRef.current) return;
      toast.error(t("loadError"));
    } finally {
      if (requestId === refreshRequestRef.current) setLoadingMore(false);
    }
  };

  const markRead = async (n: NotificationItem) => {
    if (n.read) return;
    // Optimistic update
    if (filter === "unread") {
      setNotifications((prev) => prev.filter((x) => x.id !== n.id));
    } else {
      setNotifications((prev) =>
        prev.map((x) => (x.id === n.id ? { ...x, read: true } : x)),
      );
    }
    decrement();
    try {
      await markNotificationRead(n.id);
    } catch {
      toast.error(t("markReadError"));
      refresh(filter);
    }
  };

  const markAllRead = async () => {
    try {
      await markAllNotificationsRead();
      if (filter === "unread") {
        setNotifications([]);
      } else {
        setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
      }
      setUnreadCount(0);
      setSelectedIds(new Set());
    } catch {
      toast.error(t("markAllReadError"));
    }
  };

  const markSelectedRead = async () => {
    const unreadSelected = notifications.filter(
      (n) => selectedIds.has(n.id) && !n.read,
    );
    if (unreadSelected.length === 0) {
      setSelectedIds(new Set());
      setSelectionMode(false);
      return;
    }

    if (filter === "unread") {
      setNotifications((prev) => prev.filter((n) => !selectedIds.has(n.id)));
    } else {
      setNotifications((prev) =>
        prev.map((n) => (selectedIds.has(n.id) ? { ...n, read: true } : n)),
      );
    }
    setSelectedIds(new Set());
    setSelectionMode(false);

    try {
      await Promise.all(unreadSelected.map((n) => markNotificationRead(n.id)));
      const count = await fetchUnreadCount();
      setUnreadCount(count);
    } catch {
      toast.error(t("markReadError"));
      refresh(filter);
    }
  };

  // Filtered & Sorted items
  const filteredNotifications = useMemo(() => {
    let result = [...notifications];

    // 1. Category Filter
    if (categoryFilter !== "all") {
      result = result.filter((n) => getNotificationCategory(n.type) === categoryFilter);
    }

    // 2. Search Query
    const q = searchQuery.trim().toLowerCase();
    if (q) {
      result = result.filter((n) => {
        const titleMatch = n.title.toLowerCase().includes(q);
        const bodyMatch = n.body?.toLowerCase().includes(q) ?? false;
        return titleMatch || bodyMatch;
      });
    }

    // 3. Sorting
    result.sort((a, b) => {
      const timeA = new Date(a.created_at).getTime();
      const timeB = new Date(b.created_at).getTime();
      return sortBy === "newest" ? timeB - timeA : timeA - timeB;
    });

    return result;
  }, [notifications, categoryFilter, searchQuery, sortBy]);

  // Group items by time period for intuitive scanning
  const groupedNotifications = useMemo((): NotificationGroup[] => {
    if (filteredNotifications.length === 0) return [];

    const now = new Date();
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const startOfYesterday = startOfToday - 24 * 60 * 60 * 1000;
    const startOfThisWeek = startOfToday - 6 * 24 * 60 * 60 * 1000;

    const todayItems: NotificationItem[] = [];
    const yesterdayItems: NotificationItem[] = [];
    const thisWeekItems: NotificationItem[] = [];
    const olderItems: NotificationItem[] = [];

    filteredNotifications.forEach((item) => {
      const itemTime = new Date(item.created_at).getTime();
      if (itemTime >= startOfToday) {
        todayItems.push(item);
      } else if (itemTime >= startOfYesterday) {
        yesterdayItems.push(item);
      } else if (itemTime >= startOfThisWeek) {
        thisWeekItems.push(item);
      } else {
        olderItems.push(item);
      }
    });

    const groups: NotificationGroup[] = [];
    if (todayItems.length > 0) {
      groups.push({ label: t("timeToday"), items: todayItems });
    }
    if (yesterdayItems.length > 0) {
      groups.push({ label: t("timeYesterday"), items: yesterdayItems });
    }
    if (thisWeekItems.length > 0) {
      groups.push({ label: t("timeThisWeek"), items: thisWeekItems });
    }
    if (olderItems.length > 0) {
      groups.push({ label: t("timeOlder"), items: olderItems });
    }

    return groups;
  }, [filteredNotifications, t]);

  const isAllSelected =
    filteredNotifications.length > 0 &&
    selectedIds.size === filteredNotifications.length;

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const toggleSelectAll = () => {
    if (isAllSelected) {
      setSelectedIds(new Set());
      setSelectionMode(false);
    } else {
      setSelectedIds(new Set(filteredNotifications.map((n) => n.id)));
      setSelectionMode(true);
    }
  };

  const hasUnread = notifications.some((n) => !n.read);

  const clearFilters = () => {
    setSearchQuery("");
    setCategoryFilter("all");
  };

  const hasActiveFilters = searchQuery !== "" || categoryFilter !== "all";

  return (
    <div className="w-full px-4 py-6 pb-12 sm:px-6 sm:py-8 lg:px-8">
      <div className="mx-auto w-full max-w-4xl space-y-6">
        {/* ── Hero Header ───────────────────────────────────────────── */}
        <section className="relative overflow-hidden rounded-3xl border bg-card p-5 shadow-xs sm:p-8">
          <div className="pointer-events-none absolute inset-0 bg-linear-to-br from-primary/10 via-transparent to-primary/5" />
          <div className="relative flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
            <div className="flex items-start gap-4">
              <div className="flex h-13 w-13 shrink-0 items-center justify-center rounded-2xl bg-linear-to-br from-primary/20 via-primary/10 to-muted border border-primary/20 shadow-xs">
                <Bell className="h-6 w-6 text-primary" />
              </div>
              <div>
                <h1 className="text-2xl font-extrabold tracking-tight sm:text-3xl">
                  {t("title")}
                </h1>
                <p className="mt-1 text-sm text-muted-foreground">
                  {t("description")}
                </p>
              </div>
            </div>

            {/* Header Stats & Quick Action */}
            <div className="flex items-center gap-2 sm:gap-3 shrink-0 flex-wrap">
              {/* Unread KPI Pill */}
              <div className="flex items-center gap-2 rounded-xl border bg-background/80 px-3.5 py-2 shadow-xs backdrop-blur-xs">
                <span
                  className={cn(
                    "flex h-2.5 w-2.5 rounded-full",
                    unreadCount > 0 ? "bg-primary animate-pulse" : "bg-emerald-500",
                  )}
                />
                <div className="text-xs">
                  <span className="font-bold text-foreground tabular-nums">
                    {unreadCount}
                  </span>{" "}
                  <span className="text-muted-foreground">{t("statsUnread")}</span>
                </div>
              </div>

              {/* Total server count if loaded */}
              {totalServerCount !== null && (
                <div className="hidden sm:flex items-center gap-2 rounded-xl border bg-background/80 px-3.5 py-2 shadow-xs backdrop-blur-xs">
                  <Layers className="h-3.5 w-3.5 text-muted-foreground" />
                  <div className="text-xs">
                    <span className="font-bold text-foreground tabular-nums">
                      {totalServerCount}
                    </span>{" "}
                    <span className="text-muted-foreground">{t("statsTotal")}</span>
                  </div>
                </div>
              )}

              {/* Mark All Read Button */}
              {hasUnread && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={markAllRead}
                  className="gap-1.5 rounded-xl shadow-xs font-semibold hover:bg-primary hover:text-primary-foreground transition-all"
                >
                  <CheckCheck className="h-4 w-4" />
                  <span>{t("markAllRead")}</span>
                </Button>
              )}
            </div>
          </div>
        </section>

        {/* ── Search & Filter Controls Toolbar ──────────────────────── */}
        <div className="space-y-3">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            {/* Status Segments (All / Unread / Read) */}
            <div className="flex items-center gap-1 rounded-2xl border bg-card/80 p-1 shadow-xs backdrop-blur-xs w-fit">
              {(["all", "unread", "read"] as const).map((f) => {
                const isActive = filter === f;
                return (
                  <Button
                    key={f}
                    variant={isActive ? "secondary" : "ghost"}
                    size="sm"
                    className={cn(
                      "h-8 rounded-xl px-3 text-xs font-semibold transition-all",
                      isActive && "shadow-xs bg-muted text-foreground",
                    )}
                    onClick={() => {
                      if (f === filter) return;
                      refreshRequestRef.current += 1;
                      setLoadingMore(false);
                      setLoading(true);
                      setFilter(f);
                      setSelectedIds(new Set());
                    }}
                  >
                    <span>
                      {f === "all"
                        ? t("filterAll")
                        : f === "unread"
                        ? t("filterUnread")
                        : t("filterRead")}
                    </span>
                    {f === "unread" && unreadCount > 0 && (
                      <span className="ml-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary/20 px-1 text-[10px] font-bold text-primary">
                        {unreadCount > 99 ? "99+" : unreadCount}
                      </span>
                    )}
                  </Button>
                );
              })}
            </div>

            {/* Right controls: Category filter, Sort, Multi-select toggle */}
            <div className="flex items-center gap-2 flex-wrap sm:flex-nowrap">
              {/* Category Filter */}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className={cn(
                      "h-9 gap-1.5 text-xs rounded-xl",
                      categoryFilter !== "all" && "border-primary/50 text-primary font-semibold",
                    )}
                  >
                    <Filter className="h-3.5 w-3.5" />
                    <span>
                      {categoryFilter === "all"
                        ? t("categoryAll")
                        : categoryFilter === "pr"
                        ? t("categoryPR")
                        : categoryFilter === "comment"
                        ? t("categoryComments")
                        : categoryFilter === "moderation"
                        ? t("categoryModeration")
                        : t("categorySystem")}
                    </span>
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-52">
                  <DropdownMenuRadioGroup
                    value={categoryFilter}
                    onValueChange={(val) => setCategoryFilter(val as NotificationCategory)}
                  >
                    <DropdownMenuRadioItem value="all" className="text-xs">
                      {t("categoryAll")}
                    </DropdownMenuRadioItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuRadioItem value="pr" className="text-xs">
                      {t("categoryPR")}
                    </DropdownMenuRadioItem>
                    <DropdownMenuRadioItem value="comment" className="text-xs">
                      {t("categoryComments")}
                    </DropdownMenuRadioItem>
                    <DropdownMenuRadioItem value="moderation" className="text-xs">
                      {t("categoryModeration")}
                    </DropdownMenuRadioItem>
                    <DropdownMenuRadioItem value="system" className="text-xs">
                      {t("categorySystem")}
                    </DropdownMenuRadioItem>
                  </DropdownMenuRadioGroup>
                </DropdownMenuContent>
              </DropdownMenu>

              {/* Sort Dropdown */}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-9 gap-1.5 text-xs rounded-xl"
                  >
                    <ArrowUpDown className="h-3.5 w-3.5" />
                    <span>{sortBy === "newest" ? t("sortRecent") : t("sortOldest")}</span>
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-40">
                  <DropdownMenuRadioGroup
                    value={sortBy}
                    onValueChange={(val) => setSortBy(val as SortOption)}
                  >
                    <DropdownMenuRadioItem value="newest" className="text-xs">
                      {t("sortRecent")}
                    </DropdownMenuRadioItem>
                    <DropdownMenuRadioItem value="oldest" className="text-xs">
                      {t("sortOldest")}
                    </DropdownMenuRadioItem>
                  </DropdownMenuRadioGroup>
                </DropdownMenuContent>
              </DropdownMenu>

              {/* Multi-selection / Select All toggle */}
              {filteredNotifications.length > 0 && (
                <Button
                  variant={isAllSelected ? "secondary" : "outline"}
                  size="sm"
                  onClick={toggleSelectAll}
                  className="h-9 gap-1.5 text-xs rounded-xl"
                  title={isAllSelected ? t("deselectAll") : t("selectAll")}
                >
                  {isAllSelected ? (
                    <CheckSquare className="h-3.5 w-3.5 text-primary" />
                  ) : (
                    <Square className="h-3.5 w-3.5" />
                  )}
                  <span className="hidden sm:inline">
                    {isAllSelected ? t("deselectAll") : t("selectAll")}
                  </span>
                </Button>
              )}
            </div>
          </div>

          {/* Search Bar */}
          <div className="relative w-full">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t("searchPlaceholder")}
              className="h-10 pl-9 pr-8 text-xs sm:text-sm rounded-2xl bg-card border-border shadow-2xs"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery("")}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground p-1 rounded-full"
                title={t("clearSearch")}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>

        {/* ── Selection Batch Bar (when items are selected) ─────────── */}
        {selectedIds.size > 0 && (
          <div className="sticky top-16 z-30 flex items-center justify-between rounded-2xl border bg-card/95 px-4 py-3 shadow-md backdrop-blur-md animate-in fade-in slide-in-from-top-2">
            <div className="flex items-center gap-2">
              <Badge variant="secondary" className="text-xs font-semibold px-2.5 py-1">
                {t("selectedCount", { count: selectedIds.size })}
              </Badge>
            </div>

            <div className="flex items-center gap-2">
              <Button
                variant="default"
                size="sm"
                onClick={markSelectedRead}
                className="h-8 gap-1.5 text-xs rounded-xl font-semibold shadow-xs"
              >
                <Check className="h-3.5 w-3.5" />
                <span>{t("markSelectedRead")}</span>
              </Button>

              <Button
                variant="ghost"
                size="icon"
                onClick={() => {
                  setSelectedIds(new Set());
                  setSelectionMode(false);
                }}
                className="h-8 w-8 rounded-xl text-muted-foreground hover:text-foreground"
                title={t("deselectAll")}
                aria-label={t("deselectAll")}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}

        {/* ── Notifications Content List / States ───────────────────── */}
        {loading && !hasLoaded ? (
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <div
                key={i}
                className="flex items-start gap-4 rounded-2xl border bg-card p-4 sm:p-5 shadow-xs"
              >
                <Skeleton className="h-10 w-10 shrink-0 rounded-xl" />
                <div className="flex-1 space-y-2">
                  <div className="flex items-center gap-2">
                    <Skeleton className="h-4 w-20 rounded-md" />
                    <Skeleton className="h-3 w-16 rounded-md" />
                  </div>
                  <Skeleton className="h-5 w-3/4 rounded-md" />
                  <Skeleton className="h-4 w-full rounded-md" />
                </div>
              </div>
            ))}
          </div>
        ) : notifications.length === 0 ? (
          <div
            className={cn(
              "transition-opacity duration-200",
              loading && "opacity-60",
            )}
            aria-busy={loading}
          >
            <NotificationEmptyState
              mode={filter === "unread" ? "all_caught_up" : "empty"}
            />
          </div>
        ) : filteredNotifications.length === 0 ? (
          <div
            className={cn(
              "transition-opacity duration-200",
              loading && "opacity-60",
            )}
            aria-busy={loading}
          >
            <NotificationEmptyState
              mode="no_filter_match"
              onClearFilters={clearFilters}
            />
          </div>
        ) : (
          <div
            aria-busy={loading}
            className={cn(
              "space-y-6 transition-opacity duration-200",
              loading && "opacity-60",
            )}
          >
            {groupedNotifications.map((group) => (
              <div key={group.label} className="space-y-3">
                {/* Temporal Group Header */}
                <div className="flex items-center gap-2 px-1">
                  <h2 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                    {group.label}
                  </h2>
                  <Badge variant="outline" className="text-[10px] px-1.5 py-0 rounded-md text-muted-foreground border-border/60">
                    {group.items.length}
                  </Badge>
                  <div className="flex-1 border-t border-border/40 ml-2" />
                </div>

                {/* Group Notification Cards */}
                <div className="space-y-2.5">
                  {group.items.map((n) => (
                    <NotificationItemCard
                      key={n.id}
                      notification={n}
                      selectable={selectionMode || selectedIds.size > 0}
                      isSelected={selectedIds.has(n.id)}
                      onToggleSelect={toggleSelect}
                      onMarkRead={markRead}
                    />
                  ))}
                </div>
              </div>
            ))}

            {/* Load More Pagination */}
            {hasMore && (
              <div className="flex justify-center pt-4">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={loadMore}
                  disabled={loading || loadingMore}
                  className="rounded-2xl px-6 h-10 gap-2 font-semibold shadow-xs hover:bg-muted"
                >
                  {loadingMore ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      <span>{tCommon("loading")}</span>
                    </>
                  ) : (
                    <span>{t("loadMore")}</span>
                  )}
                </Button>
              </div>
            )}
          </div>
        )}
        <div className="h-28 sm:hidden shrink-0 pointer-events-none" aria-hidden="true" />
      </div>
    </div>
  );
}
