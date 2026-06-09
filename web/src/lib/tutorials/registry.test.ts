import { describe, it, expect } from "vitest";
import { TUTORIALS, getTutorial } from "./registry";
import { tierQualifies, userTier, type TutorialTier } from "./types";
import type { UserBrief } from "@/lib/guest";
import en from "../../../messages/en.json";
import fr from "../../../messages/fr.json";

type Messages = { Tutorials: Record<string, unknown> };

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

function hasKey(obj: Record<string, unknown>, path: string[]): boolean {
    let cur: unknown = obj;
    for (const key of path) {
        if (typeof cur !== "object" || cur === null || !(key in cur)) return false;
        cur = (cur as Record<string, unknown>)[key];
    }
    return typeof cur === "string";
}

describe("tutorial registry", () => {
    it("has unique kebab-case ids", () => {
        const ids = TUTORIALS.map((t) => t.id);
        expect(new Set(ids).size).toBe(ids.length);
        for (const id of ids) expect(id).toMatch(/^[a-z0-9-]+$/);
    });

    it("resolves tutorials by id", () => {
        for (const t of TUTORIALS) expect(getTutorial(t.id)).toBe(t);
        expect(getTutorial("does-not-exist")).toBeUndefined();
    });

    it("uses targets only via the data-tutorial convention", () => {
        for (const t of TUTORIALS) {
            for (const step of t.steps) {
                if (step.target) expect(step.target).toMatch(/^\[data-tutorial="[a-z0-9-]+"\]$/);
            }
        }
    });

    it.each(["en", "fr"])("has complete %s translations for every tutorial and step", (lang) => {
        const msgs = (lang === "en" ? en : fr) as unknown as Messages;
        const T = msgs.Tutorials as Record<string, unknown>;
        for (const tut of TUTORIALS) {
            expect(hasKey(T, [tut.id, "title"])).toBe(true);
            expect(hasKey(T, [tut.id, "description"])).toBe(true);
            for (const step of tut.steps) {
                expect(hasKey(T, [tut.id, "steps", step.id, "title"])).toBe(true);
                expect(hasKey(T, [tut.id, "steps", step.id, "body"])).toBe(true);
            }
        }
    });
});

describe("tier gating", () => {
    it("maps roles to tiers", () => {
        expect(userTier(makeUser("guest"))).toBe("guest");
        expect(userTier(makeUser("student"))).toBe("student");
        expect(userTier(makeUser("moderator"))).toBe("staff");
        expect(userTier(makeUser("bureau"))).toBe("admin");
        expect(userTier(makeUser("vieux"))).toBe("admin");
        expect(userTier(makeUser("pending"))).toBeNull();
        expect(userTier(null)).toBeNull();
    });

    it("qualifies a tier and everything above it", () => {
        const cases: [string, TutorialTier, boolean][] = [
            ["guest", "guest", true],
            ["guest", "student", false],
            ["student", "guest", true],
            ["student", "staff", false],
            ["moderator", "staff", true],
            ["moderator", "admin", false],
            ["bureau", "admin", true],
            ["pending", "guest", false],
        ];
        for (const [role, min, expected] of cases) {
            expect(tierQualifies(makeUser(role), min)).toBe(expected);
        }
    });
});
