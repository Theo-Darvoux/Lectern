import { FileText, Image as ImageIcon, Video, Music, Code2, File } from "lucide-react";
import type { ElementType } from "react";
import { getFileExtension } from "@/lib/file-utils";

export interface FileTypeStyle {
    /** Tailwind gradient classes, e.g. "from-red-400 to-rose-600" */
    gradient: string;
    /** Tailwind text color class for the icon rendered on the gradient, e.g. "text-white" */
    iconColorClass: string;
    /** Lucide icon component */
    Icon: ElementType;
}

const CODE_EXTENSIONS = new Set([
    "js", "ts", "jsx", "tsx", "py", "java", "c", "cpp", "h", "hpp",
    "rs", "go", "rb", "php", "cs", "swift", "kt", "scala", "sh", "bash",
    "zsh", "lua", "r", "m", "ml", "hs", "ex", "exs", "clj",
    "html", "css", "scss", "sql",
]);

const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg"]);
const VIDEO_EXTENSIONS = new Set(["mp4", "webm", "avi", "mkv", "mov"]);
const AUDIO_EXTENSIONS = new Set(["mp3", "wav", "ogg", "flac", "aac", "m4a"]);
const DOC_EXTENSIONS = new Set(["doc", "docx", "odt"]);
const SHEET_EXTENSIONS = new Set(["xls", "xlsx", "ods", "csv"]);
const SLIDE_EXTENSIONS = new Set(["ppt", "pptx"]);
const TEXT_EXTENSIONS = new Set(["md", "markdown", "txt"]);
const EBOOK_EXTENSIONS = new Set(["epub", "djvu", "djv"]);

/**
 * Derives a consistent visual style (gradient + icon) for a file based on its
 * extension or MIME type. Falls back gracefully to a neutral gray style.
 */
export function getFileTypeStyle(
    fileName: string | null,
    mimeType: string | null,
): FileTypeStyle {
    const ext = fileName ? getFileExtension(fileName) : "";

    // PDF
    if (ext === "pdf" || mimeType === "application/pdf") {
        return { gradient: "from-red-400 to-rose-600", iconColorClass: "text-white", Icon: FileText };
    }

    // Images
    if (IMAGE_EXTENSIONS.has(ext) || (mimeType?.startsWith("image/") ?? false)) {
        return { gradient: "from-purple-400 to-violet-600", iconColorClass: "text-white", Icon: ImageIcon };
    }

    // Video
    if (VIDEO_EXTENSIONS.has(ext) || (mimeType?.startsWith("video/") ?? false)) {
        return { gradient: "from-pink-400 to-rose-500", iconColorClass: "text-white", Icon: Video };
    }

    // Audio
    if (AUDIO_EXTENSIONS.has(ext) || (mimeType?.startsWith("audio/") ?? false)) {
        return { gradient: "from-amber-400 to-orange-500", iconColorClass: "text-white", Icon: Music };
    }

    // Code
    if (CODE_EXTENSIONS.has(ext)) {
        return { gradient: "from-sky-400 to-blue-600", iconColorClass: "text-white", Icon: Code2 };
    }

    // Word documents
    if (
        DOC_EXTENSIONS.has(ext) ||
        (mimeType?.includes("msword") ?? false) ||
        (mimeType?.includes("wordprocessingml") ?? false)
    ) {
        return { gradient: "from-blue-400 to-indigo-600", iconColorClass: "text-white", Icon: FileText };
    }

    // Spreadsheets
    if (
        SHEET_EXTENSIONS.has(ext) ||
        (mimeType?.includes("spreadsheetml") ?? false) ||
        (mimeType?.includes("excel") ?? false)
    ) {
        return { gradient: "from-emerald-400 to-green-600", iconColorClass: "text-white", Icon: FileText };
    }

    // Presentations
    if (
        SLIDE_EXTENSIONS.has(ext) ||
        (mimeType?.includes("presentationml") ?? false) ||
        (mimeType?.includes("powerpoint") ?? false)
    ) {
        return { gradient: "from-orange-400 to-amber-600", iconColorClass: "text-white", Icon: FileText };
    }

    // Markdown / plain text
    if (TEXT_EXTENSIONS.has(ext)) {
        return { gradient: "from-slate-700 to-slate-900", iconColorClass: "text-white", Icon: FileText };
    }

    // E-books
    if (EBOOK_EXTENSIONS.has(ext)) {
        return { gradient: "from-teal-400 to-cyan-600", iconColorClass: "text-white", Icon: FileText };
    }

    // Default fallback
    return { gradient: "from-slate-700 to-slate-900", iconColorClass: "text-white", Icon: File };
}

/**
 * Constructs the browse URL for a material.
 * - With directory_path: `/browse/{directory_path}/{slug}`
 * - Without: `/browse/{slug}`
 */
export function getMaterialBrowsePath(material: {
    directory_path: string | null;
    slug: string;
}): string {
    if (material.directory_path) {
        return `/browse/${material.directory_path}/${material.slug}`;
    }
    return `/browse/${material.slug}`;
}
