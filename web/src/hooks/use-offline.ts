"use client";

import { useEffect, useState } from "react";

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
        window.addEventListener("wikint-api-reachable", handleReachable);
        window.addEventListener("wikint-api-unreachable", handleUnreachable);

        return () => {
            window.removeEventListener("online", handleOnline);
            window.removeEventListener("wikint-api-reachable", handleReachable);
            window.removeEventListener("wikint-api-unreachable", handleUnreachable);
        };
    }, []);

    return isOffline;
}
