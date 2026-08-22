import React, { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { apiFetch } from "@/lib/api-client";
import { LeaderboardPreview } from "./leaderboard-preview";

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={String(href)} {...props}>{children}</a>
  ),
}));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/hooks/use-auth", () => ({ useAuth: () => ({ user: { id: "user-1" } }) }));
vi.mock("@/lib/api-client", () => ({ API_BASE: "http://api.test", apiFetch: vi.fn() }));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("LeaderboardPreview", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    vi.mocked(apiFetch).mockResolvedValue({
      items: [{
        rank: 1,
        user_id: "user-1",
        display_name: "Ada",
        avatar_url: null,
        academic_year: "2A",
        approved_contributions: 4,
        annotations: 2,
        score: 44,
      }],
      current_user: null,
      total: 1,
      page: 1,
      pages: 1,
      period: "month",
      academic_year: null,
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  it("loads a compact monthly ranking with links to the full board and profiles", async () => {
    await act(async () => {
      root.render(<LeaderboardPreview />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(apiFetch).toHaveBeenCalledWith(
      "/leaderboard?period=month&limit=5&page=1",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(container.querySelector('a[href="/leaderboard"]')).not.toBeNull();
    expect(container.querySelector('a[href="/profile/user-1"]')).not.toBeNull();
    expect(container.textContent).toContain("44");
  });
});
