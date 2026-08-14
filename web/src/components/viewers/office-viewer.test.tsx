import React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OfficeViewer } from "./office-viewer";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const apiFetch = vi.fn();
const translate = (key: string, values?: Record<string, string>) =>
  values?.message ? `${key}: ${values.message}` : key;

vi.mock("@/lib/api-client", () => ({
  apiFetchRetry: (...args: unknown[]) => apiFetch(...args),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => translate,
}));

vi.mock("@/lib/stores", () => ({
  useConfigStore: (selector: (state: { config: { eurooffice_public_url: string } }) => unknown) =>
    selector({ config: { eurooffice_public_url: "/eurooffice/" } }),
}));

vi.mock("@/lib/viewer-print-registry", () => ({
  registerViewerPrint: vi.fn(),
  unregisterViewerPrint: vi.fn(),
}));

vi.mock("./viewer-shell", () => ({
  ViewerShell: ({ children, error }: { children: React.ReactNode; error?: string | null }) => (
    <div>{error ? <div role="alert">{error}</div> : children}</div>
  ),
}));

describe("OfficeViewer", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    apiFetch.mockResolvedValue({ document: { title: "notes.docx" } });
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    delete (window as typeof window & { DocsAPI?: unknown }).DocsAPI;
    document.querySelectorAll("script[data-eurooffice-api]").forEach((script) => script.remove());
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("recovers from a transient API script failure without a page refresh", async () => {
    const editor = { destroyEditor: vi.fn() };
    const DocEditor = vi.fn(function (
      _id: string,
      options: { events: { onAppReady: () => void } },
    ) {
      options.events.onAppReady();
      return editor;
    });

    await act(async () => {
      root.render(
        <OfficeViewer materialId="material-1" fileKey="file-1" fileName="notes.docx" />,
      );
    });

    const firstScript = document.querySelector<HTMLScriptElement>("script[src*='documents/api.js']");
    expect(firstScript).not.toBeNull();

    await act(async () => {
      firstScript?.dispatchEvent(new Event("error"));
      await vi.advanceTimersByTimeAsync(400);
    });

    const scripts = document.querySelectorAll<HTMLScriptElement>("script[src*='documents/api.js']");
    expect(scripts).toHaveLength(1);
    expect(scripts[0]).not.toBe(firstScript);

    (window as typeof window & { DocsAPI: unknown }).DocsAPI = {
      DocEditor,
    };

    await act(async () => {
      scripts[0].dispatchEvent(new Event("load"));
    });

    expect(host.querySelector("[role='alert']")).toBeNull();
    expect(host.textContent).not.toContain("initializing");
    expect(DocEditor).toHaveBeenCalledTimes(1);
  });

  it("does not report an expected request cancellation when the viewer unmounts", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    apiFetch.mockImplementation(
      (_path: string, options: { signal?: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          options.signal?.addEventListener(
            "abort",
            () => reject(options.signal?.reason ?? new DOMException("The operation was aborted.", "AbortError")),
            { once: true },
          );
        }),
    );

    await act(async () => {
      root.render(
        <OfficeViewer materialId="material-2" fileKey="file-2" fileName="budget.xlsx" />,
      );
    });

    await act(async () => {
      root.render(<div />);
      await Promise.resolve();
    });

    expect(consoleError).not.toHaveBeenCalled();
  });
});
