import { create } from "zustand";
import { getTutorial } from "./registry";
import type { Tutorial } from "./types";

interface TutorialRunState {
    /** The tutorial currently playing, or null when idle. */
    active: Tutorial | null;
    /** Index into `active.steps`. */
    stepIndex: number;
    /** True once the user reached the final step and confirmed (for completion). */
    start: (id: string) => void;
    next: () => void;
    prev: () => void;
    goTo: (index: number) => void;
    /** Stop without marking complete (e.g. navigated away). */
    cancel: () => void;
}

export const useTutorialRun = create<TutorialRunState>((set, get) => ({
    active: null,
    stepIndex: 0,
    start: (id) => {
        const tutorial = getTutorial(id);
        if (tutorial) set({ active: tutorial, stepIndex: 0 });
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
