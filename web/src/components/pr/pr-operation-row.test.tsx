import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PRLocationBreadcrumb, PRMoveTransition } from "./pr-location-breadcrumb";
import { PROperationThumbnail } from "./pr-operation-thumbnail";
import { PRDetailPageContent } from "./pr-detail-page-content";
import { apiFetch } from "@/lib/api-client";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

global.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

vi.mock("next/navigation", () => ({
  usePathname: () => "/pull-requests/test-pr-id",
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}));

vi.mock("next-intl", () => ({
  useTranslations: (ns: string) => {
    const t = (key: string, values?: Record<string, unknown>) => {
      if (values) {
        const parts = Object.entries(values).map(([k, v]) => `${k}=${v}`).join(", ");
        return `${ns}.${key} [${parts}]`;
      }
      return `${ns}.${key}`;
    };
    t.rich = (key: string) => `${ns}.${key}`;
    t.raw = (key: string) => `${ns}.${key}`;
    return t;
  },
  useLocale: () => "en",
}));

vi.mock("@/lib/api-client", () => ({
  apiFetch: vi.fn(),
}));

vi.mock("@/lib/material-preview-source", () => ({
  getMaterialThumbnail: vi.fn().mockResolvedValue({ url: "https://thumb.example/img.webp" }),
}));

vi.mock("@/components/pr/pr-comments", () => ({
  PRComments: () => <div data-testid="pr-comments" />,
}));

describe("PRLocationBreadcrumb & PRMoveTransition", () => {
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

  it("renders path segments with clickable links and handles temp folders", async () => {
    await act(async () => {
      root.render(
        <PRLocationBreadcrumb
          pathSegments={[
            { name: "Licence 1", slug: "l1" },
            { name: "Mathématiques", slug: "maths" },
            { name: "New Exam Folder", isTemp: true },
          ]}
          rootLabel="Root"
        />,
      );
    });

    expect(host.textContent).toContain("Root");
    expect(host.textContent).toContain("Licence 1");
    expect(host.textContent).toContain("Mathématiques");
    expect(host.textContent).toContain("New Exam Folder");
    expect(host.textContent).toContain("New");
  });

  it("renders PRMoveTransition with origin and destination", async () => {
    await act(async () => {
      root.render(
        <PRMoveTransition
          originPath="Root › L1 › Maths"
          originUrl="/browse/l1/maths"
          destPath="Root › L2 › Advanced Maths"
          destUrl="/browse/l2/adv-maths"
          originLabel="Origin"
          destLabel="Destination"
        />,
      );
    });

    expect(host.textContent).toContain("Origin");
    expect(host.textContent).toContain("Root › L1 › Maths");
    expect(host.textContent).toContain("Destination");
    expect(host.textContent).toContain("Root › L2 › Advanced Maths");
  });
});

describe("PROperationThumbnail", () => {
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

  it("renders folder thumbnail for directories", async () => {
    await act(async () => {
      root.render(
        <PROperationThumbnail
          isDirectory={true}
          directoryIcon="calculator"
          directoryColor="blue"
          size="md"
        />,
      );
    });

    expect(host.querySelector("svg")).not.toBeNull();
  });

  it("renders link thumbnail for links", async () => {
    await act(async () => {
      root.render(
        <PROperationThumbnail
          materialType="link"
          targetUrl="https://example.com"
          size="md"
        />,
      );
    });

    expect(host.querySelector("svg")).not.toBeNull();
  });

  it("renders file styled badge with extension label", async () => {
    await act(async () => {
      root.render(
        <PROperationThumbnail
          fileName="course_notes.pdf"
          mimeType="application/pdf"
          materialType="polycopie"
          size="lg"
        />,
      );
    });

    expect(host.textContent).toContain("PDF");
  });
});

describe("PRDetailPageContent with rich operations", () => {
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
    vi.clearAllMocks();
  });

  it("renders PR details with create, rename, and move operations", async () => {
    vi.mocked(apiFetch).mockImplementation((url: string) => {
      if (url.includes("/pull-requests/test-pr-id")) {
        return Promise.resolve({
          id: "test-pr-id",
          type: "batch",
          status: "open",
          title: "Batch updates to wiki",
          description: "PR with creation, rename and move",
          author: { id: "user-1", display_name: "Alice" },
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          payload: [
            {
              op: "create_material",
              title: "Linear Algebra Notes",
              type: "polycopie",
              file_name: "linear_algebra.pdf",
              file_size: 2048576,
              file_mime_type: "application/pdf",
              directory_id: "dir-1",
              attachments: [
                {
                  title: "Cheatsheet",
                  type: "cheatsheet",
                  file_name: "cheatsheet.png",
                  file_size: 512000,
                },
              ],
            },
            {
              op: "edit_material",
              material_id: "mat-2",
              title: "Calculus II - Complete",
              file_name: "calculus_v2.pdf",
              file_size: 4096000,
              file_key: "uploads/user-1/calc.pdf",
            },
            {
              op: "move_item",
              target_type: "material",
              target_id: "mat-3",
              target_title: "Physics Lab 1",
              new_parent_id: "dir-2",
            },
          ],
        }) as never;
      }

      if (url === "/materials/mat-2") {
        return Promise.resolve({
          title: "Calculus II",
          type: "polycopie",
          directory_id: "dir-1",
          current_version_info: {
            file_name: "calculus_v1.pdf",
            file_size: 3000000,
            file_mime_type: "application/pdf",
          },
        }) as never;
      }

      if (url === "/directories/dir-1/path") {
        return Promise.resolve([
          { name: "Licence 1", slug: "l1" },
          { name: "Mathématiques", slug: "maths" },
        ]) as never;
      }

      if (url === "/directories/dir-2/path") {
        return Promise.resolve([
          { name: "Licence 2", slug: "l2" },
          { name: "Physique", slug: "physics" },
        ]) as never;
      }

      return Promise.resolve([]) as never;
    });

    await act(async () => {
      root.render(<PRDetailPageContent />);
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    // Verify PR title rendered
    expect(host.textContent).toContain("Batch updates to wiki");

    // Verify operations rendered
    expect(host.textContent).toContain("Linear Algebra Notes");
    expect(host.textContent).toContain("PDF");

    // Verify rename diff rendering
    expect(host.textContent).toContain("Calculus II");
    expect(host.textContent).toContain("Calculus II - Complete");

    // Verify move item rendering
    expect(host.textContent).toContain("Physics Lab 1");
  });
});
