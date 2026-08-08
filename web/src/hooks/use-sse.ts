"use client";

import { useEffect, useRef } from "react";
import { fetchUnreadCount } from "@/lib/notifications";
import { useAuthStore, useNotificationStore, usePRStore } from "@/lib/stores";
import { createSSEConnection, SSEConnection } from "@/lib/sse-client";
import { isGuest } from "@/lib/guest";
import { fetchOpenPRCount } from "@/lib/pr-client";

const CHANNEL_NAME = "lectern-sse-leader";

export function useSSE() {
    const { isAuthenticated, user } = useAuthStore();
    const { increment, setUnreadCount } = useNotificationStore();
    const { setOpenPRCount } = usePRStore();
    const connectionRef = useRef<SSEConnection | null>(null);
    const prConnectionRef = useRef<SSEConnection | null>(null);
    const channelRef = useRef<BroadcastChannel | null>(null);
    const isLeaderRef = useRef(false);

    useEffect(() => {
        if (!isAuthenticated) return;

        const reconcileUnread = () => {
            fetchUnreadCount()
                .then((count) => setUnreadCount(count))
                .catch(() => { });
            if (user && !isGuest(user)) {
                fetchOpenPRCount()
                    .then((count) => setOpenPRCount(count))
                    .catch(() => { });
            }
        };

        reconcileUnread();

        // SSE events only ever bump the badge; reconcile against the server when
        // the tab regains focus so the count can't drift over a long session.
        const onVisible = () => {
            if (document.visibilityState === "visible") reconcileUnread();
        };
        document.addEventListener("visibilitychange", onVisible);

        const channel = new BroadcastChannel(CHANNEL_NAME);
        channelRef.current = channel;

        channel.postMessage({ type: "leader-check" });

        const delay = 200 + Math.random() * 300;
        const leaderTimeout = setTimeout(() => {
            if (!isLeaderRef.current) {
                isLeaderRef.current = true;
                channel.postMessage({ type: "leader-alive" });
                connectSSE();
            }
        }, delay);

        let fallbackTimeout: ReturnType<typeof setTimeout> | null = null;

        const resetFallback = () => {
            if (fallbackTimeout) clearTimeout(fallbackTimeout);
            fallbackTimeout = setTimeout(() => {
                if (!isLeaderRef.current) {
                    isLeaderRef.current = true;
                    connectSSE();
                }
            }, 25000); // Take over if leader is silent for 25s
        };

        channel.onmessage = (event: MessageEvent) => {
            if (event.data?.type === "leader-check" && isLeaderRef.current) {
                channel.postMessage({ type: "leader-alive" });
            }
            if (
                (event.data?.type === "leader-alive" || event.data?.type === "leader-heartbeat") &&
                !isLeaderRef.current
            ) {
                clearTimeout(leaderTimeout);
                resetFallback();
            }
            if (event.data?.type === "leader-closing" && !isLeaderRef.current) {
                if (fallbackTimeout) clearTimeout(fallbackTimeout);
                isLeaderRef.current = true;
                connectSSE();
            }
            if (event.data?.type === "notification") {
                increment();
            }
            if (event.data?.type === "pr-count") {
                setOpenPRCount(event.data.count);
            }
        };

        let heartbeatInterval: ReturnType<typeof setInterval> | null = null;

        function connectSSE() {
            if (fallbackTimeout) clearTimeout(fallbackTimeout);
            connectionRef.current?.close();
            connectionRef.current = createSSEConnection({
                url: "/notifications/sse",
                listeners: {
                    notification: () => {
                        increment();
                        channelRef.current?.postMessage({ type: "notification" });
                    },
                },
            });

            if (user && !isGuest(user)) {
                prConnectionRef.current?.close();
                prConnectionRef.current = createSSEConnection({
                    url: "/pull-requests/sse",
                    listeners: {
                        pr_opened: () => {
                            fetchOpenPRCount()
                                .then((count) => {
                                    setOpenPRCount(count);
                                    channelRef.current?.postMessage({ type: "pr-count", count });
                                })
                                .catch(() => {});
                        },
                        pr_closed: () => {
                            fetchOpenPRCount()
                                .then((count) => {
                                    setOpenPRCount(count);
                                    channelRef.current?.postMessage({ type: "pr-count", count });
                                })
                                .catch(() => {});
                        },
                    },
                });
            }

            // Set up heartbeat
            if (heartbeatInterval) clearInterval(heartbeatInterval);
            heartbeatInterval = setInterval(() => {
                channelRef.current?.postMessage({ type: "leader-heartbeat" });
            }, 10000);
        }

        return () => {
            document.removeEventListener("visibilitychange", onVisible);
            clearTimeout(leaderTimeout);
            if (fallbackTimeout) clearTimeout(fallbackTimeout);
            if (heartbeatInterval) clearInterval(heartbeatInterval);
            if (isLeaderRef.current) {
                channel.postMessage({ type: "leader-closing" });
            }
            isLeaderRef.current = false;
            connectionRef.current?.close();
            connectionRef.current = null;
            prConnectionRef.current?.close();
            prConnectionRef.current = null;
            channel.close();
            channelRef.current = null;
        };
    }, [isAuthenticated, user, increment, setUnreadCount, setOpenPRCount]);
}
