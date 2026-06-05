import { describe, it, expect } from "vitest";
import { compareNatural } from "./utils";

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
