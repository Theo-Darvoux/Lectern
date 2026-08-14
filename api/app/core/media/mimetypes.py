import mimetypes as _stdlib_mimetypes
from pathlib import Path
from typing import Final

# Single Source of Truth for allowed file formats and their accepted MIME types.
# To allow a new file format (e.g., '.csv'), add a single entry to ALLOWED_FORMAT_REGISTRY!
ALLOWED_FORMAT_REGISTRY: Final[dict[str, list[str]]] = {
    # Documents
    ".pdf": ["application/pdf"],
    ".epub": ["application/epub+zip"],
    ".djvu": ["image/vnd.djvu"],
    ".djv": ["image/vnd.djvu"],
    # Images
    ".png": ["image/png"],
    ".jpg": ["image/jpeg"],
    ".jpeg": ["image/jpeg"],
    ".gif": ["image/gif"],
    ".webp": ["image/webp"],
    ".svg": ["image/svg+xml"],
    # Audio
    ".mp3": ["audio/mpeg", "audio/mp3"],
    ".wav": ["audio/wav", "audio/x-wav"],
    ".ogg": ["audio/ogg", "video/ogg"],
    ".flac": ["audio/flac", "audio/x-flac"],
    ".aac": ["audio/aac", "audio/x-aac"],
    ".m4a": ["audio/mp4", "audio/x-m4a"],
    # Video
    ".mp4": ["video/mp4"],
    ".webm": ["video/webm", "audio/webm"],
    # Office (modern + legacy + ODF)
    ".docx": ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
    ".xlsx": ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
    ".pptx": ["application/vnd.openxmlformats-officedocument.presentationml.presentation"],
    ".doc": ["application/msword"],
    ".xls": ["application/vnd.ms-excel"],
    ".ppt": ["application/vnd.ms-powerpoint"],
    ".odt": ["application/vnd.oasis.opendocument.text"],
    ".ods": ["application/vnd.oasis.opendocument.spreadsheet"],
    ".odp": ["application/vnd.oasis.opendocument.presentation"],
    # QCM
    ".qcm": ["application/vnd.lectern.qcm+json"],
    # Text / Data / Markup
    ".csv": ["text/csv", "application/csv", "text/plain"],
    ".json": ["application/json", "text/plain"],
    ".json5": ["application/json5", "application/json", "text/plain"],
    ".jsonc": ["application/jsonc", "application/json", "text/plain"],
    ".xml": ["application/xml", "text/xml", "text/plain"],
    ".yaml": ["application/x-yaml", "text/yaml", "text/plain"],
    ".yml": ["application/x-yaml", "text/yaml", "text/plain"],
    ".toml": ["application/toml", "application/x-toml", "text/plain"],
    ".md": ["text/markdown", "text/x-markdown", "text/plain"],
    ".markdown": ["text/markdown", "text/x-markdown", "text/plain"],
    ".rst": ["text/plain"],
    ".adoc": ["text/plain"],
    ".txt": ["text/plain"],
    ".ini": ["text/plain"],
    ".cfg": ["text/plain"],
    ".conf": ["text/plain"],
    ".log": ["text/plain"],
    ".env": ["text/plain"],
    # TeX / LaTeX
    ".tex": ["application/x-tex", "text/x-tex", "text/plain"],
    ".latex": ["application/x-tex", "text/x-tex", "text/plain"],
    ".sty": ["text/plain"],
    ".cls": ["text/plain"],
    ".bib": ["text/plain"],
    ".dtx": ["text/plain"],
    ".ins": ["text/plain"],
    # C / C++
    ".c": ["text/x-c", "text/x-csrc", "text/plain"],
    ".h": ["text/x-chdr", "text/x-c", "text/plain"],
    ".cpp": ["text/x-c++", "text/x-c++src", "text/plain"],
    ".cxx": ["text/x-c++", "text/x-c++src", "text/plain"],
    ".cc": ["text/x-c++", "text/x-c++src", "text/plain"],
    ".hpp": ["text/x-c++hdr", "text/x-c++", "text/plain"],
    ".hxx": ["text/x-c++hdr", "text/x-c++", "text/plain"],
    # Python
    ".py": ["text/x-python", "application/x-python", "application/x-python-code", "text/plain"],
    ".pyw": ["text/x-python", "application/x-python", "text/plain"],
    ".pyi": ["text/x-python", "application/x-python", "text/plain"],
    # Java / JVM
    ".java": ["text/x-java-source", "text/x-java", "text/plain"],
    ".kt": ["text/x-kotlin", "text/plain"],
    ".kts": ["text/x-kotlin", "text/plain"],
    ".scala": ["text/x-scala", "text/plain"],
    ".groovy": ["text/plain"],
    ".gradle": ["text/plain"],
    # Web
    ".js": ["text/javascript", "application/javascript", "text/plain"],
    ".mjs": ["text/javascript", "application/javascript", "text/plain"],
    ".cjs": ["text/javascript", "application/javascript", "text/plain"],
    ".ts": ["text/typescript", "application/typescript", "text/plain"],
    ".jsx": ["text/javascript", "text/plain"],
    ".tsx": ["text/typescript", "text/plain"],
    ".vue": ["text/plain"],
    ".svelte": ["text/plain"],
    ".html": ["text/html", "text/plain"],
    ".htm": ["text/html", "text/plain"],
    ".css": ["text/css", "text/plain"],
    ".scss": ["text/css", "text/plain"],
    ".sass": ["text/css", "text/plain"],
    ".less": ["text/css", "text/plain"],
    # Systems / Low-level
    ".rs": ["text/x-rust", "text/plain"],
    ".go": ["text/x-go", "text/plain"],
    ".zig": ["text/x-zig", "text/plain"],
    ".v": ["text/plain"],
    ".nim": ["text/x-nim", "text/plain"],
    ".odin": ["text/plain"],
    ".d": ["text/plain"],
    # Scripting
    ".rb": ["text/x-ruby", "application/x-ruby", "text/plain"],
    ".php": ["text/x-php", "application/x-php", "text/plain"],
    ".pl": ["text/plain"],
    ".pm": ["text/plain"],
    ".sh": ["application/x-sh", "application/x-bash", "text/x-shellscript", "text/plain"],
    ".bash": ["application/x-sh", "application/x-bash", "text/x-shellscript", "text/plain"],
    ".zsh": ["text/x-shellscript", "text/plain"],
    ".fish": ["text/x-shellscript", "text/plain"],
    ".ps1": ["text/x-powershell", "application/x-powershell", "text/plain"],
    ".psm1": ["text/x-powershell", "application/x-powershell", "text/plain"],
    ".psd1": ["text/x-powershell", "application/x-powershell", "text/plain"],
    ".lua": ["text/x-lua", "text/plain"],
    ".tcl": ["text/plain"],
    # .NET
    ".cs": ["text/plain"],
    ".vb": ["text/plain"],
    ".fs": ["text/plain"],
    ".fsx": ["text/plain"],
    ".swift": ["text/x-swift", "text/plain"],
    # Data science / stats
    ".r": ["text/x-r", "text/x-r-source", "text/plain"],
    ".rmd": ["text/plain"],
    ".ipynb": ["application/json", "text/plain"],
    ".jl": ["text/x-julia", "text/plain"],
    ".m": ["text/plain"],
    # ML / AI
    ".ml": ["text/plain"],
    ".mli": ["text/plain"],
    # Functional
    ".hs": ["text/x-haskell", "text/plain"],
    ".lhs": ["text/plain"],
    ".ex": ["text/x-elixir", "text/plain"],
    ".exs": ["text/x-elixir", "text/plain"],
    ".erl": ["text/x-erlang", "text/plain"],
    ".hrl": ["text/x-erlang", "text/plain"],
    ".clj": ["text/x-clojure", "text/plain"],
    ".cljs": ["text/x-clojure", "text/plain"],
    ".cljc": ["text/x-clojure", "text/plain"],
    ".edn": ["text/plain"],
    ".elm": ["text/plain"],
    # Dart / Flutter
    ".dart": ["text/x-dart", "text/plain"],
    # Database / Query
    ".sql": ["application/sql", "text/x-sql", "text/plain"],
    ".graphql": ["text/x-graphql", "application/graphql", "text/plain"],
    ".gql": ["text/x-graphql", "application/graphql", "text/plain"],
    # Config / Build
    ".makefile": ["text/plain"],
    ".cmake": ["text/plain"],
    ".dockerfile": ["text/plain"],
    ".tf": ["text/plain"],
    ".hcl": ["text/plain"],
    ".nix": ["application/x-nix", "text/x-nix", "text/plain"],
    ".bazel": ["text/plain"],
    ".bzl": ["text/plain"],
    ".proto": ["text/x-protobuf", "application/protobuf", "text/plain"],
    ".thrift": ["text/plain"],
    ".capnp": ["text/plain"],
    # Other
    ".diff": ["text/x-diff", "text/plain"],
    ".patch": ["text/x-patch", "text/plain"],
    ".asm": ["text/plain"],
    ".s": ["text/plain"],
    ".wat": ["text/plain"],
    ".wasm": ["application/wasm", "text/plain"],
}

# Derived collections ensuring a Single Source of Truth
ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset(ALLOWED_FORMAT_REGISTRY.keys())

# Whitelist of specific extension -> MIME mappings (excluding generic text/plain)
EXTENSION_MAPPING: Final[dict[str, list[str]]] = {
    ext: [m for m in mimes if m != "text/plain"]
    for ext, mimes in ALLOWED_FORMAT_REGISTRY.items()
    if any(m != "text/plain" for m in mimes)
}


def _build_mime_to_ext() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for ext, mimes in EXTENSION_MAPPING.items():
        for mime in mimes:
            mapping.setdefault(mime, ext)
    return mapping


MIME_TO_EXTENSION: Final[dict[str, str]] = _build_mime_to_ext()

# Canonical MIME type for QCM (Multiple Choice Questions) files.
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
ALLOWED_MIME_TYPES: Final[frozenset[str]] = frozenset(
    mime for mimes in ALLOWED_FORMAT_REGISTRY.values() for mime in mimes
) | {"text/plain"}


def guess_mime_from_bytes(data: bytes, default: str = "application/octet-stream") -> str:
    """Detect MIME type from file magic bytes."""
    if len(data) < 4:
        return default

    # Gzip must be recognized before extension fallback. Gzip is not an allowed
    # upload MIME, so bytes disguised behind a text extension fail closed.
    if data.startswith(b"\x1f\x8b"):
        return "application/gzip"

    # PDF
    if data.startswith(b"%PDF-"):
        return "application/pdf"

    # Legacy Microsoft Office files share the generic OLE compound container.
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "application/x-ole-storage"

    # Images
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"

    # SVG
    _stripped = data[:1024].lstrip()
    _lower = _stripped.lower()
    if b"<svg" in _lower and _lower.startswith((b"<svg", b"<?xml", b"<!--", b"<!doctype")):
        return "image/svg+xml"

    # DjVu
    if data.startswith(b"AT&TFORM"):
        return "image/vnd.djvu"

    # ZIP-based
    if data.startswith(b"PK\x03\x04"):
        header = data[:2048]
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

    # Audio
    if data.startswith(b"ID3") or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    if data.startswith(b"fLaC"):
        return "audio/flac"
    if data.startswith(b"OggS"):
        if b"\x80theora" in data[:512]:
            return "video/ogg"
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
    def normalize_mime(mime_type: str) -> str:
        """Return the canonical base media type used by the upload pipeline."""
        return mime_type.split(";", 1)[0].strip().lower()

    @staticmethod
    def is_supported_extension(ext: str, allowed: set[str] | frozenset[str] | None = None) -> bool:
        if allowed is not None:
            return ext.lower() in allowed
        return ext.lower() in ALLOWED_EXTENSIONS

    @staticmethod
    def get_canonical_extension(mime_type: str) -> str | None:
        return MIME_TO_EXTENSION.get(mime_type)

    @staticmethod
    def is_valid_extension_for_mime(filename_or_ext: str, mime_type: str) -> bool:
        """Check if a filename or extension is compatible with the given MIME type."""
        ext = (
            filename_or_ext.lower()
            if filename_or_ext.startswith(".")
            else MimeRegistry.get_extension(filename_or_ext)
        )
        if not ext:
            return False
        allowed_mimes = ALLOWED_FORMAT_REGISTRY.get(ext, [])
        norm_mime = MimeRegistry.normalize_mime(mime_type)
        return norm_mime in allowed_mimes or (
            norm_mime == "text/plain" and "text/plain" in allowed_mimes
        )

    @staticmethod
    def get_allowed_mimes_for_extension(ext: str) -> list[str]:
        return EXTENSION_MAPPING.get(ext.lower(), [])

    @staticmethod
    def is_allowed_mime(mime_type: str, allowed: set[str] | frozenset[str] | None = None) -> bool:
        """Strict check: is this MIME type explicitly allowed for upload?"""
        base_mime = MimeRegistry.normalize_mime(mime_type)
        if allowed is not None:
            return base_mime in allowed
        return base_mime in ALLOWED_MIME_TYPES

    @staticmethod
    def get_authoritative_mime(filename: str, magic_mime: str) -> str:
        """Resolve MIME type giving precedence to magic bytes, falling back to extension."""
        magic_mime = MimeRegistry.normalize_mime(magic_mime)
        ext = MimeRegistry.get_extension(filename)
        extension_mimes = EXTENSION_MAPPING.get(ext, [])

        if magic_mime == "application/x-ole-storage":
            for candidate in extension_mimes:
                if candidate in OLE2_MIME_TYPES:
                    return candidate
            return magic_mime

        if magic_mime == "video/mp4" and "audio/mp4" in extension_mimes:
            return "audio/mp4"

        if magic_mime != "application/octet-stream":
            return magic_mime

        return MimeRegistry.resolve_upload_mime(filename, magic_mime)

    @staticmethod
    def get_extension(filename: str) -> str:
        """Extract extension from filename.

        Uses pathlib.Path for clean single (.pdf) and compound (.tar.gz) extension extraction.
        Also supports dotfiles (e.g. '.env') and special extensionless names
        (e.g. 'Dockerfile') if registered in ALLOWED_EXTENSIONS.
        """
        if not filename:
            return ""

        p = Path(filename)
        suffixes = p.suffixes
        if len(suffixes) > 1:
            compound = "".join(suffixes).lower()
            if compound in ALLOWED_EXTENSIONS:
                return compound

        suffix = p.suffix.lower()
        if suffix:
            return suffix

        candidate = f".{filename.lstrip('.').lower()}"
        if candidate in ALLOWED_EXTENSIONS:
            return candidate

        return ""

    @staticmethod
    def resolve_upload_mime(filename: str, raw_mime: str) -> str:
        """Resolve the best MIME to use for upload validation."""
        raw_mime = MimeRegistry.normalize_mime(raw_mime)
        if raw_mime and raw_mime != "application/octet-stream":
            return raw_mime
        ext = MimeRegistry.get_extension(filename)
        known = EXTENSION_MAPPING.get(ext)
        if known:
            return known[0]

        guessed, _ = _stdlib_mimetypes.guess_type(filename)
        if guessed and guessed in ALLOWED_MIME_TYPES:
            return guessed

        if ext in ALLOWED_EXTENSIONS:
            return "text/plain"
        return raw_mime
