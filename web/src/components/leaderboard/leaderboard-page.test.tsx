import React, { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { apiFetch } from "@/lib/api-client";
import { LeaderboardPage } from "./leaderboard-page";
import type { LeaderboardResponse } from "./types";

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={String(href)} {...props}>{children}</a>
  ),
}));
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, values?: Record<string, string | number>) =>
    values ? `${key}:${Object.values(values).join(":")}` : key,
}));
vi.mock("@/hooks/use-auth", () => ({ useAuth: () => ({ user: { id: "user-1" } }) }));
vi.mock("@/lib/api-client", () => ({ API_BASE: "http://api.test", apiFetch: vi.fn() }));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function response(name: string, period: LeaderboardResponse["period"]): LeaderboardResponse {
  return {
    items: [{
      rank: 1,
      user_id: "user-1",
      display_name: name,
      avatar_url: null,
      academic_year: "2A",
      approved_contributions: 4,
      annotations: 2,
      score: 40,
    }],
    current_user: null,
    total: 1,
    page: 1,
    pages: 1,
    period,
    academic_year: null,
  };
}

describe("LeaderboardPage", () => {
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
    vi.clearAllMocks();
  });

  it("keeps the current ranking visible while a different period loads", async () => {
    let resolveSemester: ((value: LeaderboardResponse) => void) | undefined;
    const semester = new Promise<LeaderboardResponse>((resolve) => {
      resolveSemester = resolve;
    });
    vi.mocked(apiFetch)
      .mockResolvedValueOnce(response("Ada", "month"))
      .mockReturnValueOnce(semester);

    await act(async () => {
      root.render(<LeaderboardPage />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const semesterButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent === "periods.semester");
    expect(semesterButton).toBeDefined();

    await act(async () => {
      semesterButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(apiFetch).toHaveBeenLastCalledWith(
      expect.stringContaining("period=semester"),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(container.textContent).toContain("Ada");
    expect(container.querySelector("[data-leaderboard-list]")).not.toBeNull();
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();

    await act(async () => {
      resolveSemester?.(response("Grace", "semester"));
      await semester;
    });
    expect(container.textContent).toContain("Grace");
  });
});
