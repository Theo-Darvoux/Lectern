import React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ImageViewer } from "./image-viewer";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const translate = (key: string) => key;

vi.mock("next-intl", () => ({
  useTranslations: () => translate,
}));

vi.mock("@/hooks/use-material-file", () => ({
  useMaterialFile: ({ materialId, fileKey }: { materialId: string; fileKey: string }) => ({
    blobUrl: `blob:http://localhost/${materialId}/${fileKey}`,
    loading: false,
    error: null,
    reload: vi.fn(),
  }),
}));

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });

  global.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

describe("ImageViewer", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    vi.restoreAllMocks();
  });

  it("renders rotate buttons and zoom controls", async () => {
    await act(async () => {
      root.render(
        <ImageViewer
          materialId="mat-1"
          fileKey="key-1"
          fileName="photo.png"
        />
      );
    });

    const rotateCcwBtn = host.querySelector('button[aria-label="rotateCcw"]');
    const rotateCwBtn = host.querySelector('button[aria-label="rotateCw"]');
    const zoomOutBtn = host.querySelector('button[aria-label="zoomControls.out"]');
    const zoomInBtn = host.querySelector('button[aria-label="zoomControls.in"]');
    const img = host.querySelector("img");

    expect(rotateCcwBtn).not.toBeNull();
    expect(rotateCwBtn).not.toBeNull();
    expect(zoomOutBtn).not.toBeNull();
    expect(zoomInBtn).not.toBeNull();
    expect(img).not.toBeNull();
    expect(img?.getAttribute("src")).toBe("blob:http://localhost/mat-1/key-1");
  });

  it("rotates image +90° and -90° when clicking rotate buttons", async () => {
    await act(async () => {
      root.render(
        <ImageViewer
          materialId="mat-1"
          fileKey="key-1"
          fileName="photo.png"
        />
      );
    });

    const rotateCwBtn = host.querySelector('button[aria-label="rotateCw"]') as HTMLButtonElement;
    const rotateCcwBtn = host.querySelector('button[aria-label="rotateCcw"]') as HTMLButtonElement;
    const img = host.querySelector("img") as HTMLImageElement;

    // Initially unrotated
    expect(img.style.transform).toBe("");

    // Click rotate +90°
    await act(async () => {
      rotateCwBtn.click();
    });
    expect(img.style.transform).toBe("rotate(90deg)");

    // Click rotate +90° again -> 180°
    await act(async () => {
      rotateCwBtn.click();
    });
    expect(img.style.transform).toBe("rotate(180deg)");

    // Click rotate -90° -> back to 90°
    await act(async () => {
      rotateCcwBtn.click();
    });
    expect(img.style.transform).toBe("rotate(90deg)");

    // Click rotate -90° -> back to 0°
    await act(async () => {
      rotateCcwBtn.click();
    });
    expect(img.style.transform).toBe("");

    // Click rotate -90° -> -90°
    await act(async () => {
      rotateCcwBtn.click();
    });
    expect(img.style.transform).toBe("rotate(-90deg)");
  });
});
