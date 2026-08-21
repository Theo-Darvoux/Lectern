import React, { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { LeaderboardList } from "./leaderboard-list";

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={String(href)} {...props}>{children}</a>
  ),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, values?: Record<string, number>) =>
    values?.count === undefined ? key : `${key}:${values.count}`,
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const entries = [
  {
    rank: 1,
    user_id: "user-1",
    display_name: "Ada",
    avatar_url: null,
    academic_year: "2A",
    approved_contributions: 8,
    annotations: 12,
    score: 104,
  },
];

describe("LeaderboardList", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("renders transparent ranking details in a responsive profile-linked list", () => {
    act(() => root.render(<LeaderboardList entries={entries} currentUserId="user-1" />));

    const list = container.querySelector<HTMLElement>("[data-leaderboard-list]");
    const row = container.querySelector<HTMLElement>("[data-leaderboard-entry]");
    expect(list?.className).toContain("space-y-3");
    expect(row?.className).toContain("sm:grid-cols-");
    expect(row?.getAttribute("data-current-user")).toBe("true");
    expect(container.querySelector('a[href="/profile/user-1"]')).not.toBeNull();
    expect(container.textContent).toContain("approvedCount:8");
    expect(container.textContent).toContain("annotationCount:12");
    expect(container.textContent).toContain("104");
  });
});
