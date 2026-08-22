import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SearchResultRow, SearchResultThumbnail } from "./search-modal";
import type { SearchResult } from "./use-search";
import * as previewSource from "@/lib/material-preview-source";

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) => {
    const translations: Record<string, string> = {
      untitled: "Untitled",
      libraryRoot: "Library root",
      folder: "Folder",
      material: "Material",
      inLocation: `In ${params?.location ?? ""}`,
      "matchedFields.description": "Description",
      "matchedFields.file_name": "File name",
    };
    return translations[key] ?? key;
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/browse",
}));

vi.mock("@/lib/material-preview-source", () => ({
  getMaterialThumbnail: vi.fn(),
  subscribeMaterialThumbnail: vi.fn(() => () => {}),
}));

vi.mock("@/lib/api-client", () => ({
  getMaterialFileUrl: vi.fn().mockResolvedValue(null),
}));

vi.mock("@/components/home/material-preview", () => ({
  MaterialPreview: ({ material }: { material: { id: string; title: string } }) => (
    <div data-testid="material-preview" data-material-id={material.id} data-material-title={material.title}>
      Preview for {material.title}
    </div>
  ),
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("SearchResultRow & SearchResultThumbnail", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    if (root) {
      act(() => root.unmount());
    }
    container?.remove();
  });

  it("renders a material result with thumbnail preview instead of default icon", async () => {
    const materialResult: SearchResult = {
      id: "mat-react-svg",
      search_type: "material",
      title: "React",
      file_name: "react.svg",
      file_mime_type: "image/svg+xml",
      type: "document",
      ancestor_path: "Dossier de test auto",
      browse_path: "/browse/dossier/react-svg",
    };

    await act(async () => {
      root.render(<SearchResultRow result={materialResult} />);
    });

    const preview = container.querySelector('[data-testid="material-preview"]');
    expect(preview).not.toBeNull();
    expect(preview?.getAttribute("data-material-id")).toBe("mat-react-svg");
    expect(preview?.getAttribute("data-material-title")).toBe("React");

    expect(container.textContent).toContain("React");
    expect(container.textContent).toContain("SVG");
    expect(container.textContent).toContain("In Dossier de test auto");
  });

  it("renders a directory result with styled directory icon", async () => {
    const directoryResult: SearchResult = {
      id: "dir-math",
      search_type: "directory",
      name: "Mathematics",
      ancestor_path: "Courses",
      browse_path: "/browse/courses/mathematics",
      metadata: {
        thumbnail_icon: "calculator",
        thumbnail_color: "purple",
      },
    };

    await act(async () => {
      root.render(<SearchResultRow result={directoryResult} />);
    });

    expect(container.querySelector('[data-testid="material-preview"]')).toBeNull();
    expect(container.textContent).toContain("Mathematics");
    expect(container.textContent).toContain("Folder");
    expect(container.textContent).toContain("In Courses");

    // Has SVG icon inside the styled container
    const svgIcon = container.querySelector("svg");
    expect(svgIcon).not.toBeNull();
  });

  it("renders match context when a non-title field matched the query", async () => {
    const resultWithMatch: SearchResult = {
      id: "mat-notes",
      search_type: "material",
      title: "Course Notes",
      file_name: "notes.pdf",
      browse_path: "/browse/notes",
      match_context: "Detailed React and TypeScript component guide",
      matched_field: "description",
    };

    await act(async () => {
      root.render(<SearchResultRow result={resultWithMatch} />);
    });

    expect(container.textContent).toContain("Course Notes");
    expect(container.textContent).toContain("Description:");
    expect(container.textContent).toContain("Detailed React and TypeScript component guide");
  });
});
