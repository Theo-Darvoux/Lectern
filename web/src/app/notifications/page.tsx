"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { formatDistanceToNow } from "date-fns/formatDistanceToNow";
import { Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useNotificationStore } from "@/lib/stores";
import {
  fetchNotifications,
  fetchUnreadCount,
  markAllNotificationsRead,
  markNotificationRead,
  notificationIcon,
  type NotificationItem,
} from "@/lib/notifications";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

const PAGE_SIZE = 30;

type Filter = "all" | "unread";

export default function NotificationsPage() {
  const t = useTranslations("Notifications");
  const tCommon = useTranslations("Common");
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const setUnreadCount = useNotificationStore((state) => state.setUnreadCount);
  const decrement = useNotificationStore((state) => state.decrement);

  const loadPage = useCallback(
    async (targetPage: number, currentFilter: Filter) => {
      const data = await fetchNotifications({
        page: targetPage,
        limit: PAGE_SIZE,
        read: currentFilter === "unread" ? false : undefined,
      });
      setHasMore(targetPage < data.pages);
      setPage(targetPage);
      return data.items;
    },
    [],
  );

  const refresh = useCallback(
    async (currentFilter: Filter) => {
      setLoading(true);
      try {
        const [items, count] = await Promise.all([
          loadPage(1, currentFilter),
          fetchUnreadCount(),
        ]);
        setNotifications(items);
        setUnreadCount(count);
      } catch {
        toast.error(t("loadError"));
      } finally {
        setLoading(false);
      }
    },
    [loadPage, setUnreadCount, t],
  );

  useEffect(() => {
    refresh(filter);
  }, [refresh, filter]);

  const loadMore = async () => {
    setLoadingMore(true);
    try {
      const items = await loadPage(page + 1, filter);
      setNotifications((prev) => [...prev, ...items]);
    } catch {
      toast.error(t("loadError"));
    } finally {
      setLoadingMore(false);
    }
  };

  const markRead = async (n: NotificationItem) => {
    if (n.read) return;
    // Optimistic update.
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
    } catch {
      toast.error(t("markAllReadError"));
    }
  };

  const hasUnread = notifications.some((n) => !n.read);

  return (
    <div className="w-full mx-auto max-w-3xl space-y-4 p-4 sm:p-6 pb-20 sm:pb-6">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        {hasUnread && (
          <Button variant="outline" size="sm" onClick={markAllRead}>
            <Check className="mr-2 h-4 w-4" />
            {t("markAllRead")}
          </Button>
        )}
      </div>

      <div className="flex items-center gap-1 rounded-lg border p-1 w-fit">
        {(["all", "unread"] as const).map((f) => (
          <Button
            key={f}
            variant={filter === f ? "secondary" : "ghost"}
            size="sm"
            className="h-7"
            onClick={() => setFilter(f)}
          >
            {f === "all" ? t("filterAll") : t("filterUnread")}
          </Button>
        ))}
      </div>

      {loading ? (
        <div className="p-6 text-center text-muted-foreground">
          {tCommon("loading")}
        </div>
      ) : notifications.length === 0 ? (
        <div className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">
          <p>
            {filter === "unread"
              ? t("noUnreadNotifications")
              : t("noNotifications")}
          </p>
        </div>
      ) : (
        <>
          <div className="divide-y rounded-lg border">
            {notifications.map((n) => {
              const Icon = notificationIcon(n.type);
              return (
                <div
                  key={n.id}
                  className={`flex items-start gap-4 p-4 transition-colors ${
                    n.read ? "bg-muted/30" : "bg-background"
                  }`}
                  onClick={() => !n.read && markRead(n)}
                >
                  <div
                    className={`mt-1 rounded-full p-2 ${
                      n.read
                        ? "bg-muted text-muted-foreground"
                        : "bg-primary/10 text-primary"
                    }`}
                  >
                    <Icon className="h-4 w-4" />
                  </div>
                  <div className="flex-1 space-y-1">
                    <p
                      className={`text-sm ${n.read ? "text-muted-foreground" : "font-medium"}`}
                    >
                      {n.title}
                    </p>
                    {n.body && (
                      <p className="line-clamp-2 text-sm text-muted-foreground">
                        {n.body}
                      </p>
                    )}
                    <div className="flex items-center gap-3">
                      <span className="text-xs text-muted-foreground">
                        {formatDistanceToNow(new Date(n.created_at), {
                          addSuffix: true,
                        })}
                      </span>
                      {n.link && (
                        <Link
                          href={n.link}
                          className="text-xs text-primary hover:underline"
                          onClick={(e) => {
                            e.stopPropagation();
                            markRead(n);
                          }}
                        >
                          {t("viewDetails")}
                        </Link>
                      )}
                    </div>
                  </div>
                  {!n.read && (
                    <div className="h-2 w-2 rounded-full bg-primary" />
                  )}
                </div>
              );
            })}
          </div>

          {hasMore && (
            <div className="flex justify-center">
              <Button
                variant="outline"
                size="sm"
                onClick={loadMore}
                disabled={loadingMore}
              >
                {loadingMore ? tCommon("loading") : t("loadMore")}
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
