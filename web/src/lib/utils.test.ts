import { describe, it, expect } from "vitest";
import { compareNatural, normalizePathname } from "./utils";
import { compareMaterialStatus, getContentStatusRank } from "@/components/content-status-badge";

describe("compareNatural", () => {
  it("orders numbered names by value, not lexicographically", () => {
    const items = ["Chapitre 10", "Chapitre 2", "Chapitre 1"];
    expect([...items].sort(compareNatural)).toEqual([
      "Chapitre 1",
      "Chapitre 2",
      "Chapitre 10",
    ]);
  });

  it("handles numbers with no separator before them", () => {
    const items = ["TD11", "TD2", "TD1"];
    expect([...items].sort(compareNatural)).toEqual(["TD1", "TD2", "TD11"]);
  });

  it("orders multiple numeric runs (version-like strings)", () => {
    const items = ["v1.10", "v1.2", "v1.9"];
    expect([...items].sort(compareNatural)).toEqual(["v1.2", "v1.9", "v1.10"]);
  });

  it("ignores case and diacritics", () => {
    expect(compareNatural("élève", "ELEVE")).toBe(0);
    expect(compareNatural("a", "B")).toBeLessThan(0);
  });
});

describe("compareMaterialStatus", () => {
  it("orders important < current < deprecated < archived", () => {
    const statuses = ["archived", "important", "deprecated", "current"];
    expect([...statuses].sort(compareMaterialStatus)).toEqual([
      "important",
      "current",
      "deprecated",
      "archived",
    ]);
  });

  it("defaults unrecognized or null status to current rank", () => {
    expect(getContentStatusRank(null)).toBe(1);
    expect(getContentStatusRank(undefined)).toBe(1);
    expect(getContentStatusRank("unknown")).toBe(1);
    expect(compareMaterialStatus("important", "unknown")).toBeLessThan(0);
    expect(compareMaterialStatus("unknown", "deprecated")).toBeLessThan(0);
  });
});

describe("normalizePathname", () => {
  it("removes trailing slashes from non-root paths", () => {
    expect(normalizePathname("/setup/")).toBe("/setup");
    expect(normalizePathname("/login///")).toBe("/login");
  });

  it("preserves the root path", () => {
    expect(normalizePathname("/")).toBe("/");
  });
});
