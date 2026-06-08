import mimetypes
from pathlib import Path
from typing import Final

# ────────────────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────────────────

# Allowed file extensions — matches viewer-supported types
ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        # Documents
        ".pdf",
        ".epub",
        ".djvu",
        ".djv",
        # Images
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        # Audio
        ".mp3",
        ".wav",
        ".ogg",
        ".flac",
        ".aac",
        ".m4a",
        # Video
        ".mp4",
        ".webm",
        # Office (modern + legacy + ODF)
        ".docx",
        ".xlsx",
        ".pptx",
        ".doc",
        ".xls",
        ".ppt",
        ".odt",
        ".ods",
        ".odp",
        # Text / markup
        ".md",
        ".markdown",
        ".rst",
        ".adoc",
        ".txt",
        ".csv",
        ".json",
        ".json5",
        ".jsonc",
        ".xml",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".log",
        ".env",
        # TeX / LaTeX
        ".tex",
        ".latex",
        ".sty",
        ".cls",
        ".bib",
        ".dtx",
        ".ins",
        # C / C++
        ".c",
        ".h",
        ".cpp",
        ".cxx",
        ".cc",
        ".hpp",
        ".hxx",
        # Python
        ".py",
        ".pyw",
        ".pyi",
        # Java / JVM
        ".java",
        ".kt",
        ".kts",
        ".scala",
        ".groovy",
        ".gradle",
        # Web
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".jsx",
        ".tsx",
        ".vue",
        ".svelte",
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".sass",
        ".less",
        # Systems / Low-level
        ".rs",
        ".go",
        ".zig",
        ".v",
        ".nim",
        ".odin",
        ".d",
        # Scripting
        ".rb",
        ".php",
        ".pl",
        ".pm",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".psm1",
        ".psd1",
        ".lua",
        ".tcl",
        # .NET
        ".cs",
        ".vb",
        ".fs",
        ".fsx",
        ".swift",
        # Data science / stats
        ".r",
        ".rmd",
        ".ipynb",
        ".jl",
        ".m",
        # ML / AI
        ".ml",
        ".mli",
        # Functional
        ".hs",
        ".lhs",
        ".ex",
        ".exs",
        ".erl",
        ".hrl",
        ".clj",
        ".cljs",
        ".cljc",
        ".edn",
        ".elm",
        # Dart / Flutter
        ".dart",
        # Database / Query
        ".sql",
        ".graphql",
        ".gql",
        # Config / Build
        ".makefile",
        ".cmake",
        ".dockerfile",
        ".tf",
        ".hcl",
        ".nix",
        ".bazel",
        ".bzl",
        ".proto",
        ".thrift",
        ".capnp",
        # Other
        ".diff",
        ".patch",
        ".asm",
        ".s",
        ".wat",
        ".wasm",
        # QCM
        ".qcm",
    }
)

# Whitelist of trusted extension -> MIME mappings.
EXTENSION_MAPPING: Final[dict[str, list[str]]] = {
    ".pdf": ["application/pdf"],
    ".png": ["image/png"],
    ".jpg": ["image/jpeg"],
    ".jpeg": ["image/jpeg"],
    ".gif": ["image/gif"],
    ".webp": ["image/webp"],
    ".svg": ["image/svg+xml"],
    ".mp3": ["audio/mpeg", "audio/mp3"],
    ".wav": ["audio/wav", "audio/x-wav"],
    ".ogg": ["audio/ogg", "video/ogg"],
    ".flac": ["audio/flac", "audio/x-flac"],
    ".aac": ["audio/aac", "audio/x-aac"],
    ".m4a": ["audio/mp4", "audio/x-m4a"],
    ".mp4": ["video/mp4"],
    ".webm": ["video/webm", "audio/webm"],
    ".docx": ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
    ".xlsx": ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
    ".pptx": ["application/vnd.openxmlformats-officedocument.presentationml.presentation"],
    ".epub": ["application/epub+zip"],
    ".djvu": ["image/vnd.djvu"],
    ".djv": ["image/vnd.djvu"],
    ".doc": ["application/msword"],
    ".xls": ["application/vnd.ms-excel"],
    ".ppt": ["application/vnd.ms-powerpoint"],
    ".odt": ["application/vnd.oasis.opendocument.text"],
    ".ods": ["application/vnd.oasis.opendocument.spreadsheet"],
    ".odp": ["application/vnd.oasis.opendocument.presentation"],
    ".tex": ["application/x-tex", "text/x-tex"],
    ".qcm": ["application/vnd.lectern.qcm+json"],
}

# Reverse mapping: MIME type -> canonical extension
MIME_TO_EXTENSION: Final[dict[str, str]] = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/vnd.djvu": ".djvu",
    "audio/mpeg": ".mp3",
    "audio/flac": ".flac",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/mp4": ".m4a",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "audio/webm": ".webm",
    "application/epub+zip": ".epub",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/vnd.lectern.qcm+json": ".qcm",
}

# Canonical MIME type for QCM (Multiple Choice Questions) files.
# Imported by routers and tests — do not duplicate this string elsewhere.
QCM_MIME_TYPE: Final[str] = "application/vnd.lectern.qcm+json"

# MIME types safe for gzip with Content-Encoding header
GZIP_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/json",
        "application/xml",
        "application/x-yaml",
        "text/yaml",
        "image/svg+xml",
    }
)

# MIME types that are ZIP archives
ZIP_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
        "application/epub+zip",
    }
)

# Legacy Office MIME types (OLE2)
OLE2_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
    }
)

# Flattened set of all allowed MIME types for strict server-side rejection.
# Includes both binary formats and common text/code types.
ALLOWED_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {
        # Flattened from EXTENSION_MAPPING (includes application/vnd.lectern.qcm+json)
        *[mime for mimes in EXTENSION_MAPPING.values() for mime in mimes],
        # Plain text (catch-all for many code files)
        "text/plain",
        # Markdown
        "text/markdown",
        "text/x-markdown",
        # CSV
        "text/csv",
        "application/csv",
        # Web
        "text/html",
        "text/css",
        "text/javascript",
        "application/javascript",
        "application/typescript",
        "text/typescript",
        # Python
        "text/x-python",
        "application/x-python",
        "application/x-python-code",
        # C / C++
        "text/x-c",
        "text/x-csrc",
        "text/x-chdr",
        "text/x-c++",
        "text/x-c++src",
        "text/x-c++hdr",
        # Java / JVM
        "text/x-java-source",
        "text/x-java",
        "text/x-kotlin",
        "text/x-scala",
        # Shell
        "application/x-sh",
        "application/x-bash",
        "text/x-shellscript",
        # Ruby
        "application/x-ruby",
        "text/x-ruby",
        # PHP
        "application/x-php",
        "text/x-php",
        # Go
        "text/x-go",
        # Rust
        "text/x-rust",
        # Swift
        "text/x-swift",
        # Data formats
        "application/json",
        "application/xml",
        "text/xml",
        "application/x-yaml",
        "text/yaml",
        "application/toml",
        "application/x-toml",
        # SQL
        "application/sql",
        "text/x-sql",
        # TeX
        "application/x-tex",
        "text/x-tex",
        # R
        "text/x-r",
        "text/x-r-source",
        # Lua
        "text/x-lua",
        # Diff / patch
        "text/x-diff",
        "text/x-patch",
        # Other code mime types OSes may report
        "text/x-haskell",
        "text/x-lisp",
        "text/x-clojure",
        "text/x-elixir",
        "text/x-erlang",
        "text/x-julia",
        "text/x-dart",
        "text/x-nim",
        "text/x-zig",
        "text/x-powershell",
        "application/x-powershell",
        "text/x-graphql",
        "application/graphql",
        "text/x-protobuf",
        "application/protobuf",
        "application/x-nix",
        "text/x-nix",
    }
)

# ────────────────────────────────────────────────────────────────────────────────
# Logic
# ────────────────────────────────────────────────────────────────────────────────


def guess_mime_from_bytes(data: bytes, default: str = "application/octet-stream") -> str:
    """Detect MIME type from file magic bytes. Covers all viewer-supported types."""
    if len(data) < 4:
        return default

    # PDF
    if data.startswith(b"%PDF-"):
        return "application/pdf"

    # Images
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"

    # SVG — must appear near the document start (after optional BOM / XML declaration)
    _stripped = data[:500].lstrip()
    _lower = _stripped.lower()
    if _lower.startswith(b"<svg") or (_lower.startswith(b"<?xml") and b"<svg" in _lower):
        return "image/svg+xml"

    # DjVu
    if data.startswith(b"AT&TFORM"):
        return "image/vnd.djvu"

    # ZIP-based formats (OOXML, EPUB, ODF)
    if data.startswith(b"PK\x03\x04"):
        header = data[:200]
        if b"mimetypeapplication/epub+zip" in header:
            return "application/epub+zip"
        if b"mimetypeapplication/vnd.oasis.opendocument.text" in header:
            return "application/vnd.oasis.opendocument.text"
        if b"mimetypeapplication/vnd.oasis.opendocument.spreadsheet" in header:
            return "application/vnd.oasis.opendocument.spreadsheet"
        if b"mimetypeapplication/vnd.oasis.opendocument.presentation" in header:
            return "application/vnd.oasis.opendocument.presentation"
        # OOXML
        if b"word/" in data[:2048]:
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if b"xl/" in data[:2048]:
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if b"ppt/" in data[:2048]:
            return "application/vnd.openxmlformats-officedocument.presentationml.presentation"

    # Audio formats
    if data.startswith(b"ID3") or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    if data.startswith(b"fLaC"):
        return "audio/flac"
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WAVE":
        return "audio/wav"
    if len(data) >= 8 and data[4:8] == b"ftyp":
        brand = data[8:12] if len(data) >= 12 else b""
        if brand in (b"M4A ", b"M4B "):
            return "audio/mp4"
        return "video/mp4"

    # TeX / LaTeX
    _tex_stripped = data[:100].lstrip()
    if _tex_stripped.startswith((b"\\documentclass", b"\\documentstyle", b"\\begin{")):
        return "text/x-tex"

    return default


def guess_mime_from_file_path(path: Path) -> str:
    """Read first 2KB of file and guess MIME from bytes."""
    with open(path, "rb") as f:
        data = f.read(2048)
    return guess_mime_from_bytes(data)


class MimeRegistry:
    """Central registry for file type validation and canonical mapping."""

    @staticmethod
    def is_supported_extension(ext: str, allowed: set[str] | frozenset[str] | None = None) -> bool:
        if allowed is not None:
            return ext.lower() in allowed
        return ext.lower() in ALLOWED_EXTENSIONS

    @staticmethod
    def get_canonical_extension(mime_type: str) -> str | None:
        return MIME_TO_EXTENSION.get(mime_type)

    @staticmethod
    def get_allowed_mimes_for_extension(ext: str) -> list[str]:
        return EXTENSION_MAPPING.get(ext.lower(), [])

    @staticmethod
    def is_allowed_mime(mime_type: str, allowed: set[str] | frozenset[str] | None = None) -> bool:
        """Strict check: is this MIME type explicitly allowed for upload?"""
        # Strip parameters like "; charset=utf-8"
        base_mime = mime_type.split(";")[0].strip().lower()
        if allowed is not None:
            return base_mime in allowed
        return base_mime in ALLOWED_MIME_TYPES

    @staticmethod
    def get_authoritative_mime(filename: str, magic_mime: str) -> str:
        """Resolve MIME type giving precedence to magic bytes, falling back to extension."""
        if magic_mime != "application/octet-stream":
            return magic_mime

        guessed, _ = mimetypes.guess_type(filename)
        return guessed or "application/octet-stream"

    @staticmethod
    def get_extension(filename: str) -> str:
        """Extract extension from filename.

        To align with frontend, treats the last dot-separated part (or the entire filename
        if no dot is present) as the extension, with a leading dot.
        """
        if not filename:
            return ""
        parts = filename.split(".")
        return f".{parts[-1].lower()}" if len(parts) > 1 or (parts and parts[0]) else ""

    @staticmethod
    def resolve_upload_mime(filename: str, raw_mime: str) -> str:
        """Resolve the best MIME to use for upload validation.

        When the client sends application/octet-stream (browser doesn't know the type),
        try to determine the real MIME from the file extension so the upload can proceed.
        The actual MIME is always re-detected from magic bytes during processing.
        """
        if raw_mime and raw_mime != "application/octet-stream":
            return raw_mime
        ext = MimeRegistry.get_extension(filename)
        # Check our trusted extension mapping first
        known = EXTENSION_MAPPING.get(ext)
        if known:
            return known[0]
        # Fall back to Python's mimetypes database
        guessed, _ = mimetypes.guess_type(filename)
        if guessed and guessed in ALLOWED_MIME_TYPES:
            return guessed
        # For any extension we explicitly allow, use text/plain as safe fallback
        if ext in ALLOWED_EXTENSIONS:
            return "text/plain"
        return raw_mime
