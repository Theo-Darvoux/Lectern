import { describe, it, expect } from "vitest";
import { formatFileSize, getFileExtension, getViewerType, guessFileMime } from "./file-utils";

describe("file-utils", () => {
  describe("formatFileSize", () => {
    it("formats bytes correctly", () => {
      expect(formatFileSize(0)).toBe("0 B");
      expect(formatFileSize(1024)).toBe("1.0 KB");
      expect(formatFileSize(1024 * 1024)).toBe("1.0 MB");
    });
  });

  describe("getFileExtension", () => {
    it("extracts extension correctly", () => {
      expect(getFileExtension("test.pdf")).toBe("pdf");
      expect(getFileExtension("test.PNG")).toBe("png");
      expect(getFileExtension("no-extension")).toBe("");
    });
  });

  describe("getViewerType", () => {
    it("returns correct viewer type for common mimetypes", () => {
      expect(getViewerType("application/pdf", "test.pdf")).toBe("pdf");
      expect(getViewerType("image/png", "test.png")).toBe("image");
      expect(getViewerType("text/plain", "test.txt")).toBe("code");
      expect(getViewerType("video/mp4", "test.mp4")).toBe("video");
    });

    it("falls back to extension if mimetype is ambiguous", () => {
      expect(getViewerType("application/octet-stream", "test.md")).toBe("markdown");
    });

    it("returns qcm for QCM mime type", () => {
      expect(getViewerType("application/vnd.wikint.qcm+json", "quiz.qcm")).toBe("qcm");
    });

    it("returns qcm for .qcm extension with octet-stream", () => {
      expect(getViewerType("application/octet-stream", "quiz.qcm")).toBe("qcm");
    });

    it("returns qcm mime type before other checks", () => {
      // Even with a generic filename, the mime type should win
      expect(getViewerType("application/vnd.wikint.qcm+json", "data")).toBe("qcm");
    });
  });

  describe("guessFileMime", () => {
    it("returns raw mime if valid", () => {
      const file = { name: "test.pdf", type: "application/pdf" } as File;
      expect(guessFileMime(file)).toBe("application/pdf");
    });

    it("guesses mime from extension when raw mime is empty or octet-stream", () => {
      const file1 = { name: "test.pdf", type: "" } as File;
      expect(guessFileMime(file1)).toBe("application/pdf");

      const file2 = { name: "test.tex", type: "application/octet-stream" } as File;
      expect(guessFileMime(file2)).toBe("application/x-tex");
    });

    it("falls back to octet-stream for unknown extensions", () => {
      const file = { name: "test.unknown", type: "" } as File;
      expect(guessFileMime(file)).toBe("application/octet-stream");
    });
  });
});

