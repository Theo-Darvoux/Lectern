"use client";

import { useCallback, useMemo } from "react";
import { apiFetch } from "@/lib/api-client";
import { safeLocalStorage } from "@/lib/safe-storage";
import { isGuest, type UserBrief } from "@/lib/guest";
import { useAuthStore } from "@/lib/stores";
import { TUTORIALS } from "./registry";
import { useTutorialRun } from "./tutorial-store";
import { tierQualifies, type Tutorial } from "./types";

/** Build-time kill-switch. `off`/`false`/`0` disable the whole feature. */
export function tutorialsEnabled(): boolean {
    const v = process.env.NEXT_PUBLIC_TUTORIALS;
    if (v == null || v === "") return true;
    return !["off", "false", "0", "no"].includes(v.toLowerCase());
}

const GUEST_STORAGE_KEY = "lectern.tutorials.completed";

function readGuestCompleted(): string[] {
    const raw = safeLocalStorage.getItem(GUEST_STORAGE_KEY);
    if (!raw) return [];
    try {
        const parsed: unknown = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
    } catch {
        return [];
    }
}

function writeGuestCompleted(ids: string[]): void {
    safeLocalStorage.setItem(GUEST_STORAGE_KEY, JSON.stringify(ids));
}

/**
 * Single entry point for tutorial state. Persists completion server-side for
 * logged-in users and in localStorage for guests, and exposes launch/complete/
 * reset plus the set of tutorials the current user qualifies for.
 */
export function useTutorial() {
    const { user, setUser } = useAuthStore();
    const start = useTutorialRun((s) => s.start);

    const completed = useMemo<string[]>(() => {
        if (!user) return [];
        if (isGuest(user)) return readGuestCompleted();
        return user.completed_tutorials ?? [];
    }, [user]);

    const isCompleted = useCallback((id: string) => completed.includes(id), [completed]);

    /** Tutorials the current user is allowed to see, in registry order. */
    const available = useMemo<Tutorial[]>(
        () => TUTORIALS.filter((t) => tierQualifies(user, t.minTier)),
        [user],
    );

    const launch = useCallback((id: string) => {
        if (tutorialsEnabled()) start(id);
    }, [start]);

    const markComplete = useCallback(
        async (id: string) => {
            if (!user || isCompleted(id)) return;
            if (isGuest(user)) {
                writeGuestCompleted([...readGuestCompleted(), id]);
                // Mirror onto the in-memory user so reactive consumers update.
                setUser({ ...user, completed_tutorials: [...completed, id] } as UserBrief);
                return;
            }
            // Optimistic local update; reconcile from the server response.
            setUser({ ...user, completed_tutorials: [...completed, id] });
            try {
                const updated = await apiFetch<UserBrief>("/users/me/tutorials/complete", {
                    method: "POST",
                    body: JSON.stringify({ tutorial_id: id }),
                });
                setUser(updated);
            } catch {
                // Keep the optimistic value; a later /me refresh will reconcile.
            }
        },
        [user, completed, isCompleted, setUser],
    );

    const resetAll = useCallback(async () => {
        if (!user) return;
        if (isGuest(user)) {
            writeGuestCompleted([]);
            setUser({ ...user, completed_tutorials: [] } as UserBrief);
            return;
        }
        setUser({ ...user, completed_tutorials: [] });
        try {
            const updated = await apiFetch<UserBrief>("/users/me/tutorials", { method: "DELETE" });
            setUser(updated);
        } catch {
            // ignore — optimistic state stands
        }
    }, [user, setUser]);

    return { available, completed, isCompleted, launch, markComplete, resetAll };
}
