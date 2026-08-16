import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { isExternalUrl, getDomainFromUrl, normalizeTargetUrl } from "./url-utils";

describe("url-utils", () => {
    const originalLocation = window.location;

    beforeEach(() => {
        // Mock window.location
        delete (window as unknown as { location?: unknown }).location;
        window.location = {
            origin: "https://wikint.example.com",
            host: "wikint.example.com",
            hostname: "wikint.example.com",
            href: "https://wikint.example.com/browse",
        } as unknown as Location;
    });

    afterEach(() => {
        window.location = originalLocation;
    });

    describe("isExternalUrl", () => {
        it("returns false for empty or non-string values", () => {
            expect(isExternalUrl("")).toBe(false);
            expect(isExternalUrl(null)).toBe(false);
            expect(isExternalUrl(undefined)).toBe(false);
        });

        it("returns false for relative internal paths", () => {
            expect(isExternalUrl("/browse/course-1")).toBe(false);
            expect(isExternalUrl("/browse/math/syllabus")).toBe(false);
            expect(isExternalUrl("#section-1")).toBe(false);
            expect(isExternalUrl("./sibling")).toBe(false);
            expect(isExternalUrl("../parent")).toBe(false);
            expect(isExternalUrl("?preview_pr=123")).toBe(false);
        });

        it("returns false for same-origin absolute URLs", () => {
            expect(isExternalUrl("https://wikint.example.com/browse/course-1")).toBe(false);
            expect(isExternalUrl("https://wikint.example.com/materials/123")).toBe(false);
        });

        it("returns true for different-origin absolute URLs", () => {
            expect(isExternalUrl("https://github.com/clubcode")).toBe(true);
            expect(isExternalUrl("https://discord.com/invite/123")).toBe(true);
            expect(isExternalUrl("http://external-site.org/docs")).toBe(true);
            expect(isExternalUrl("//google.com/search")).toBe(true);
        });

        it("returns false for non-web protocols like mailto and tel", () => {
            expect(isExternalUrl("mailto:contact@example.com")).toBe(false);
            expect(isExternalUrl("tel:+1234567890")).toBe(false);
        });
    });

    describe("getDomainFromUrl", () => {
        it("extracts hostname correctly", () => {
            expect(getDomainFromUrl("https://github.com/foo/bar")).toBe("github.com");
            expect(getDomainFromUrl("https://www.youtube.com/watch?v=123")).toBe("youtube.com");
            expect(getDomainFromUrl("discord.gg/abc")).toBe("discord.gg");
        });

        it("returns empty string for empty input", () => {
            expect(getDomainFromUrl("")).toBe("");
            expect(getDomainFromUrl(null)).toBe("");
        });
    });

    describe("normalizeTargetUrl", () => {
        it("preserves relative paths", () => {
            expect(normalizeTargetUrl("/browse/algo")).toBe("/browse/algo");
            expect(normalizeTargetUrl("#heading")).toBe("#heading");
        });

        it("preserves existing schemes", () => {
            expect(normalizeTargetUrl("https://example.com")).toBe("https://example.com");
            expect(normalizeTargetUrl("http://example.com")).toBe("http://example.com");
        });

        it("prepends https:// when missing", () => {
            expect(normalizeTargetUrl("example.com/path")).toBe("https://example.com/path");
            expect(normalizeTargetUrl("github.com")).toBe("https://github.com");
        });
    });
});
