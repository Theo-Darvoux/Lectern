import { describe, expect, it } from "vitest";
import {
    DEFAULT_LOCALE,
    isSupportedLocale,
    loadLocaleMessages,
} from "./locale-messages";

describe("locale message loading", () => {
    it("recognizes only shipped locales", () => {
        expect(DEFAULT_LOCALE).toBe("fr");
        expect(isSupportedLocale("fr")).toBe(true);
        expect(isSupportedLocale("en")).toBe(true);
        expect(isSupportedLocale("de")).toBe(false);
    });

    it("deduplicates lazy catalog loads", async () => {
        const first = loadLocaleMessages("en");
        const second = loadLocaleMessages("en");

        expect(second).toBe(first);
        await expect(first).resolves.toHaveProperty("Login");
    });
});
