import { describe, it, expect, vi, beforeEach } from "vitest";
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { SavedItem } from "@/lib/collections";

const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, prefetch: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/saved",
  useSearchParams: () => new URLSearchParams(),
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
  useTranslations: () => (key: string) => key,
  useLocale: () => "en",
}));

vi.mock("@/lib/api-client", () => ({
  apiFetch: vi.fn(),
}));

vi.mock("@/components/saved/collection-picker", () => ({
  CollectionPicker: () => <div data-testid="collection-picker" />,
}));

import { SavedCard } from "./saved-card";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const testMaterialItem: SavedItem = {
  target_type: "material",
  target_id: "123e4567-e89b-12d3-a456-426614174000",
  title: "Machine Learning Final Exam 2025",
  item_type: "pdf",
  description: "Comprehensive notes covering deep learning and transformers",
  href: "/browse/ai/ml-final-exam",
  added_at: new Date().toISOString(),
  like_count: 12,
  total_views: 154,
};

const testDirectoryItem: SavedItem = {
  target_type: "directory",
  target_id: "223e4567-e89b-12d3-a456-426614174001",
  title: "Advanced Data Structures",
  item_type: "module",
  description: "Folder containing all course materials",
  href: "/browse/cs/advanced-data-structures",
  added_at: new Date().toISOString(),
};

describe("SavedCard", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  it("renders a saved material item with title, description, and stats", async () => {
    await act(async () => {
      root.render(
        <SavedCard
          item={testMaterialItem}
          collectionId={null}
          selected={false}
          selectMode={false}
          onToggleSelect={vi.fn()}
          onRemoved={vi.fn()}
          onCollectionsChanged={vi.fn()}
        />,
      );
    });

    expect(container.textContent).toContain("Machine Learning Final Exam 2025");
    expect(container.textContent).toContain("Comprehensive notes covering deep learning");
    expect(container.textContent).toContain("154");
    expect(container.textContent).toContain("12");
  });

  it("renders a saved directory item correctly", async () => {
    await act(async () => {
      root.render(
        <SavedCard
          item={testDirectoryItem}
          collectionId={null}
          selected={false}
          selectMode={false}
          onToggleSelect={vi.fn()}
          onRemoved={vi.fn()}
          onCollectionsChanged={vi.fn()}
        />,
      );
    });

    expect(container.textContent).toContain("Advanced Data Structures");
    expect(container.textContent).toContain("Folder containing all course materials");
  });

  it("triggers onToggleSelect when selection checkbox is clicked", async () => {
    const onToggleSelect = vi.fn();

    await act(async () => {
      root.render(
        <SavedCard
          item={testMaterialItem}
          collectionId={null}
          selected={false}
          selectMode={true}
          onToggleSelect={onToggleSelect}
          onRemoved={vi.fn()}
          onCollectionsChanged={vi.fn()}
        />,
      );
    });

    const selectButton = container.querySelector('button[aria-label="Select item"]');
    expect(selectButton).toBeDefined();

    await act(async () => {
      selectButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onToggleSelect).toHaveBeenCalledWith(testMaterialItem);
  });
});
