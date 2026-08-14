"use client";

import { useEffect } from "react";
import { fetchUnreadCount } from "@/lib/notifications";
import {
    useAuthStore,
    useBrowseRefreshStore,
    useNotificationStore,
    usePRStore,
} from "@/lib/stores";
import { subscribeToSSE } from "@/lib/sse-client";
import { isGuest } from "@/lib/guest";
import { fetchOpenPRCount, reconcilePendingContribution } from "@/lib/pr-client";
import {
    resolvePendingContributionEvent,
    usePendingContributionsStore,
} from "@/lib/pending-contributions";

export function useSSE() {
    const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
    const isAuthLoading = useAuthStore((state) => state.isLoading);
    const user = useAuthStore((state) => state.user);
    const increment = useNotificationStore((state) => state.increment);
    const setUnreadCount = useNotificationStore((state) => state.setUnreadCount);
    const setOpenPRCount = usePRStore((state) => state.setOpenPRCount);

    useEffect(() => {
        if (isAuthLoading) return;
        const ownerId = isAuthenticated && user ? String(user.id) : null;
        usePendingContributionsStore.getState().activateOwner(ownerId);
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
            for (const id of Object.keys(usePendingContributionsStore.getState().contributions)) {
                void reconcilePendingContribution(id);
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
                          pr_closed: (event) => {
                              resolvePendingContributionEvent(event);
                              useBrowseRefreshStore.getState().triggerBrowseRefresh();
                              reconcile();
                          },
                      },
                      onResync: reconcile,
                  })
                : null;

        return () => {
            document.removeEventListener("visibilitychange", onVisible);
            notifications.close();
            pullRequests?.close();
        };
    }, [isAuthenticated, isAuthLoading, user, increment, setUnreadCount, setOpenPRCount]);
}
