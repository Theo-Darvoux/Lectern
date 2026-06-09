import { create } from "zustand";
import type { UserBrief } from "@/lib/guest";
import { getTutorial } from "./registry";
import { tierQualifies, type Tutorial } from "./types";

interface TutorialRunState {
    /** The tutorial currently playing, or null when idle. */
    active: Tutorial | null;
    /** Index into `active.steps`. */
    stepIndex: number;
    /** True once the user reached the final step and confirmed (for completion). */
    start: (id: string, ctx: { user: UserBrief | null | undefined; isMobile: boolean }) => void;
    next: () => void;
    prev: () => void;
    goTo: (index: number) => void;
    /** Stop without marking complete (e.g. navigated away). */
    cancel: () => void;
}

export const useTutorialRun = create<TutorialRunState>((set, get) => ({
    active: null,
    stepIndex: 0,
    start: (id, { user, isMobile }) => {
        const tutorial = getTutorial(id);
        if (!tutorial) return;
        // Drop steps the viewer can't see (their target never renders), so the
        // engine doesn't blank out polling for an absent element.
        const steps = tutorial.steps.filter((s) => {
            if (s.minTier && !tierQualifies(user, s.minTier)) return false;
            if (s.only === "desktop" && isMobile) return false;
            if (s.only === "mobile" && !isMobile) return false;
            return true;
        });
        set({ active: { ...tutorial, steps }, stepIndex: 0 });
    },
    next: () => {
        const { active, stepIndex } = get();
        if (!active) return;
        if (stepIndex < active.steps.length - 1) {
            set({ stepIndex: stepIndex + 1 });
        }
    },
    prev: () => {
        const { stepIndex } = get();
        if (stepIndex > 0) set({ stepIndex: stepIndex - 1 });
    },
    goTo: (index) => {
        const { active } = get();
        if (!active) return;
        const clamped = Math.max(0, Math.min(index, active.steps.length - 1));
        set({ stepIndex: clamped });
    },
    cancel: () => set({ active: null, stepIndex: 0 }),
}));

/**
 * True when the active tutorial step asks for the menu with this id to be open.
 * Dropdown components tie their `open` state to this so the tour can reveal the
 * items inside (e.g. the "New content" or profile menu).
 */
export function useTutorialMenuOpen(menuId: string): boolean {
    return useTutorialRun((s) => s.active?.steps[s.stepIndex]?.openMenu === menuId);
}
