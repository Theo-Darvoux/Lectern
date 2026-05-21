/** Maximum upload size in bytes, configurable via NEXT_PUBLIC_MAX_FILE_SIZE_MB (default 100 MiB). */
export const MAX_FILE_SIZE_MB = parseInt(process.env.NEXT_PUBLIC_MAX_FILE_SIZE_MB || "100", 10);

/** Comma-separated string of accepted file extensions for <input accept="...">. */
export const ACCEPTED_FILE_TYPES = [
    // Documents
    ".pdf", ".epub", ".djvu", ".djv",
    // Images
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    // Audio
    ".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a",
    // Video
    ".mp4", ".webm",
    // Office (modern + legacy + ODF)
    ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".odt", ".ods",
    // Text / markup
    ".md", ".markdown", ".rst", ".adoc",
    ".txt", ".csv", ".log", ".env",
    ".json", ".json5", ".jsonc", ".xml", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf",
    // TeX / LaTeX
    ".tex", ".latex", ".sty", ".cls", ".bib", ".dtx", ".ins",
    // C / C++
    ".c", ".h", ".cpp", ".cxx", ".cc", ".hpp", ".hxx",
    // Python
    ".py", ".pyw", ".pyi",
    // Java / JVM
    ".java", ".kt", ".kts", ".scala", ".groovy", ".gradle",
    // Web
    ".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx", ".vue", ".svelte",
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    // Systems / Low-level
    ".rs", ".go", ".zig", ".v", ".nim", ".odin", ".d",
    // Scripting
    ".rb", ".php", ".pl", ".pm", ".sh", ".bash", ".zsh", ".fish", ".lua", ".tcl",
    ".ps1", ".psm1", ".psd1",
    // .NET
    ".cs", ".vb", ".fs", ".fsx", ".swift",
    // Data science / stats
    ".r", ".rmd", ".jl", ".m",
    // Functional / other
    ".ml", ".mli", ".hs", ".lhs", ".ex", ".exs", ".erl", ".hrl",
    ".clj", ".cljs", ".cljc", ".edn", ".elm",
    // Dart, SQL, GraphQL
    ".dart", ".sql", ".graphql", ".gql",
    // Config / Build
    ".tf", ".hcl", ".nix", ".proto", ".diff", ".patch", ".asm", ".s",
    // WikINT QCM
    ".qcm",
].join(",");

export function formatFileSize(bytes: number): string {
    if (bytes === 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return `${(bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

export function getFileExtension(filename: string): string {
    const parts = filename.split(".");
    return parts.length > 1 ? parts[parts.length - 1].toLowerCase() : "";
}

/** Extension → color classes used by browse listing, viewer header, and sidebar. */
export const EXT_BADGE_COLORS: Record<string, string> = {
    pdf: "bg-red-100 text-red-800 dark:bg-red-950/40 dark:text-red-300",
    doc: "bg-blue-100 text-blue-800 dark:bg-blue-950/40 dark:text-blue-300",
    docx: "bg-blue-100 text-blue-800 dark:bg-blue-950/40 dark:text-blue-300",
    odt: "bg-blue-100 text-blue-800 dark:bg-blue-950/40 dark:text-blue-300",
    txt: "bg-slate-100 text-slate-800 dark:bg-slate-800/40 dark:text-slate-300",
    md: "bg-slate-100 text-slate-800 dark:bg-slate-800/40 dark:text-slate-300",
    xls: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300",
    xlsx: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300",
    ods: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300",
    csv: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300",
    ppt: "bg-orange-100 text-orange-800 dark:bg-orange-950/40 dark:text-orange-300",
    pptx: "bg-orange-100 text-orange-800 dark:bg-orange-950/40 dark:text-orange-300",
    png: "bg-purple-100 text-purple-800 dark:bg-purple-950/40 dark:text-purple-300",
    jpg: "bg-purple-100 text-purple-800 dark:bg-purple-950/40 dark:text-purple-300",
    jpeg: "bg-purple-100 text-purple-800 dark:bg-purple-950/40 dark:text-purple-300",
    gif: "bg-purple-100 text-purple-800 dark:bg-purple-950/40 dark:text-purple-300",
    webp: "bg-purple-100 text-purple-800 dark:bg-purple-950/40 dark:text-purple-300",
    svg: "bg-purple-100 text-purple-800 dark:bg-purple-950/40 dark:text-purple-300",
    mp4: "bg-pink-100 text-pink-800 dark:bg-pink-950/40 dark:text-pink-300",
    webm: "bg-pink-100 text-pink-800 dark:bg-pink-950/40 dark:text-pink-300",
    mp3: "bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
    wav: "bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
    ogg: "bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
    flac: "bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
    aac: "bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
    m4a: "bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
    epub: "bg-teal-100 text-teal-800 dark:bg-teal-950/40 dark:text-teal-300",
    djvu: "bg-teal-100 text-teal-800 dark:bg-teal-950/40 dark:text-teal-300",
    djv: "bg-teal-100 text-teal-800 dark:bg-teal-950/40 dark:text-teal-300",
    zip: "bg-orange-100 text-orange-800 dark:bg-orange-950/40 dark:text-orange-300",
    js: "bg-yellow-100 text-yellow-800 dark:bg-yellow-950/40 dark:text-yellow-300",
    ts: "bg-blue-100 text-blue-800 dark:bg-blue-950/40 dark:text-blue-300",
    py: "bg-sky-100 text-sky-800 dark:bg-sky-950/40 dark:text-sky-300",
    html: "bg-orange-100 text-orange-800 dark:bg-orange-950/40 dark:text-orange-300",
    css: "bg-blue-100 text-blue-800 dark:bg-blue-950/40 dark:text-blue-300",
    json: "bg-zinc-100 text-zinc-800 dark:bg-zinc-800/40 dark:text-zinc-300",
    qcm: "bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300",
};

const DEFAULT_BADGE_COLOR = "bg-gray-100 text-gray-800 dark:bg-gray-800/40 dark:text-gray-300";

/** Get badge color classes for a file, by extension or MIME type. */
export function getFileBadgeColor(fileName: string, mimeType?: string): string {
    const ext = getFileExtension(fileName);
    if (ext && EXT_BADGE_COLORS[ext]) return EXT_BADGE_COLORS[ext];
    
    if (mimeType) {
        if (mimeType === "application/pdf") return EXT_BADGE_COLORS["pdf"];
        if (mimeType.startsWith("image/")) return EXT_BADGE_COLORS["jpg"];
        if (mimeType.startsWith("video/")) return EXT_BADGE_COLORS["mp4"];
        if (mimeType.startsWith("audio/")) return EXT_BADGE_COLORS["mp3"];
        if (mimeType.includes("document") || mimeType.includes("msword")) return EXT_BADGE_COLORS["doc"];
        if (mimeType.includes("sheet") || mimeType.includes("excel")) return EXT_BADGE_COLORS["xls"];
    }
    
    return DEFAULT_BADGE_COLOR;
}

/** Extension → icon foreground color classes (light + dark variants). */
export const EXT_ICON_COLORS: Record<string, string> = {
    pdf: "text-red-600 dark:text-red-400",
    doc: "text-blue-600 dark:text-blue-400",
    docx: "text-blue-600 dark:text-blue-400",
    odt: "text-blue-600 dark:text-blue-400",
    txt: "text-slate-600 dark:text-slate-400",
    md: "text-slate-600 dark:text-slate-400",
    xls: "text-emerald-600 dark:text-emerald-400",
    xlsx: "text-emerald-600 dark:text-emerald-400",
    ods: "text-emerald-600 dark:text-emerald-400",
    csv: "text-emerald-600 dark:text-emerald-400",
    ppt: "text-orange-600 dark:text-orange-400",
    pptx: "text-orange-600 dark:text-orange-400",
    png: "text-purple-600 dark:text-purple-400",
    jpg: "text-purple-600 dark:text-purple-400",
    jpeg: "text-purple-600 dark:text-purple-400",
    gif: "text-purple-600 dark:text-purple-400",
    webp: "text-purple-600 dark:text-purple-400",
    svg: "text-purple-600 dark:text-purple-400",
    mp4: "text-pink-600 dark:text-pink-400",
    webm: "text-pink-600 dark:text-pink-400",
    avi: "text-pink-600 dark:text-pink-400",
    mkv: "text-pink-600 dark:text-pink-400",
    mp3: "text-amber-600 dark:text-amber-400",
    wav: "text-amber-600 dark:text-amber-400",
    ogg: "text-amber-600 dark:text-amber-400",
    flac: "text-amber-600 dark:text-amber-400",
    aac: "text-amber-600 dark:text-amber-400",
    m4a: "text-amber-600 dark:text-amber-400",
    epub: "text-teal-600 dark:text-teal-400",
    djvu: "text-teal-600 dark:text-teal-400",
    djv: "text-teal-600 dark:text-teal-400",
    zip: "text-orange-600 dark:text-orange-400",
    rar: "text-orange-600 dark:text-orange-400",
    "7z": "text-orange-600 dark:text-orange-400",
    tar: "text-orange-600 dark:text-orange-400",
    gz: "text-orange-600 dark:text-orange-400",
    js: "text-yellow-600 dark:text-yellow-500",
    ts: "text-blue-600 dark:text-blue-400",
    py: "text-sky-600 dark:text-sky-400",
    html: "text-orange-600 dark:text-orange-400",
    css: "text-blue-600 dark:text-blue-400",
    json: "text-zinc-600 dark:text-zinc-400",
    qcm: "text-violet-600 dark:text-violet-400",
};

/** Material type → icon foreground color classes (light + dark variants). */
export const MATERIAL_TYPE_ICON_COLORS: Record<string, string> = {
    polycopie: "text-blue-600 dark:text-blue-400",
    annal: "text-orange-600 dark:text-orange-400",
    cheatsheet: "text-green-600 dark:text-green-400",
    tip: "text-yellow-600 dark:text-yellow-500",
    review: "text-purple-600 dark:text-purple-400",
    discussion: "text-pink-600 dark:text-pink-400",
    video: "text-red-600 dark:text-red-400",
    qcm: "text-violet-600 dark:text-violet-400",
    other: "text-muted-foreground",
    document: "text-muted-foreground",
};

const DEFAULT_ICON_COLOR = "text-muted-foreground";

/** Icon foreground color for a file, derived from extension/MIME type. */
export function getFileIconColor(fileName: string, mimeType?: string): string {
    const ext = getFileExtension(fileName);
    if (ext && EXT_ICON_COLORS[ext]) return EXT_ICON_COLORS[ext];

    if (mimeType) {
        if (mimeType === "application/pdf") return EXT_ICON_COLORS["pdf"];
        if (mimeType === "application/vnd.wikint.qcm+json") return EXT_ICON_COLORS["qcm"];
        if (mimeType.startsWith("image/")) return EXT_ICON_COLORS["jpg"];
        if (mimeType.startsWith("video/")) return EXT_ICON_COLORS["mp4"];
        if (mimeType.startsWith("audio/")) return EXT_ICON_COLORS["mp3"];
        if (mimeType.includes("document") || mimeType.includes("msword")) return EXT_ICON_COLORS["doc"];
        if (mimeType.includes("sheet") || mimeType.includes("excel")) return EXT_ICON_COLORS["xls"];
    }

    return DEFAULT_ICON_COLOR;
}

/** Short uppercase label for a file, e.g. "PDF", "MP3". Falls back to MIME subtype. */
export function getFileBadgeLabel(fileName: string, mimeType?: string): string {
    const ext = getFileExtension(fileName);
    if (ext) return ext.toUpperCase();
    if (mimeType) {
        const sub = mimeType.split("/")[1];
        if (sub && sub !== "octet-stream") return sub.replace(/^(x-|vnd\.)/, "").toUpperCase();
    }
    return "FILE";
}



// ────────────────────────────────────────────────────────────────────────────────
// Client-side image compression
// ────────────────────────────────────────────────────────────────────────────────

const COMPRESSIBLE_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MIN_COMPRESSION_SIZE = 512 * 1024; // 512 KB — skip small files to avoid overhead

interface CompressResult {
    file: File;
    /** True if client-side compression was actually applied and reduced the file size. */
    compressed: boolean;
}

/**
 * Compress image if it meets criteria (JPEG/PNG/WebP, ≥512 KB).
 * Skips GIF (animation loss), SVG (needs raw bytes for security checks), and small files.
 * Fails open: returns original File if compression fails or is not applicable.
 *
 * @param file The original File object
 * @param skip Whether to skip compression entirely
 * @returns Promise resolving to `{ file, compressed }` — `compressed` is true when the
 *          file was actually shrunk client-side.
 */
export async function compressImageIfNeeded(file: File, skip = false): Promise<CompressResult> {
    if (skip) return { file, compressed: false };

    // Skip non-compressible image types
    if (!COMPRESSIBLE_IMAGE_TYPES.has(file.type)) {
        return { file, compressed: false };
    }

    // Skip small files (compression overhead not worth it)
    if (file.size < MIN_COMPRESSION_SIZE) {
        return { file, compressed: false };
    }

    try {
        const { default: imageCompression } = await import("browser-image-compression");
        const compressedFile = await imageCompression(file, {
            maxSizeMB: 4, // Increased from 2 MB
            maxWidthOrHeight: 4096, // Increased from 1920 to preserve 4K (U10)
            useWebWorker: true, // Non-blocking
            initialQuality: 0.85, // Increased from 0.8
            fileType: file.type, // Preserve original format
            preserveExif: false, // Remove EXIF (server will strip anyway, but reduce size)
        });

        // Use compressed only if it actually reduced size
        if (compressedFile.size < file.size) {
            return { file: compressedFile, compressed: true };
        }
        return { file, compressed: false };
    } catch {
        // Fail open: return original file on any error
        return { file, compressed: false };
    }
}

/** MIME type → viewer type mapping. */
const MIME_TO_VIEWER: Record<string, string> = {
    // Documents
    "application/pdf": "pdf",
    // Markdown
    "text/markdown": "markdown",
    "text/x-markdown": "markdown",
    // Images
    "image/png": "image",
    "image/jpeg": "image",
    "image/gif": "image",
    "image/webp": "image",
    "image/svg+xml": "image",
    // Video
    "video/mp4": "video",
    "video/webm": "video",
    "video/ogg": "video",
    // Audio
    "audio/mpeg": "audio",
    "audio/wav": "audio",
    "audio/ogg": "audio",
    "audio/flac": "audio",
    "audio/aac": "audio",
    "audio/mp3": "audio",
    "audio/mp4": "audio",
    // Office
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "office",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "office",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "office",
    "application/msword": "office",
    "application/vnd.ms-excel": "office",
    "application/vnd.ms-powerpoint": "office",
    "application/vnd.oasis.opendocument.text": "office",
    "application/vnd.oasis.opendocument.spreadsheet": "office",
    "application/vnd.oasis.opendocument.presentation": "office",
    // E-books
    "application/epub+zip": "epub",
    // DjVu
    "image/vnd.djvu": "djvu",
    "image/x-djvu": "djvu",
    // CSV
    "text/csv": "csv",
    "application/csv": "csv",
    // Code / text — explicit MIME entries
    "text/plain": "code",
    "application/x-tex": "code",
    "text/x-tex": "code",
    "text/x-python": "code",
    "application/x-python": "code",
    "application/x-python-code": "code",
    "text/x-c": "code",
    "text/x-csrc": "code",
    "text/x-chdr": "code",
    "text/x-c++": "code",
    "text/x-c++src": "code",
    "text/x-c++hdr": "code",
    "text/x-java-source": "code",
    "text/x-java": "code",
    "text/x-kotlin": "code",
    "text/x-scala": "code",
    "text/x-ruby": "code",
    "application/x-ruby": "code",
    "text/x-php": "code",
    "application/x-php": "code",
    "text/x-go": "code",
    "text/x-rust": "code",
    "text/x-swift": "code",
    "text/javascript": "code",
    "application/javascript": "code",
    "application/typescript": "code",
    "text/typescript": "code",
    "text/css": "code",
    "text/html": "code",
    "application/json": "code",
    "application/xml": "code",
    "text/xml": "code",
    "application/x-yaml": "code",
    "text/yaml": "code",
    "application/toml": "code",
    "application/x-toml": "code",
    "application/sql": "code",
    "text/x-sql": "code",
    "text/x-r": "code",
    "text/x-r-source": "code",
    "text/x-lua": "code",
    "text/x-shellscript": "code",
    "application/x-sh": "code",
    "application/x-bash": "code",
    "text/x-diff": "code",
    "text/x-patch": "code",
    "text/x-haskell": "code",
    "text/x-lisp": "code",
    "text/x-clojure": "code",
    "text/x-elixir": "code",
    "text/x-erlang": "code",
    "text/x-julia": "code",
    "text/x-dart": "code",
    "text/x-nim": "code",
    "text/x-zig": "code",
    "text/x-powershell": "code",
    "application/x-powershell": "code",
    "text/x-graphql": "code",
    "application/graphql": "code",
    "text/x-protobuf": "code",
    "text/x-nix": "code",
};

/** Code extensions that should use the code viewer. */
const CODE_EXTENSIONS = new Set([
    // JavaScript / TypeScript / Web
    "js", "mjs", "cjs", "ts", "jsx", "tsx", "vue", "svelte",
    "html", "htm", "css", "scss", "sass", "less",
    // Python
    "py", "pyw", "pyi",
    // Java / JVM
    "java", "kt", "kts", "scala", "groovy", "gradle",
    // C / C++
    "c", "h", "cpp", "cxx", "cc", "hpp", "hxx",
    // Systems
    "rs", "go", "zig", "v", "nim", "odin", "d",
    // Scripting
    "rb", "php", "pl", "pm", "sh", "bash", "zsh", "fish", "lua", "tcl",
    "ps1", "psm1", "psd1",
    // .NET
    "cs", "vb", "fs", "fsx", "swift",
    // Data / Query
    "sql", "graphql", "gql", "r", "rmd", "jl",
    // Config / markup
    "json", "json5", "jsonc", "yaml", "yml", "toml", "xml", "ini", "cfg", "conf", "env",
    "tf", "hcl", "nix", "proto", "diff", "patch", "asm", "s",
    // TeX / LaTeX
    "tex", "latex", "sty", "cls", "bib", "dtx", "ins",
    // Functional
    "ml", "mli", "hs", "lhs", "ex", "exs", "erl", "hrl",
    "clj", "cljs", "cljc", "edn", "elm",
    // Dart
    "dart",
    // Text / misc
    "m", "txt", "log", "md", "markdown", "rst", "adoc",
]);

/** Extension → viewer type fallback mapping. */
const EXT_TO_VIEWER: Record<string, string> = {
    pdf: "pdf",
    md: "markdown",
    png: "image",
    jpg: "image",
    jpeg: "image",
    gif: "image",
    webp: "image",
    svg: "image",
    mp4: "video",
    webm: "video",
    ogg: "video",
    mp3: "audio",
    wav: "audio",
    flac: "audio",
    m4a: "audio",
    aac: "audio",
    docx: "office",
    xlsx: "office",
    pptx: "office",
    doc: "office",
    xls: "office",
    ppt: "office",
    odt: "office",
    ods: "office",
    epub: "epub",
    djvu: "djvu",
    djv: "djvu",
    csv: "csv",
};

/**
 * Determines the viewer type (e.g., 'pdf', 'image', 'code') based on MIME type and file name.
 */
export function getViewerType(mimeType: string, fileName: string): string {
    const ext = getFileExtension(fileName);

    // QCM check before other lookups
    if (mimeType === "application/vnd.wikint.qcm+json" || ext === "qcm") return "qcm";

    // 1. Exact MIME match
    if (MIME_TO_VIEWER[mimeType]) return MIME_TO_VIEWER[mimeType];

    // 2. MIME prefix match
    if (mimeType.startsWith("image/")) return "image";
    if (mimeType.startsWith("video/")) return "video";
    if (mimeType.startsWith("audio/")) return "audio";
    if (mimeType.startsWith("text/")) return "code";

    // 3. Force video player for video extensions if mime type is ambiguous
    if (ext === "mp4" || ext === "webm" || ext === "ogg" || ext === "mov") {
        return "video";
    }

    // 4. File extension fallback
    if (EXT_TO_VIEWER[ext]) return EXT_TO_VIEWER[ext];
    if (CODE_EXTENSIONS.has(ext)) return "code";

    return "generic";
}



