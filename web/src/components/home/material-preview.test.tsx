import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MaterialPreview } from "./material-preview";
import type { MaterialDetail } from "./types";
import * as browsePrefetch from "@/lib/browse-prefetch";
import * as previewSource from "@/lib/material-preview-source";
import * as apiClient from "@/lib/api-client";

vi.mock("@/lib/browse-prefetch", () => ({
  fetchBrowsePath: vi.fn(),
  prefetchBrowsePath: vi.fn(),
}));

vi.mock("@/lib/material-preview-source", () => ({
  getMaterialThumbnail: vi.fn(),
  refreshMaterialThumbnail: vi.fn(),
  subscribeMaterialThumbnail: vi.fn(() => () => {}),
}));

vi.mock("@/lib/api-client", () => ({
  getMaterialFileUrl: vi.fn().mockResolvedValue(null),
}));

function fileMaterial(
  fileName: string,
  mimeType: string,
  version: Partial<MaterialDetail["current_version_info"] & object> = {},
): MaterialDetail {
  return {
    id: `mat-${fileName}`,
    directory_id: "dir-1",
    directory_path: "cs",
    title: `Title of ${fileName}`,
    slug: fileName,
    description: null,
    type: "document",
    current_version: 1,
    parent_material_id: null,
    author_id: null,
    metadata: {},
    download_count: 0,
    total_views: 0,
    views_today: 0,
    like_count: 0,
    is_liked: false,
    is_favourited: false,
    attachment_count: 0,
    tags: [],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    current_version_info: {
      id: `ver-${fileName}`,
      material_id: `mat-${fileName}`,
      version_number: 1,
      file_key: `cas/${fileName}`,
      file_name: fileName,
      file_size: 10,
      file_mime_type: mimeType,
      diff_summary: null,
      author_id: null,
      pr_id: null,
      virus_scan_result: "clean",
      created_at: new Date().toISOString(),
      ...version,
    },
  };
}

async function renderAndSettle(root: Root, material: MaterialDetail) {
  await act(async () => {
    root.render(<MaterialPreview material={material} />);
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 150));
  });
}

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("MaterialPreview", () => {
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

  it("fetches thumbnail for standard non-link materials", async () => {
    vi.mocked(previewSource.getMaterialThumbnail).mockResolvedValue({
      url: "https://cdn.example.com/thumb-123.webp",
      thumbnailType: "webp",
    });

    const material: MaterialDetail = {
      id: "mat-123",
      directory_id: "dir-1",
      directory_path: "math",
      title: "Linear Algebra PDF",
      slug: "linear-algebra",
      description: "Notes",
      type: "document",
      current_version: 1,
      parent_material_id: null,
      author_id: "author-1",
      metadata: {},
      download_count: 5,
      total_views: 10,
      views_today: 1,
      like_count: 2,
      is_liked: false,
      is_favourited: false,
      attachment_count: 0,
      tags: [],
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      current_version_info: {
        id: "ver-1",
        material_id: "mat-123",
        version_number: 1,
        file_key: "files/la.pdf",
        file_name: "la.pdf",
        file_size: 1024,
        file_mime_type: "application/pdf",
        diff_summary: null,
        author_id: "author-1",
        pr_id: null,
        virus_scan_result: "clean",
        created_at: new Date().toISOString(),
      },
    };

    await act(async () => {
      root.render(<MaterialPreview material={material} />);
    });

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 150));
    });

    expect(previewSource.getMaterialThumbnail).toHaveBeenCalledWith("mat-123");
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img?.src).toContain("https://cdn.example.com/thumb-123.webp");
  });

  it("does NOT fetch thumbnail for external links", async () => {
    const material: MaterialDetail = {
      id: "mat-link-1",
      directory_id: "dir-1",
      directory_path: "resources",
      title: "External Resource",
      slug: "ext-res",
      description: "External tool",
      type: "link",
      current_version: 1,
      parent_material_id: null,
      author_id: "author-1",
      metadata: { url: "https://github.com/clubcode/wikint" },
      download_count: 0,
      total_views: 10,
      views_today: 0,
      like_count: 1,
      is_liked: false,
      is_favourited: false,
      attachment_count: 0,
      tags: [],
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      current_version_info: null,
    };

    await act(async () => {
      root.render(<MaterialPreview material={material} />);
    });

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 150));
    });

    expect(previewSource.getMaterialThumbnail).not.toHaveBeenCalled();
    expect(browsePrefetch.fetchBrowsePath).not.toHaveBeenCalled();
    const img = container.querySelector("img");
    expect(img).toBeNull();
  });

  it("resolves target material and fetches thumbnail for internal /browse links", async () => {
    const targetMaterial: MaterialDetail = {
      id: "target-mat-789",
      directory_id: "dir-2",
      directory_path: "math/analysis",
      title: "Target Analysis Course",
      slug: "analysis-course",
      description: "Course material",
      type: "document",
      current_version: 1,
      parent_material_id: null,
      author_id: "author-2",
      metadata: {},
      download_count: 20,
      total_views: 100,
      views_today: 5,
      like_count: 10,
      is_liked: false,
      is_favourited: false,
      attachment_count: 0,
      tags: [],
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      current_version_info: {
        id: "ver-target-1",
        material_id: "target-mat-789",
        version_number: 1,
        file_key: "files/analysis.pdf",
        file_name: "analysis.pdf",
        file_size: 2048,
        file_mime_type: "application/pdf",
        diff_summary: null,
        author_id: "author-2",
        pr_id: null,
        virus_scan_result: "clean",
        created_at: new Date().toISOString(),
      },
    };

    vi.mocked(browsePrefetch.fetchBrowsePath).mockResolvedValue({
      type: "material",
      material: targetMaterial,
    } as any);

    vi.mocked(previewSource.getMaterialThumbnail).mockResolvedValue({
      url: "https://cdn.example.com/target-thumb.webp",
      thumbnailType: "webp",
    });

    const linkMaterial: MaterialDetail = {
      id: "mat-internal-link-1",
      directory_id: "dir-1",
      directory_path: "shortcuts",
      title: "Shortcut to Analysis",
      slug: "shortcut-analysis",
      description: "Internal shortcut",
      type: "link",
      current_version: 1,
      parent_material_id: null,
      author_id: "author-1",
      metadata: { url: "/browse/math/analysis" },
      download_count: 0,
      total_views: 15,
      views_today: 2,
      like_count: 3,
      is_liked: false,
      is_favourited: false,
      attachment_count: 0,
      tags: [],
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      current_version_info: null,
    };

    await act(async () => {
      root.render(<MaterialPreview material={linkMaterial} />);
    });

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 150));
    });

    expect(browsePrefetch.fetchBrowsePath).toHaveBeenCalledWith("math/analysis");
    expect(previewSource.getMaterialThumbnail).toHaveBeenCalledWith("target-mat-789");
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img?.src).toContain("https://cdn.example.com/target-thumb.webp");
  });

  it("uses the thumbnail embedded by the listing without asking the API", async () => {
    const material = fileMaterial("notes.pdf", "application/pdf", {
      thumbnail_url: "https://cdn.example.com/embedded.webp",
      thumbnail_type: "webp",
    });

    await renderAndSettle(root, material);

    expect(previewSource.getMaterialThumbnail).not.toHaveBeenCalled();
    expect(container.querySelector("img")?.src).toContain("https://cdn.example.com/embedded.webp");
  });

  it("does not ask the API when the listing says there is no preview", async () => {
    const material = fileMaterial("slides.pptx", "application/vnd.ms-powerpoint", {
      thumbnail_url: null,
      thumbnail_type: "none",
    });

    await renderAndSettle(root, material);

    expect(previewSource.getMaterialThumbnail).not.toHaveBeenCalled();
    expect(container.querySelector("img")).toBeNull();
  });

  it("shows a generic language card for code files and fetches nothing", async () => {
    const material = fileMaterial("solver.py", "text/x-python");

    await renderAndSettle(root, material);

    expect(previewSource.getMaterialThumbnail).not.toHaveBeenCalled();
    expect(apiClient.getMaterialFileUrl).not.toHaveBeenCalled();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("Python");
    expect(container.querySelector(`[data-language-logo="python"]`)).not.toBeNull();
    expect(container.textContent).not.toContain("PY");
  });

  it("draws the text mark for languages without a logo", async () => {
    await renderAndSettle(root, fileMaterial("Program.cs", "text/plain"));

    expect(container.querySelector("[data-language-logo]")).toBeNull();
    expect(container.textContent).toContain("C#");
  });

  it("shows the extension for plain text files", async () => {
    await renderAndSettle(root, fileMaterial("notes.log", "text/plain"));

    expect(previewSource.getMaterialThumbnail).not.toHaveBeenCalled();
    expect(container.textContent).toContain("LOG");
  });

  it("only requests the video file when the pointer hovers a video with a thumbnail", async () => {
    vi.mocked(apiClient.getMaterialFileUrl).mockResolvedValue("https://cdn.example.com/clip.mp4");
    const material = fileMaterial("clip.mp4", "video/mp4", {
      thumbnail_url: "https://cdn.example.com/clip.webp",
      thumbnail_type: "webp",
    });

    await renderAndSettle(root, material);

    expect(container.querySelector("img")?.src).toContain("https://cdn.example.com/clip.webp");
    expect(container.querySelector("video")).toBeNull();
    expect(apiClient.getMaterialFileUrl).not.toHaveBeenCalled();

    const card = container.firstElementChild as HTMLElement;
    await act(async () => {
      card.dispatchEvent(
        Object.assign(new MouseEvent("pointerover", { bubbles: true }), { pointerType: "mouse" }),
      );
    });

    expect(apiClient.getMaterialFileUrl).toHaveBeenCalledWith("mat-clip.mp4");
    expect(container.querySelector("video")?.getAttribute("src")).toBe("https://cdn.example.com/clip.mp4");
  });
});

