import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { LayoutShell } from "./layout-shell";
import { useUIStore } from "@/lib/stores";

let mockPathname = "/browse";
const mockPush = vi.fn();
const mockReplace = vi.fn();

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

vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: {
      id: "user-1",
      email: "test@example.com",
      display_name: "Test User",
      avatar_url: null,
      role: "bureau",
      onboarded: true,
      auto_approve: false,
    },
    isAuthenticated: true,
    isLoading: false,
    bootstrapError: null,
    bootstrapAuth: vi.fn(),
  }),
}));

vi.mock("@/hooks/use-offline", () => ({ useOffline: () => false }));
vi.mock("@/lib/guest", () => ({
  isGuest: () => false,
  isGuestBlockedPath: () => false,
}));

vi.mock("@/components/navbar", () => ({ Navbar: () => <div data-testid="navbar">Navbar</div> }));
vi.mock("@/components/mobile-bottom-bar", () => ({ MobileBottomBar: () => null }));
vi.mock("@/components/footer", () => ({ Footer: () => null }));
vi.mock("@/components/confirm-dialog", () => ({ ConfirmDialog: () => null }));
vi.mock("@/components/pr/staging-fab", () => ({ StagingFab: () => null }));
vi.mock("@/components/pr/review-drawer", () => ({ ReviewDrawer: () => null }));
vi.mock("@/components/pr/global-drop-zone", () => ({ GlobalDropZone: () => null }));
vi.mock("lucide-react", () => ({ WifiOff: () => null }));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("LayoutShell mouse reveal navbar", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    mockPathname = "/browse";
    useUIStore.setState({ hideFooter: false, navbarVisible: true });
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

  it("reveals the navbar when mouse cursor moves to the top of the page (clientY <= 20)", async () => {
    await act(async () => {
      root.render(
        <LayoutShell>
          <div>Content</div>
        </LayoutShell>,
      );
    });

    // Close the navbar
    act(() => {
      useUIStore.getState().setNavbarVisible(false);
    });
    expect(useUIStore.getState().navbarVisible).toBe(false);

    // Mouse movement in middle of screen should not open navbar
    act(() => {
      window.dispatchEvent(new MouseEvent("mousemove", { clientY: 150 }));
    });
    expect(useUIStore.getState().navbarVisible).toBe(false);

    // Mouse movement to top of page (clientY = 10) should open navbar
    act(() => {
      window.dispatchEvent(new MouseEvent("mousemove", { clientY: 10 }));
    });
    expect(useUIStore.getState().navbarVisible).toBe(true);
  });

  it("ignores touch events when moving near the top", async () => {
    await act(async () => {
      root.render(
        <LayoutShell>
          <div>Content</div>
        </LayoutShell>,
      );
    });

    // Close the navbar
    act(() => {
      useUIStore.getState().setNavbarVisible(false);
    });
    expect(useUIStore.getState().navbarVisible).toBe(false);

    // Simulated touch pointer event at the top should not reveal navbar
    act(() => {
      const event = new MouseEvent("mousemove", { clientY: 5 });
      Object.defineProperty(event, "pointerType", { value: "touch" });
      window.dispatchEvent(event);
    });
    expect(useUIStore.getState().navbarVisible).toBe(false);
  });
});
