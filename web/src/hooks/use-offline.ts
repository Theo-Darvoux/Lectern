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
        window.addEventListener("lectern-api-reachable", handleReachable);
        window.addEventListener("lectern-api-unreachable", handleUnreachable);

        return () => {
            window.removeEventListener("online", handleOnline);
            window.removeEventListener("lectern-api-reachable", handleReachable);
            window.removeEventListener("lectern-api-unreachable", handleUnreachable);
        };
    }, []);

    return isOffline;
}
