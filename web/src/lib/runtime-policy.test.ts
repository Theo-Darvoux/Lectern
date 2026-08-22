import { describe, expect, it } from "vitest";
import { requiresAppRuntime } from "./runtime-policy";

describe("requiresAppRuntime", () => {
    it.each(["/setup", "/setup/", "/login", "/login/verify/", "/login/reset-password/"])(
        "keeps the app shell out of bootstrap route %s",
        (pathname) => {
            expect(requiresAppRuntime(pathname)).toBe(false);
        },
    );

    it.each(["/", "/browse", "/privacy", "/admin/users", "/onboarding"])(
        "loads the app shell for %s",
        (pathname) => {
            expect(requiresAppRuntime(pathname)).toBe(true);
        },
    );
});
