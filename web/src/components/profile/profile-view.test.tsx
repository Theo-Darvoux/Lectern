import React, { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { ProfileView, type UserProfile } from "./profile-view";

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={String(href)} {...props}>{children}</a>
  ),
}));

vi.mock("next-intl", () => ({
  useLocale: () => "en",
  useTranslations: () => (key: string) => key,
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/api-client", () => ({ API_BASE: "http://api.test", apiFetch: vi.fn() }));
vi.mock("@/components/profile/contribution-list", () => ({
  ContributionList: () => <div>contribution-list</div>,
}));
vi.mock("@/components/profile/recently-viewed", () => ({
  RecentlyViewed: () => <div>recently-viewed</div>,
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const profile: UserProfile = {
  id: "user-1",
  email: "ada@example.com",
  display_name: "Ada",
  avatar_url: null,
  role: "student",
  bio: null,
  academic_year: null,
  created_at: "2026-01-01T00:00:00Z",
  prs_approved: 7,
  prs_total: 8,
  annotations_count: 2,
  comments_count: 3,
  open_pr_count: 1,
  reputation: 42,
};

describe("ProfileView layout", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <ProfileView
          profile={profile}
          isOwn
          showRecentlyViewed
          onAvatarUpload={vi.fn()}
        />,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    Object.defineProperty(window, "scrollY", { configurable: true, value: 0 });
    vi.restoreAllMocks();
  });

  it("keeps profile editing and activity surfaces visually continuous", async () => {
    const page = container.querySelector<HTMLElement>("[data-profile-page]");
    const profileCard = container.querySelector<HTMLElement>("[data-profile-card]");
    const activity = container.querySelector<HTMLElement>("[data-profile-activity]");
    const avatarControl = container.querySelector<HTMLElement>("[data-avatar-upload-control]");

    expect(page?.className).toContain("bg-background");
    expect(avatarControl?.className).toContain("rounded-full");
    expect(avatarControl?.className).not.toContain("inset-1");
    expect(activity?.className).not.toContain("bg-card");

    const addBio = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "addBio",
    );
    expect(addBio).toBeDefined();

    await act(async () => {
      addBio?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const form = container.querySelector("form");
    expect(form).not.toBeNull();
    expect(profileCard?.contains(form)).toBe(true);
  });

  it("exposes the saved library as a profile action", () => {
    expect(container.querySelector('a[href="/saved"]')).not.toBeNull();
  });

  it("integrates contribution snapshot metrics inside the profile card", () => {
    const profileCard = container.querySelector<HTMLElement>("[data-profile-card]");
    const snapshot = container.querySelector<HTMLElement>('[aria-label="contributionSnapshot"]');
    expect(snapshot).not.toBeNull();
    expect(profileCard?.contains(snapshot)).toBe(true);
  });

  it("keeps every profile region responsive at narrow and wide breakpoints", () => {
    expect(container.querySelector<HTMLElement>("[data-profile-actions]")?.className).toContain("flex-wrap");
    expect(container.querySelector<HTMLElement>("[data-profile-summary-layout]")?.className).toContain("lg:grid-cols-");
    expect(container.querySelector<HTMLElement>("[data-profile-impact]")?.className).toContain("lg:border-l");
    expect(container.querySelector<HTMLElement>("[data-profile-tabs]")?.className).toContain("overflow-x-auto");
    expect(container.querySelector<HTMLElement>("[data-profile-tabs]")?.className).toContain("overflow-y-hidden");

    const materialsTab = [...container.querySelectorAll<HTMLElement>('[role="tab"]')].find(
      (tab) => tab.textContent === "materials",
    );
    expect(materialsTab?.getAttribute("data-state")).toBe("active");
  });

  it("preserves the page position when switching activity categories", async () => {
    Object.defineProperty(window, "scrollY", { configurable: true, value: 640 });
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    const requestAnimationFrame = vi
      .spyOn(window, "requestAnimationFrame")
      .mockImplementation((callback) => {
        callback(Number.MAX_SAFE_INTEGER);
        return 1;
      });
    const recentTab = [...container.querySelectorAll<HTMLElement>('[role="tab"]')].find(
      (tab) => tab.textContent === "recentlyViewed",
    );

    await act(async () => {
      recentTab?.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0 }));
    });

    expect(recentTab?.getAttribute("data-state")).toBe("active");
    expect(scrollTo).toHaveBeenCalledWith({ left: 0, top: 640, behavior: "auto" });
    requestAnimationFrame.mockRestore();
    scrollTo.mockRestore();
  });
});
