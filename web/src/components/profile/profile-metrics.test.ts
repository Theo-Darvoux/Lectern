import { describe, expect, it } from "vitest";
import { getApprovedShare, getProfileCompletion } from "./profile-metrics";

describe("profile metrics", () => {
  it("calculates completion from the four editable profile fields", () => {
    expect(
      getProfileCompletion({
        display_name: "Ada Lovelace",
        bio: "I collect clear explanations.",
        academic_year: "2A",
        avatar_url: "/avatars/ada.png",
      }),
    ).toBe(100);

    expect(
      getProfileCompletion({
        display_name: "Ada Lovelace",
        bio: "   ",
        academic_year: null,
        avatar_url: null,
      }),
    ).toBe(25);
  });

  it("returns the safe rounded share of all proposals that were approved", () => {
    expect(getApprovedShare(7, 9)).toBe(78);
    expect(getApprovedShare(0, 0)).toBe(0);
    expect(getApprovedShare(4, 2)).toBe(100);
    expect(getApprovedShare(-1, 4)).toBe(0);
  });
});
