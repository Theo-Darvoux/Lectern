import { describe, it, expect } from "vitest";
import { isGuest, isGuestBlockedPath, GUEST_ROLE, type UserBrief } from "@/lib/guest";

function makeUser(role: string): UserBrief {
    return {
        id: "1",
        email: "x@example.com",
        display_name: null,
        avatar_url: null,
        role,
        onboarded: true,
        auto_approve: false,
    };
}

describe("isGuest", () => {
    it("is true only for the guest role", () => {
        expect(isGuest(makeUser(GUEST_ROLE))).toBe(true);
    });

    it("is false for other roles", () => {
        for (const role of ["student", "moderator", "bureau", "vieux", "pending"]) {
            expect(isGuest(makeUser(role))).toBe(false);
        }
    });

    it("is false for null / undefined", () => {
        expect(isGuest(null)).toBe(false);
        expect(isGuest(undefined)).toBe(false);
    });
});

describe("isGuestBlockedPath", () => {
    it("blocks the guest's own profile, settings, PRs, notifications, onboarding", () => {
        const blocked = [
            "/profile",
            "/settings",
            "/settings/preferences",
            "/pull-requests",
            "/pull-requests/abc",
            "/notifications",
            "/onboarding",
        ];
        for (const p of blocked) expect(isGuestBlockedPath(p)).toBe(true);
    });

    it("blocks QCM authoring routes", () => {
        expect(isGuestBlockedPath("/qcm/new")).toBe(true);
        expect(isGuestBlockedPath("/qcm/preview")).toBe(true);
        expect(isGuestBlockedPath("/qcm/123/edit")).toBe(true);
    });

    it("allows browsing, other users' profiles, and QCM viewing", () => {
        const allowed = [
            "/",
            "/browse",
            "/browse/cours/math",
            "/profile/some-user-id",
            "/qcm/123",
            "/popular",
        ];
        for (const p of allowed) expect(isGuestBlockedPath(p)).toBe(false);
    });
});
