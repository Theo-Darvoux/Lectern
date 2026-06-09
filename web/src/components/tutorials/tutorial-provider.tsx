"use client";

import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import { useAuthStore } from "@/lib/stores";
import { TUTORIALS } from "@/lib/tutorials/registry";
import { tierQualifies } from "@/lib/tutorials/types";
import { useTutorialRun } from "@/lib/tutorials/tutorial-store";
import { tutorialsEnabled } from "@/lib/tutorials/use-tutorial";
import { isGuest } from "@/lib/guest";
import { safeLocalStorage } from "@/lib/safe-storage";
import { TutorialOverlay } from "./tutorial-overlay";

const GUEST_STORAGE_KEY = "lectern.tutorials.completed";

/** Read completion without the hook (avoids re-render churn in the provider). */
function readCompleted(user: ReturnType<typeof useAuthStore.getState>["user"]): string[] {
    if (!user) return [];
    if (isGuest(user)) {
        const raw = safeLocalStorage.getItem(GUEST_STORAGE_KEY);
        try {
            const parsed: unknown = raw ? JSON.parse(raw) : [];
            return Array.isArray(parsed) ? (parsed as string[]) : [];
        } catch {
            return [];
        }
    }
    return user.completed_tutorials ?? [];
}

function autoMatches(autoStartOn: string, pathname: string): boolean {
    if (autoStartOn === "/") return pathname === "/";
    return pathname === autoStartOn || pathname.startsWith(autoStartOn + "/");
}

/**
 * Globally mounts the tutorial overlay and auto-launches a tutorial the first
 * time a qualifying user lands on its `autoStartOn` route. Each tutorial is
 * auto-attempted at most once per session to avoid nagging after a dismissal.
 */
export function TutorialProvider() {
    const pathname = usePathname();
    const user = useAuthStore((s) => s.user);
    const isLoading = useAuthStore((s) => s.isLoading);
    const active = useTutorialRun((s) => s.active);
    const start = useTutorialRun((s) => s.start);
    const attempted = useRef<Set<string>>(new Set());

    useEffect(() => {
        if (!tutorialsEnabled() || isLoading || !user || active) return;
        const completed = readCompleted(user);
        const candidate = TUTORIALS.find(
            (t) =>
                t.autoStartOn != null &&
                autoMatches(t.autoStartOn, pathname) &&
                tierQualifies(user, t.minTier) &&
                !completed.includes(t.id) &&
                !attempted.current.has(t.id),
        );
        if (!candidate) return;
        attempted.current.add(candidate.id);
        // Let the page settle before spotlighting.
        const timer = window.setTimeout(() => start(candidate.id, user), 900);
        return () => window.clearTimeout(timer);
    }, [pathname, user, isLoading, active, start]);

    if (!tutorialsEnabled()) return null;
    return <TutorialOverlay />;
}
