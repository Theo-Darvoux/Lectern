"use client";

import { useCallback, useSyncExternalStore } from "react";

function useMediaQuery(query: string): boolean {
    const subscribe = useCallback(
        (callback: () => void) => {
            if (typeof window === "undefined") return () => { };
            const matchMedia = window.matchMedia(query);
            matchMedia.addEventListener("change", callback);
            return () => matchMedia.removeEventListener("change", callback);
        },
        [query]
    );

    const getSnapshot = useCallback(() => {
        if (typeof window === "undefined") return false;
        return window.matchMedia(query).matches;
    }, [query]);

    const getServerSnapshot = useCallback(() => false, []);

    return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export function useIsMobile(): boolean {
    return useMediaQuery("(max-width: 768px)");
}


export function useIsDesktop(): boolean {
    return useMediaQuery("(min-width: 1025px)");
}
