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

const mockApiFetchRetry = vi.fn();
const mockApiFetch = vi.fn();

vi.mock("@/lib/api-client", () => ({
  apiFetchRetry: (...args: any[]) => mockApiFetchRetry(...args),
  apiFetch: (...args: any[]) => mockApiFetch(...args),
}));

const mockSubscribeToSSE = vi.fn((..._args: any[]) => ({ close: vi.fn() }));
vi.mock("@/lib/sse-client", () => ({
  subscribeToSSE: (...args: any[]) => mockSubscribeToSSE(...args),
}));

import { DirectoryTreeSidebar } from "./directory-tree-sidebar";
import { useUIStore } from "@/lib/stores";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

async function render(node: React.ReactElement) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(node);
  });
}

describe("DirectoryTreeSidebar", () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockApiFetchRetry.mockReset();
    mockApiFetch.mockReset();
    mockSubscribeToSSE.mockClear();
    mockPathname = "/browse/parent-dir/child-dir";
    useUIStore.setState({ treeSidebarOpen: true });

    if (container) {
      container.remove();
    }

    mockApiFetchRetry.mockImplementation(async (url: string) => {
      if (url === "/browse") {
        return {
          directories: [
            {
              id: "dir-parent",
              name: "Parent Folder",
              slug: "parent-dir",
              full_path: "parent-dir",
              child_directory_count: 1,
              child_material_count: 0,
            },
          ],
          materials: [],
        };
      }
      if (url === "/directories/dir-parent/children") {
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

    mockApiFetch.mockImplementation(async (url: string) => {
      if (url === "/browse") {
        return {
          directories: [
            {
              id: "dir-parent",
              name: "Parent Folder",
              slug: "parent-dir",
              full_path: "parent-dir",
              child_directory_count: 1,
              child_material_count: 0,
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

    const initialFetchCount = mockApiFetchRetry.mock.calls.filter(
      ([url]) => url === "/browse",
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

    const finalFetchCount = mockApiFetchRetry.mock.calls.filter(
      ([url]) => url === "/browse",
    ).length;

    // Toggling should NOT trigger new /browse network requests
    expect(finalFetchCount).toBe(initialFetchCount);
  });
});
