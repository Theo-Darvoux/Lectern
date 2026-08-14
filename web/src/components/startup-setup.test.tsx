import { beforeEach, describe, expect, it, vi } from "vitest";
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { UserBrief } from "@/lib/guest";

let mockPathname = "/setup/";
const mockPush = vi.fn();
const mockReplace = vi.fn();
const mockBootstrapAuth = vi.fn();
const mockApiFetchRetry = vi.fn();
let mockAuthState: {
  user: UserBrief | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  bootstrapError: string | null;
  bootstrapAuth: typeof mockBootstrapAuth;
} = {
  user: null,
  isAuthenticated: false,
  isLoading: true,
  bootstrapError: null as string | null,
  bootstrapAuth: mockBootstrapAuth,
};

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({ push: mockPush, replace: mockReplace }),
}));

vi.mock("next/dynamic", () => ({
  default: () => () => null,
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

vi.mock("@/lib/api-client", () => ({
  apiFetchRetry: (...args: unknown[]) => mockApiFetchRetry(...args),
}));

vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => mockAuthState,
}));

vi.mock("@/hooks/use-offline", () => ({ useOffline: () => false }));
vi.mock("@/lib/auth-sync", () => ({ initAuthSync: () => () => {} }));
vi.mock("@/lib/guest", () => ({
  isGuest: () => false,
  isGuestBlockedPath: () => false,
}));
vi.mock("@/lib/fonts", () => ({
  parseSegments: () => null,
  buildFontsUrlForNames: () => null,
}));

vi.mock("@/components/background-watermark", () => ({ BackgroundWatermark: () => null }));
vi.mock("@/components/navbar", () => ({ Navbar: () => null }));
vi.mock("@/components/mobile-bottom-bar", () => ({ MobileBottomBar: () => null }));
vi.mock("@/components/footer", () => ({ Footer: () => null }));
vi.mock("@/components/confirm-dialog", () => ({ ConfirmDialog: () => null }));
vi.mock("@/components/cookie-banner", () => ({ CookieBanner: () => null }));
vi.mock("@/components/pr/staging-fab", () => ({ StagingFab: () => null }));
vi.mock("@/components/pr/review-drawer", () => ({ ReviewDrawer: () => null }));
vi.mock("@/components/pr/global-drop-zone", () => ({ GlobalDropZone: () => null }));
vi.mock("lucide-react", () => ({ WifiOff: () => null }));

import { ConfigProvider } from "./config-provider";
import { AuthBootstrap } from "./auth-bootstrap";
import { LayoutShell } from "./layout-shell";
import { useConfigStore, useUIStore } from "@/lib/stores";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const freshConfig = {
  needs_setup: true,
  bootstrap_token_required: false,
  site_name: "Lectern",
  site_name_style: null,
  site_description: "",
  site_logo_url: null,
  site_favicon_url: null,
  primary_color: "",
  footer_text: "",
  footer_logo_url: null,
  organization_url: null,
  repo_url: null,
  eurooffice_public_url: null,
  og_image_url: null,
  bg_watermark_url: null,
  bg_watermark_opacity_light: null,
  bg_watermark_opacity_dark: null,
  legal_name: null,
  legal_address: null,
  legal_siret: null,
  contact_email: null,
  dpo_email: null,
  dpo_address: null,
  data_transfers: null,
  legal_version: null,
  totp_enabled: false,
  google_enabled: false,
  google_client_id: null,
  classic_enabled: false,
  allow_all_domains: false,
  guest_access_enabled: false,
  tutorials_enabled: false,
  allow_external_document_links: true,
  max_contribution_note_length: 10000,
};

const configuredConfig = { ...freshConfig, needs_setup: false };

let container: HTMLDivElement;
let root: Root;

async function renderStartup() {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <ConfigProvider>
        <AuthBootstrap />
        <LayoutShell>
          <div data-testid="setup-ui">Setup UI</div>
        </LayoutShell>
      </ConfigProvider>,
    );
  });
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function cleanup() {
  if (root) {
    act(() => root.unmount());
  }
  container?.remove();
}

describe("clean-install /setup startup", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockPathname = "/setup/";
    mockAuthState = {
      user: null,
      isAuthenticated: false,
      isLoading: true,
      bootstrapError: null,
      bootstrapAuth: mockBootstrapAuth,
    };
    useConfigStore.setState({ config: null });
    useUIStore.setState({ hideFooter: false, navbarVisible: true });
  });

  it("renders setup after installation detection even while auth is unresolved", async () => {
    mockApiFetchRetry.mockResolvedValueOnce(freshConfig);

    await renderStartup();
    await flush();

    expect(container.textContent).toContain("Setup UI");
    expect(container.textContent).not.toContain("recoveringSession");
    expect(mockPush).not.toHaveBeenCalledWith(expect.stringContaining("/login"));
    expect(mockBootstrapAuth).not.toHaveBeenCalled();
    expect(mockApiFetchRetry).toHaveBeenCalledWith(
      "/auth/methods",
      expect.objectContaining({
        skipAuth: true,
        timeoutMs: 5000,
        retries: 1,
      }),
    );

    cleanup();
  });

  it("settles into an actionable retry state when installation detection fails", async () => {
    mockApiFetchRetry
      .mockRejectedValueOnce(new TypeError("network down"))
      .mockResolvedValueOnce(freshConfig);

    await renderStartup();
    await flush();

    expect(container.textContent).toContain("installationCheckFailedTitle");
    expect(container.textContent).toContain("installationCheckFailedDescription");
    const retry = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "retry",
    );
    expect(retry).toBeDefined();

    await act(async () => {
      retry!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();

    expect(container.textContent).toContain("Setup UI");
    expect(mockApiFetchRetry).toHaveBeenCalledTimes(2);

    cleanup();
  });


  it("redirects an initialized unauthenticated installation to login instead of remaining terminally loading", async () => {
    mockPathname = "/browse/";
    mockAuthState = {
      ...mockAuthState,
      isLoading: false,
    };
    useConfigStore.setState({ config: configuredConfig });
    mockApiFetchRetry.mockResolvedValueOnce(configuredConfig);

    await renderStartup();
    await flush();

    expect(mockPush).toHaveBeenCalledWith("/login?next=%2Fbrowse");
    expect(container.textContent).not.toContain("startupErrorTitle");

    cleanup();
  });

  it("renders protected content for a valid restored session", async () => {
    mockPathname = "/browse/";
    mockAuthState = {
      ...mockAuthState,
      user: {
        id: "user-1",
        email: "admin@example.com",
        display_name: "Admin",
        avatar_url: null,
        role: "bureau",
        onboarded: true,
        auto_approve: false,
      },
      isAuthenticated: true,
      isLoading: false,
    };
    useConfigStore.setState({ config: configuredConfig });
    mockApiFetchRetry.mockResolvedValueOnce(configuredConfig);

    await renderStartup();
    await flush();

    expect(container.textContent).toContain("Setup UI");
    expect(container.textContent).not.toContain("recoveringSession");
    expect(mockPush).not.toHaveBeenCalledWith(expect.stringContaining("/login"));

    cleanup();
  });

  it("shows a retryable startup error instead of redirecting when auth bootstrap failed", async () => {
    mockPathname = "/browse/";
    mockAuthState = {
      ...mockAuthState,
      isLoading: false,
      bootstrapError: "network down",
    };
    useConfigStore.setState({ config: configuredConfig });
    mockApiFetchRetry.mockResolvedValueOnce(configuredConfig);

    await renderStartup();
    await flush();

    expect(container.textContent).toContain("startupErrorTitle");
    expect(mockPush).not.toHaveBeenCalledWith(expect.stringContaining("/login"));
    const retry = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "retry",
    );
    expect(retry).toBeDefined();

    await act(async () => {
      retry!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockBootstrapAuth).toHaveBeenCalledTimes(1);

    cleanup();
  });
});
