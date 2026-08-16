import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { HeroBar } from "./hero-bar";
import type { UserBrief } from "@/lib/guest";

let mockUser: UserBrief | null = null;

vi.mock("next/link", () => ({
  default: ({ href, children, className }: { href: string; children: React.ReactNode; className?: string }) => (
    <a href={href} className={className} data-testid={`link-${href}`}>
      {children}
    </a>
  ),
}));

vi.mock("@/components/search/search-inline", () => ({
  SearchInline: () => <div data-testid="search-inline">Search</div>,
}));

vi.mock("@/components/moderator/add-featured-dialog", () => ({
  AddFeaturedDialog: ({ open, onSuccess }: { open: boolean; onSuccess: () => void }) =>
    open ? (
      <div data-testid="add-featured-dialog">
        <span>Add Featured Dialog</span>
        <button onClick={onSuccess} data-testid="dialog-success-btn">
          Submit
        </button>
      </div>
    ) : null,
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => {
    const translations: Record<string, string> = {
      quickBrowse: "Explorer",
      quickFeatured: "Mettre en avant",
    };
    return translations[key] ?? key;
  },
}));

vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: mockUser,
    isAuthenticated: !!mockUser,
    isLoading: false,
    bootstrapError: null,
    bootstrapAuth: vi.fn(),
  }),
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("HeroBar", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    mockUser = null;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    if (root) {
      act(() => root.unmount());
    }
    container?.remove();
  });

  it("renders only browse button for regular user", async () => {
    mockUser = {
      id: "user-1",
      email: "user@example.com",
      display_name: "Regular User",
      avatar_url: null,
      role: "user",
      onboarded: true,
      auto_approve: false,
    };

    await act(async () => {
      root.render(
        <HeroBar
          greeting="Bonjour"
          displayName="Regular User"
          subtitle="Voici ce qui se passe sur Intellect"
        />,
      );
    });

    const browseLink = container.querySelector('a[href="/browse"]');
    expect(browseLink).not.toBeNull();
    expect(browseLink?.textContent).toContain("Explorer");

    const buttons = Array.from(container.querySelectorAll("button"));
    const featuredButton = buttons.find((btn) => btn.textContent?.includes("Mettre en avant"));
    expect(featuredButton).toBeUndefined();
  });

  it("renders browse link and featured button for moderator, and clicking opens modal", async () => {
    mockUser = {
      id: "user-mod",
      email: "mod@example.com",
      display_name: "Mod User",
      avatar_url: null,
      role: "moderator",
      onboarded: true,
      auto_approve: false,
    };

    const handleFeaturedSuccess = vi.fn();

    await act(async () => {
      root.render(
        <HeroBar
          greeting="Bonjour"
          displayName="Mod User"
          subtitle="Voici ce qui se passe sur Intellect"
          onFeaturedSuccess={handleFeaturedSuccess}
        />,
      );
    });

    const browseLink = container.querySelector('a[href="/browse"]');
    expect(browseLink).not.toBeNull();
    expect(browseLink?.textContent).toContain("Explorer");

    // Modal is initially closed
    expect(container.querySelector('[data-testid="add-featured-dialog"]')).toBeNull();

    const buttons = Array.from(container.querySelectorAll("button"));
    const featuredButton = buttons.find((btn) => btn.textContent?.includes("Mettre en avant"));
    expect(featuredButton).toBeDefined();

    // Click featured button to open modal
    await act(async () => {
      featuredButton?.click();
    });

    expect(container.querySelector('[data-testid="add-featured-dialog"]')).not.toBeNull();

    // Trigger success callback
    const successBtn = container.querySelector('[data-testid="dialog-success-btn"]') as HTMLButtonElement;
    await act(async () => {
      successBtn?.click();
    });

    expect(handleFeaturedSuccess).toHaveBeenCalledTimes(1);
  });

  it("renders featured button for bureau and vieux roles as well", async () => {
    mockUser = {
      id: "user-bureau",
      email: "bureau@example.com",
      display_name: "Bureau User",
      avatar_url: null,
      role: "bureau",
      onboarded: true,
      auto_approve: false,
    };

    await act(async () => {
      root.render(
        <HeroBar
          greeting="Bonjour"
          displayName="Bureau User"
          subtitle="Voici ce qui se passe sur Intellect"
        />,
      );
    });

    const buttons = Array.from(container.querySelectorAll("button"));
    const featuredButton = buttons.find((btn) => btn.textContent?.includes("Mettre en avant"));
    expect(featuredButton).toBeDefined();
  });
});
