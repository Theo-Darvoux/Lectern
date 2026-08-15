import { describe, it, expect } from "vitest";
import { ACCEPTED_FILE_TYPES, formatFileSize, getFileExtension, getViewerType, guessFileMime, sniffFileType, MIME_TO_EXT, MIME_QCM, isThumbnailEligible } from "./file-utils";

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
      expect(getViewerType(MIME_QCM, "quiz.qcm")).toBe("qcm");
    });

    it("returns qcm for .qcm extension with octet-stream", () => {
      expect(getViewerType("application/octet-stream", "quiz.qcm")).toBe("qcm");
    });

    it("returns qcm mime type before other checks", () => {
      // Even with a generic filename, the mime type should win
      expect(getViewerType(MIME_QCM, "data")).toBe("qcm");
    });

    it("uses the notebook viewer for .ipynb files reported as JSON or plain text", () => {
      expect(getViewerType("application/json", "analysis.ipynb")).toBe("notebook");
      expect(getViewerType("text/plain", "analysis.ipynb")).toBe("notebook");
    });
  });

  describe("guessFileMime", () => {
    it("accepts Jupyter notebooks and infers their JSON MIME type", () => {
      expect(ACCEPTED_FILE_TYPES.split(",")).toContain(".ipynb");

      const file = { name: "analysis.ipynb", type: "" } as File;
      expect(guessFileMime(file)).toBe("application/json");
    });

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

  describe("MIME_TO_EXT", () => {
    it("maps common mime types to their canonical extension", () => {
      expect(MIME_TO_EXT["application/pdf"]).toBe("pdf");
      expect(MIME_TO_EXT["image/jpeg"]).toBe("jpg");
      expect(MIME_TO_EXT["text/plain"]).toBe("txt");
      expect(MIME_TO_EXT["text/markdown"]).toBe("md");
    });
  });

  describe("sniffFileType", () => {
    const fileOf = (bytes: number[], name = "noext") =>
      new File([new Uint8Array(bytes)], name, { type: "application/octet-stream" });

    it("detects a PDF from magic bytes", async () => {
      const pdf = fileOf([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x37]); // %PDF-1.7
      expect(await sniffFileType(pdf)).toEqual({ mime: "application/pdf", ext: "pdf" });
    });

    it("detects PNG and JPEG", async () => {
      expect(await sniffFileType(fileOf([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])))
        .toEqual({ mime: "image/png", ext: "png" });
      expect(await sniffFileType(fileOf([0xff, 0xd8, 0xff, 0xe0])))
        .toEqual({ mime: "image/jpeg", ext: "jpg" });
    });

    it("detects mp4 via the ftyp box at offset 4", async () => {
      const mp4 = fileOf([0x00, 0x00, 0x00, 0x18, 0x66, 0x74, 0x79, 0x70, 0x6d, 0x70, 0x34, 0x32]);
      expect(await sniffFileType(mp4)).toEqual({ mime: "video/mp4", ext: "mp4" });
    });

    it("disambiguates RIFF containers", async () => {
      const riff = (tag: string) =>
        fileOf([0x52, 0x49, 0x46, 0x46, 0x00, 0x00, 0x00, 0x00, ...[...tag].map((c) => c.charCodeAt(0))]);
      expect(await sniffFileType(riff("WEBP"))).toEqual({ mime: "image/webp", ext: "webp" });
      expect(await sniffFileType(riff("WAVE"))).toEqual({ mime: "audio/wav", ext: "wav" });
      expect(await sniffFileType(riff("AVI "))).toBeNull();
    });

    it("returns null for unknown or ZIP-based signatures", async () => {
      expect(await sniffFileType(fileOf([0x50, 0x4b, 0x03, 0x04]))).toBeNull(); // ZIP (docx/epub…)
      expect(await sniffFileType(fileOf([0x68, 0x65, 0x6c, 0x6c, 0x6f]))).toBeNull();
      expect(await sniffFileType(fileOf([]))).toBeNull();
    });
  });

  describe("isThumbnailEligible", () => {
    it("returns true for images", () => {
      expect(isThumbnailEligible("image/png", "picture.png")).toBe(true);
      expect(isThumbnailEligible("image/jpeg", "photo.jpg")).toBe(true);
      expect(isThumbnailEligible("image/svg+xml", "icon.svg")).toBe(true);
      expect(isThumbnailEligible("", "image.webp")).toBe(true);
    });

    it("returns true for videos", () => {
      expect(isThumbnailEligible("video/mp4", "movie.mp4")).toBe(true);
      expect(isThumbnailEligible("video/webm", "clip.webm")).toBe(true);
      expect(isThumbnailEligible("", "recording.mkv")).toBe(true);
    });

    it("returns true for PDFs", () => {
      expect(isThumbnailEligible("application/pdf", "document.pdf")).toBe(true);
      expect(isThumbnailEligible("", "notes.pdf")).toBe(true);
    });

    it("returns true for Office documents, notebooks, markdown and code", () => {
      expect(isThumbnailEligible("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "paper.docx")).toBe(true);
      expect(isThumbnailEligible("application/x-ipynb+json", "lab.ipynb")).toBe(true);
      expect(isThumbnailEligible("text/markdown", "readme.md")).toBe(true);
      expect(isThumbnailEligible("text/plain", "script.py")).toBe(true);
      expect(isThumbnailEligible("application/json", "config.json")).toBe(true);
      expect(isThumbnailEligible("", "analysis.ipynb")).toBe(true);
      expect(isThumbnailEligible("", "sheet.xlsx")).toBe(true);
    });

    it("returns false for unsupported binaries and audio", () => {
      expect(isThumbnailEligible("audio/mpeg", "song.mp3")).toBe(false);
      expect(isThumbnailEligible("audio/wav", "recording.wav")).toBe(false);
      expect(isThumbnailEligible("application/octet-stream", "binary.bin")).toBe(false);
      expect(isThumbnailEligible("application/zip", "archive.zip")).toBe(false);
      expect(isThumbnailEligible("", "archive.tar.gz")).toBe(false);
    });
  });
});
