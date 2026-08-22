export interface UserBrief {
    id: string;
    email: string;
    display_name: string | null;
    avatar_url: string | null;
    role: string;
    onboarded: boolean;
    auto_approve: boolean;
    /** Whether this account can use classic email + password login. */
    has_password?: boolean;
    /** Tutorial IDs the user has finished (server-persisted). Absent for guests. */
    completed_tutorials?: string[];
}

export const GUEST_ROLE = "guest";

/** A guest is a read-only visitor with no real profile. */
export function isGuest(user: UserBrief | null | undefined): boolean {
    return user?.role === GUEST_ROLE;
}

/** Staff members (moderator, bureau, vieux) have content management rights. */
export function isStaff(user: UserBrief | null | undefined): boolean {
    return user?.role === "moderator" || user?.role === "bureau" || user?.role === "vieux";
}

/**
 * Routes a read-only guest may not visit: their own profile, settings,
 * notifications, pull requests, onboarding, and QCM authoring. Other users'
 * profiles (`/profile/<id>`) and QCM viewing (under `/browse`) remain allowed.
 */
export function isGuestBlockedPath(pathname: string): boolean {
    return (
        pathname === "/profile" ||
        pathname === "/onboarding" ||
        pathname.startsWith("/settings") ||
        pathname.startsWith("/pull-requests") ||
        pathname.startsWith("/notifications") ||
        pathname === "/qcm/new" ||
        pathname === "/qcm/preview" ||
        (pathname.startsWith("/qcm/") && pathname.endsWith("/edit"))
    );
}
