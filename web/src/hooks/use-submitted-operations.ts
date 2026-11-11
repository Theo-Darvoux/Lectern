"use client";

import { useMemo } from "react";
import { pendingOperations, usePendingContributionsStore } from "@/lib/pending-contributions";
import type { Operation } from "@/lib/staging-store";
import { useAuthStore } from "@/lib/stores";

/**
 * Operations of the signed-in user's submitted, not yet applied contributions,
 * so the tree and listings can show them before the server confirms.
 */
export function useSubmittedOperations(): Operation[] {
    const contributions = usePendingContributionsStore((s) => s.contributions);
    const ownerId = usePendingContributionsStore((s) => s.ownerId);
    const currentOwnerId = useAuthStore((s) => (s.user?.id ? String(s.user.id) : null));
    return useMemo(
        () => (ownerId === currentOwnerId ? pendingOperations({ contributions }) : []),
        [contributions, ownerId, currentOwnerId],
    );
}
