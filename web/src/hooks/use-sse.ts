"use client";

import { useEffect } from "react";
import { fetchUnreadCount } from "@/lib/notifications";
import {
    useAuthStore,
    useNotificationStore,
    usePRStore,
} from "@/lib/stores";
import { refreshBrowse } from "@/lib/browse-prefetch";
import { setSSEIdentity, subscribeToSSE } from "@/lib/sse-client";
import { isGuest } from "@/lib/guest";
import { fetchOpenPRCount, reconcilePendingContribution } from "@/lib/pr-client";
import {
    resolvePendingContributionEvent,
    usePendingContributionsStore,
} from "@/lib/pending-contributions";
import { useStagingStore } from "@/lib/staging-store";

export function useSSE() {
    const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
    const isAuthLoading = useAuthStore((state) => state.isLoading);
    const user = useAuthStore((state) => state.user);
    const increment = useNotificationStore((state) => state.increment);
    const setUnreadCount = useNotificationStore((state) => state.setUnreadCount);
    const setOpenPRCount = usePRStore((state) => state.setOpenPRCount);

    const ownerId = isAuthenticated && user?.id ? String(user.id) : null;
    const isGuestUser = isGuest(user);
    const canTrackPRs = Boolean(user) && !isGuestUser;

    useEffect(() => {
        if (isAuthLoading) return;
        usePendingContributionsStore.getState().activateOwner(ownerId);
        // A guest session can't own a draft, so it must not replace the last account's.
        useStagingStore.getState().activateOwner(isGuestUser ? null : ownerId);
        setSSEIdentity(ownerId);
        if (!isAuthenticated) return;

        const reconcile = () => {
            fetchUnreadCount()
                .then((count) => setUnreadCount(count))
                .catch(() => {});
            if (canTrackPRs) {
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

        const pullRequests = canTrackPRs
            ? subscribeToSSE({
                  channel: "pull_requests",
                  listeners: {
                      pr_opened: reconcile,
                      pr_closed: (event) => {
                          resolvePendingContributionEvent(event);
                          refreshBrowse();
                          reconcile();
                      },
                  },
                  // The notifications subscription reconciles PR counts too.
                  onResync: () => {},
              })
            : null;

        return () => {
            document.removeEventListener("visibilitychange", onVisible);
            notifications.close();
            pullRequests?.close();
        };
    }, [
        isAuthenticated,
        isAuthLoading,
        ownerId,
        isGuestUser,
        canTrackPRs,
        increment,
        setUnreadCount,
        setOpenPRCount,
    ]);
}
