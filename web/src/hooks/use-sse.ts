"use client";

import { useEffect } from "react";
import { fetchUnreadCount } from "@/lib/notifications";
import { useAuthStore, useNotificationStore, usePRStore } from "@/lib/stores";
import { subscribeToSSE } from "@/lib/sse-client";
import { isGuest } from "@/lib/guest";
import { fetchOpenPRCount } from "@/lib/pr-client";

export function useSSE() {
    const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
    const user = useAuthStore((state) => state.user);
    const increment = useNotificationStore((state) => state.increment);
    const setUnreadCount = useNotificationStore((state) => state.setUnreadCount);
    const setOpenPRCount = usePRStore((state) => state.setOpenPRCount);

    useEffect(() => {
        if (!isAuthenticated) return;

        const reconcile = () => {
            fetchUnreadCount()
                .then((count) => setUnreadCount(count))
                .catch(() => {});
            if (user && !isGuest(user)) {
                fetchOpenPRCount()
                    .then((count) => setOpenPRCount(count))
                    .catch(() => {});
            }
        };

        reconcile();

        const onVisible = () => {
            if (document.visibilityState === "visible") reconcile();
        };
        document.addEventListener("visibilitychange", onVisible);

        const notifications = subscribeToSSE({
            channel: "notifications",
            listeners: { notification: increment },
            onResync: reconcile,
        });

        const pullRequests =
            user && !isGuest(user)
                ? subscribeToSSE({
                      channel: "pull_requests",
                      listeners: {
                          pr_opened: reconcile,
                          pr_closed: reconcile,
                      },
                      onResync: reconcile,
                  })
                : null;

        return () => {
            document.removeEventListener("visibilitychange", onVisible);
            notifications.close();
            pullRequests?.close();
        };
    }, [isAuthenticated, user, increment, setUnreadCount, setOpenPRCount]);
}
