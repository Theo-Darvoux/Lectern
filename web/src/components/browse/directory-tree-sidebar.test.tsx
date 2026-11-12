import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";

let mockPathname = "/browse/parent-dir/child-dir";
const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, prefetch: vi.fn(), replace: vi.fn() }),
  usePathname: () => mockPathname,
}));

vi.mock("next/link", () => ({
  __esModule: true,
  default: React.forwardRef(function MockLink(
    { href, children, onClick, ...rest }: { href: string; children: React.ReactNode; onClick?: (e: React.MouseEvent) => void },
    ref: React.Ref<HTMLAnchorElement>,
  ) {
    return React.createElement("a", { href, ref, onClick, ...rest }, children);
  }),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => {
    const t = (key: string) => key;
    (t as unknown as { has: () => boolean }).has = () => false;
    return t;
  },
}));

const mockApiFetch = vi.fn();

vi.mock("@/lib/api-client", () => ({
  apiFetch: (...args: any[]) => mockApiFetch(...args),
  isRetriableError: () => false,
}));

const mockSubscribeToSSE = vi.fn((..._args: any[]) => ({ close: vi.fn() }));
vi.mock("@/lib/sse-client", () => ({
  subscribeToSSE: (...args: any[]) => mockSubscribeToSSE(...args),
}));

import { QueryClientProvider } from "@tanstack/react-query";
import { DirectoryTreeSidebar } from "./directory-tree-sidebar";
import { useUIStore } from "@/lib/stores";
import { getQueryClient } from "@/lib/query-client";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

async function render(node: React.ReactElement) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      React.createElement(QueryClientProvider, { client: getQueryClient() }, node),
    );
  });
}

describe("DirectoryTreeSidebar", () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockApiFetch.mockReset();
    getQueryClient().clear();
    mockSubscribeToSSE.mockClear();
    mockPathname = "/browse/parent-dir/child-dir";
    useUIStore.setState({ treeSidebarOpen: true });

    if (container) {
      container.remove();
    }

    mockApiFetch.mockImplementation(async (url: string) => {
      if (url === "/directories/root/tree") {
        return {
          directories: [
            {
              id: "dir-parent",
              name: "Parent Folder",
              slug: "parent-dir",
              full_path: "parent-dir",
              child_directory_count: 1,
              child_material_count: 0,
              parent_id: null,
            },
          ],
          materials: [],
        };
      }
      if (url === "/directories/dir-parent/tree") {
        return {
          directories: [
            {
              id: "dir-child",
              name: "Child Folder",
              slug: "child-dir",
              full_path: "parent-dir/child-dir",
              child_directory_count: 0,
              child_material_count: 0,
              parent_id: "dir-parent",
            },
          ],
          materials: [],
        };
      }
      return { directories: [], materials: [] };
    });
  });

  afterEach(() => {
    if (root) {
      act(() => {
        root.unmount();
      });
    }
  });

  it("auto-expands parent folder for active route and keeps it collapsed when user closes it", async () => {
    await render(<DirectoryTreeSidebar />);

    // Wait for initial async tree resolution
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    // Parent and child should be rendered initially
    expect(container.textContent).toContain("Parent Folder");
    expect(container.textContent).toContain("Child Folder");

    // Find the toggle button (chevron) for Parent Folder
    const toggleButtons = container.querySelectorAll("button[aria-expanded]");
    expect(toggleButtons.length).toBeGreaterThan(0);
    const parentToggleBtn = toggleButtons[0] as HTMLButtonElement;
    expect(parentToggleBtn.getAttribute("aria-expanded")).toBe("true");

    // Collapse the parent folder
    await act(async () => {
      parentToggleBtn.click();
    });

    // Parent folder should now be collapsed
    expect(parentToggleBtn.getAttribute("aria-expanded")).toBe("false");
    expect(container.textContent).not.toContain("Child Folder");

    // Wait and verify it stays collapsed and does NOT immediately reopen
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 100));
    });

    expect(parentToggleBtn.getAttribute("aria-expanded")).toBe("false");
    expect(container.textContent).not.toContain("Child Folder");
  });

  it("does not refetch root on every expand or collapse toggle", async () => {
    await render(<DirectoryTreeSidebar />);

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    const initialFetchCount = mockApiFetch.mock.calls.filter(
      ([url]) => url === "/directories/root/tree",
    ).length;

    const toggleButtons = container.querySelectorAll("button[aria-expanded]");
    const parentToggleBtn = toggleButtons[0] as HTMLButtonElement;

    // Toggle collapse
    await act(async () => {
      parentToggleBtn.click();
    });

    // Toggle expand
    await act(async () => {
      parentToggleBtn.click();
    });

    const finalFetchCount = mockApiFetch.mock.calls.filter(
      ([url]) => url === "/directories/root/tree",
    ).length;

    // Toggling should NOT trigger new /browse network requests
    expect(finalFetchCount).toBe(initialFetchCount);
  });
});
