import React, { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { apiFetchWithResponse } from "@/lib/api-client";
import { PRList } from "./pr-list";
import type { PullRequestOut } from "@/components/home/types";

vi.mock("next-intl", () => {
  const translate = (key: string) => key;
  return {
    useTranslations: () => translate,
    useLocale: () => "en",
  };
});
vi.mock("@/lib/api-client", () => ({ apiFetchWithResponse: vi.fn() }));
vi.mock("@/lib/sse-client", () => ({
  subscribeToSSE: () => ({ close: vi.fn() }),
}));
vi.mock("@/lib/stores", () => ({
  usePRStore: { getState: () => ({ setOpenPRCount: vi.fn() }) },
  useAuthStore: (selector: (state: { user: null; isAuthenticated: boolean }) => unknown) =>
    typeof selector === "function"
      ? selector({ user: null, isAuthenticated: false })
      : { user: null, isAuthenticated: false },
}));
vi.mock("./pr-card", () => ({
  PRCard: ({ pr }: { pr: PullRequestOut }) => <article>{pr.title}</article>,
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const pr = (title: string, id = title) => ({ id, title, type: "create_material", created_at: new Date().toISOString() }) as PullRequestOut;
const result = (data: PullRequestOut[], total = data.length) => ({
  data,
  response: new Response(null, { headers: { "X-Total-Count": String(total) } }),
});

describe("PRList", () => {
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

  it("keeps contributions visible while a different status loads", async () => {
    let resolveApproved: ((value: ReturnType<typeof result>) => void) | undefined;
    const approved = new Promise<ReturnType<typeof result>>((resolve) => {
      resolveApproved = resolve;
    });
    vi.mocked(apiFetchWithResponse).mockImplementation((url) => {
      const path = String(url);
      if (path.includes("limit=1")) return Promise.resolve(result([])) as never;
      if (path.includes("status=approved")) return approved as never;
      return Promise.resolve(result([pr("Open contribution")])) as never;
    });

    await act(async () => {
      root.render(<PRList />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const approvedButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("approved"));
    await act(async () => {
      approvedButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.textContent).toContain("Open contribution");
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();

    await act(async () => {
      resolveApproved?.(result([pr("Approved contribution")]));
      await approved;
    });
    expect(container.textContent).toContain("Approved contribution");
  });

  it("filters contributions by search query", async () => {
    vi.mocked(apiFetchWithResponse).mockImplementation((url) => {
      const path = String(url);
      if (path.includes("limit=1")) return Promise.resolve(result([])) as never;
      return Promise.resolve(
        result([
          pr("Mathematics Exam 2024", "pr-1"),
          pr("Physics Lecture Notes", "pr-2"),
        ]),
      ) as never;
    });

    await act(async () => {
      root.render(<PRList />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.textContent).toContain("Mathematics Exam 2024");
    expect(container.textContent).toContain("Physics Lecture Notes");

    const searchInput = container.querySelector("input[type='text']") as HTMLInputElement;
    expect(searchInput).not.toBeNull();

    await act(async () => {
      const nativeSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      nativeSetter?.call(searchInput, "Physics");
      searchInput.dispatchEvent(new Event("input", { bubbles: true }));
      searchInput.dispatchEvent(new Event("change", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.textContent).toContain("Physics Lecture Notes");
    expect(container.textContent).not.toContain("Mathematics Exam 2024");
  });
});
