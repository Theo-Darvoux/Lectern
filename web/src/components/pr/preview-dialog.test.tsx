import React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PreviewDialog } from "./preview-dialog";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("next/dynamic", () => ({
  default: () => () => <div data-testid="dynamic-viewer" />,
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
}));

vi.mock("@/components/viewers/notebook-renderer", () => ({
  NotebookRenderer: () => <div data-testid="notebook" />,
}));

describe("PreviewDialog", () => {
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
  });

  it("shows the download fallback when a known file type has no direct PR viewer", async () => {
    await act(async () => {
      root.render(
        <PreviewDialog
          url="https://files.example/notes.docx"
          mimeType="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          fileName="notes.docx"
          onClose={() => undefined}
        />,
      );
    });

    expect(host.textContent).toContain("previewUnavailable");
    expect(host.querySelector("a[download='notes.docx']")).not.toBeNull();
  });
});
