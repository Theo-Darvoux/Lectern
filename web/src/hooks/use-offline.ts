"use client";

import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api-client";

const RECONNECT_PROBE_MS = 5_000;

/**
 * Hook to track browser online/offline status (U4).
 */
export function useOffline() {
    // Start by assuming online, because navigator.onLine is famously unreliable
    // (e.g. on Linux/Docker it can be permanently false even when connected).
    const [isOffline, setIsOffline] = useState(false);

    useEffect(() => {
        const handleOnline = () => setIsOffline(false);
        const handleReachable = () => setIsOffline(false);
        const handleUnreachable = () => setIsOffline(true);

        window.addEventListener("online", handleOnline);
        window.addEventListener("lectern-api-reachable", handleReachable);
        window.addEventListener("lectern-api-unreachable", handleUnreachable);

        return () => {
            window.removeEventListener("online", handleOnline);
            window.removeEventListener("lectern-api-reachable", handleReachable);
            window.removeEventListener("lectern-api-unreachable", handleUnreachable);
        };
    }, []);

    // While offline, probe the API so the banner clears as soon as it is back
    // (e.g. after a restart), even if the current page issues no requests.
    // apiRequest dispatches the reachability events itself.
    useEffect(() => {
        if (!isOffline) return;
        const id = setInterval(() => {
            apiRequest("/health", { skipAuth: true, timeoutMs: RECONNECT_PROBE_MS }).catch(() => {});
        }, RECONNECT_PROBE_MS);
        return () => clearInterval(id);
    }, [isOffline]);

    return isOffline;
}
