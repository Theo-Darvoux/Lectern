"use client";

import { useEffect, useState } from "react";

/**
 * Hook to track browser online/offline status (U4).
 */
export function useOffline() {
    const [isOffline, setIsOffline] = useState(
        typeof navigator !== "undefined" ? !navigator.onLine : false
    );

    useEffect(() => {
        const handleOnline = () => setIsOffline(false);
        const handleOffline = () => {
            // Only set to offline if navigator also says so,
            // to avoid transient glitches from other tabs.
            if (typeof navigator !== "undefined" && !navigator.onLine) {
                setIsOffline(true);
            }
        };
        const handleReachable = () => setIsOffline(false);

        window.addEventListener("online", handleOnline);
        window.addEventListener("offline", handleOffline);
        window.addEventListener("wikint-api-reachable", handleReachable);

        return () => {
            window.removeEventListener("online", handleOnline);
            window.removeEventListener("offline", handleOffline);
            window.removeEventListener("wikint-api-reachable", handleReachable);
        };
    }, []);

    return isOffline;
}
