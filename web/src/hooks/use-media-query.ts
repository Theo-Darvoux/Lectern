"use client";

import { useCallback, useSyncExternalStore } from "react";

// Reuse one MediaQueryList per query string. Without this, every render of
// every component that reads a media query re-parses the query via
// window.matchMedia — and a directory listing renders this hook once per row,
// so the allocations add up to real jank on navigation.
const mqlCache = new Map<string, MediaQueryList>();

function getMediaQueryList(query: string): MediaQueryList | null {
    if (typeof window === "undefined") return null;
    let mql = mqlCache.get(query);
    if (!mql) {
        mql = window.matchMedia(query);
        mqlCache.set(query, mql);
    }
    return mql;
}

function useMediaQuery(query: string): boolean {
    const subscribe = useCallback(
        (callback: () => void) => {
            const mql = getMediaQueryList(query);
            if (!mql) return () => { };
            mql.addEventListener("change", callback);
            return () => mql.removeEventListener("change", callback);
        },
        [query]
    );

    const getSnapshot = useCallback(() => {
        return getMediaQueryList(query)?.matches ?? false;
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
