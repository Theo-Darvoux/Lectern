import React, { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { apiFetch } from "@/lib/api-client";
import PopularPage from "./page";
import type { MaterialDetail } from "@/components/home/types";
import { settleQueries, withQueryClient } from "@/test/query";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));
vi.mock("@/lib/stores", () => ({ useConfigStore: () => ({ site_name: "Lectern" }) }));
vi.mock("@/lib/api-client", () => ({ apiFetch: vi.fn() }));
vi.mock("@/components/home/material-card", () => ({
  MaterialCard: ({ material }: { material: MaterialDetail }) => <article>{material.title}</article>,
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const material = (title: string) => ({ id: title, title }) as MaterialDetail;

describe("PopularPage", () => {
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

  it("keeps popular materials visible while a different period loads", async () => {
    let resolveFortnight: ((items: MaterialDetail[]) => void) | undefined;
    const fortnight = new Promise<MaterialDetail[]>((resolve) => {
      resolveFortnight = resolve;
    });
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([material("Algorithms")])
      .mockReturnValueOnce(fortnight);

    await act(async () => {
      root.render(withQueryClient(<PopularPage />));
    });
    await settleQueries();

    const fortnightButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("last14Days"));
    expect(fortnightButton).toBeDefined();

    await act(async () => {
      fortnightButton?.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0 }));
      fortnightButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await settleQueries();

    expect(apiFetch).toHaveBeenLastCalledWith(expect.stringContaining("period=14d"), expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(container.textContent).toContain("Algorithms");
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();

    await act(async () => {
      resolveFortnight?.([material("Databases")]);
      await fortnight;
    });
    await settleQueries();
    expect(container.textContent).toContain("Databases");
  });

  it("ignores a late load-more response after the period changes", async () => {
    let resolveOldPage: ((items: MaterialDetail[]) => void) | undefined;
    const oldPage = new Promise<MaterialDetail[]>((resolve) => {
      resolveOldPage = resolve;
    });
    vi.mocked(apiFetch)
      .mockResolvedValueOnce(Array.from({ length: 20 }, (_, index) => material(`Today ${index}`)))
      .mockReturnValueOnce(oldPage)
      .mockResolvedValueOnce([material("Fortnight result")]);

    await act(async () => {
      root.render(withQueryClient(<PopularPage />));
    });
    await settleQueries();

    const loadMoreButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("loadMore"));
    await act(async () => {
      loadMoreButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const fortnightButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("last14Days"));
    await act(async () => {
      fortnightButton?.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0 }));
      fortnightButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await settleQueries();
    expect(container.textContent).toContain("Fortnight result");

    await act(async () => {
      resolveOldPage?.([material("Late today result")]);
      await oldPage;
    });
    await settleQueries();
    expect(container.textContent).not.toContain("Late today result");
  });
});
