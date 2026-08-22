import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useUIStore } from "@/lib/stores";
import { SearchInline } from "./search-inline";

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) =>
    ({
      searchMaterialsDirs: "Search materials and folders",
      shortcutHint: "Open search",
    })[key] ?? key,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

describe("SearchInline", () => {
  afterEach(() => {
    useUIStore.setState({ searchOpen: false });
  });

  it("is one accessible trigger for the centrally owned search dialog", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    act(() => root.render(<SearchInline />));

    const trigger = container.querySelector("button");
    expect(trigger?.getAttribute("aria-label")).toBe("Search materials and folders");

    act(() => trigger?.click());
    expect(useUIStore.getState().searchOpen).toBe(true);

    act(() => root.unmount());
    container.remove();
  });
});
