import React, { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { MaterialGridSection } from "./material-grid-section";
import type { MaterialDetail } from "./types";

vi.mock("./material-card", () => ({
  MaterialCard: ({ material }: { material: MaterialDetail }) => <article>{material.title}</article>,
}));
vi.mock("./section-header", () => ({
  SectionHeader: ({ title }: { title: string }) => <h2>{title}</h2>,
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("MaterialGridSection", () => {
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
  });

  it("keeps loaded cards visible during a background refresh", () => {
    const material = { id: "material-1", title: "Operating Systems" } as MaterialDetail;
    act(() => {
      root.render(
        <MaterialGridSection
          title="Recently added"
          materials={[material]}
          isLoading
          emptyText="Nothing here"
        />,
      );
    });

    expect(container.textContent).toContain(material.title);
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
  });

  it("keeps a loaded empty state during a background refresh", () => {
    act(() => {
      root.render(
        <MaterialGridSection
          title="Recently added"
          materials={[]}
          isLoading
          hasLoaded
          emptyText="Nothing here"
        />,
      );
    });

    expect(container.textContent).toContain("Nothing here");
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
  });
});
