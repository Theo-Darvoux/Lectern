import React, { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { PRCommitGraph } from "./pr-commit-graph";
import type { PullRequestOut } from "@/components/home/types";

vi.mock("next-intl", () => {
  const translate = (key: string) => key;
  return { useTranslations: () => translate, useLocale: () => "en" };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const pr = (
  id: string,
  title: string,
  status: "open" | "approved" | "rejected" | "cancelled" = "open",
  type = "create_material",
): PullRequestOut =>
  ({
    id,
    title,
    status,
    type,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    author: { id: "user-1", display_name: "Theo" },
    summary_types: [type],
  } as unknown as PullRequestOut);

describe("PRCommitGraph", () => {
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

  it("renders empty state when PR list is empty", async () => {
    await act(async () => {
      root.render(<PRCommitGraph prs={[]} />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.textContent).toContain("noContributionsYet");
  });

  it("renders commit nodes, SVG tracks, and branch tags", async () => {
    const list = [
      pr("pr-1", "Feature Auth", "open"),
      pr("pr-2", "Merge Docs", "approved"),
      pr("pr-3", "Fix Bug", "rejected"),
    ];

    await act(async () => {
      root.render(<PRCommitGraph prs={list} />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.textContent).toContain("Feature Auth");
    expect(container.textContent).toContain("Merge Docs");
    expect(container.textContent).toContain("Fix Bug");

    // Branch tag pills
    expect(container.textContent).toContain("branchPending");
    expect(container.textContent).toContain("origin/main");
    expect(container.textContent).toContain("branchMain");

    // SVG elements
    const svgElements = container.querySelectorAll("svg");
    expect(svgElements.length).toBeGreaterThanOrEqual(1);

    const circles = container.querySelectorAll("circle");
    expect(circles.length).toBeGreaterThanOrEqual(3);
  });

  it("renders revert PR tag and copy button interaction", async () => {
    const revertPr = pr("pr-revert-1", "Revert bad upload", "approved", "revert");
    revertPr.reverts_pr_id = "pr-old";

    await act(async () => {
      root.render(<PRCommitGraph prs={[revertPr]} />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.textContent).toContain("revert");
    expect(container.textContent).toContain("Revert bad upload");

    // Copy hash button
    const copyButton = container.querySelector("button[title='copyHash']");
    expect(copyButton).not.toBeNull();

    // Mock clipboard
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    });

    await act(async () => {
      copyButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("pr-revert-1");
  });

  it("renders multi-operation merge commits and connects tracks properly", async () => {
    const list = [
      pr("pr-1", "Merge batch PR", "approved", "batch"),
      pr("pr-2", "Add Chapter 1", "approved", "create_material"),
      pr("pr-3", "Add Chapter 2", "approved", "create_material"),
      pr("pr-4", "Open branch feature", "open", "create_material"),
    ];

    await act(async () => {
      root.render(<PRCommitGraph prs={list} />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.textContent).toContain("Merge batch PR");
    expect(container.textContent).toContain("Add Chapter 1");
    expect(container.textContent).toContain("Add Chapter 2");

    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    const paths = svg?.querySelectorAll("path");
    expect(paths && paths.length).toBeGreaterThanOrEqual(2);
  });
});

