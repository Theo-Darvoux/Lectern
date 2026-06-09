import type { UserBrief } from "@/lib/guest";

/**
 * Capability tiers used to gate tutorials. Not a strict role hierarchy — `guest`
 * is read-only rather than "below" a student — but ordered so a tutorial with a
 * given `minTier` is shown to that tier and every more-privileged one.
 *
 * Mapping (see {@link userTier}):
 *  - guest                      → "guest"
 *  - student                    → "student"
 *  - moderator                  → "staff"
 *  - bureau / vieux             → "admin"  (these are the admin roles)
 *  - pending / unknown          → null     (no tutorials)
 */
export type TutorialTier = "guest" | "student" | "staff" | "admin";

const TIER_ORDER: TutorialTier[] = ["guest", "student", "staff", "admin"];

export function tierRank(tier: TutorialTier): number {
    return TIER_ORDER.indexOf(tier);
}

/** Resolve a user to a tutorial tier, or null if they should see no tutorials. */
export function userTier(user: UserBrief | null | undefined): TutorialTier | null {
    switch (user?.role) {
        case "guest":
            return "guest";
        case "student":
            return "student";
        case "moderator":
            return "staff";
        case "bureau":
        case "vieux":
            return "admin";
        default:
            return null; // pending / logged-out / unknown
    }
}

/** A user qualifies for a tutorial when their tier is at least the minimum. */
export function tierQualifies(
    user: UserBrief | null | undefined,
    minTier: TutorialTier,
): boolean {
    const tier = userTier(user);
    if (!tier) return false;
    return tierRank(tier) >= tierRank(minTier);
}

export type StepPlacement = "top" | "bottom" | "left" | "right" | "center";

export interface TutorialStep {
    /** Stable id within the tutorial (used for i18n keys). */
    id: string;
    /**
     * CSS selector for the element to spotlight. Prefer
     * `[data-tutorial="..."]`. Omit for a centered intro/outro card.
     */
    target?: string;
    placement?: StepPlacement;
    /** Navigate here before the step is shown (e.g. "/browse"). */
    route?: string;
    /** Extra px of breathing room around the highlighted element. */
    spotlightPadding?: number;
    /**
     * If true, poll for the target before showing (for async-rendered UI).
     * Steps whose target never appears are skipped gracefully.
     */
    waitForTarget?: boolean;
    /**
     * Let the user interact with the highlighted element (e.g. click it to
     * proceed). When false (default) the spotlight blocks clicks on the target.
     */
    allowInteraction?: boolean;
}

export interface Tutorial {
    /** kebab-case id; must match the server allowlist + i18n namespace. */
    id: string;
    minTier: TutorialTier;
    /** Lucide icon name resolved in the Help center. */
    icon: string;
    steps: TutorialStep[];
    /**
     * Route prefix that triggers first-visit auto-launch. Omit to make the
     * tutorial replay-only (Help center).
     */
    autoStartOn?: string;
}

/** A measured rectangle in viewport coordinates. */
export interface TargetRect {
    top: number;
    left: number;
    width: number;
    height: number;
}
