import re
from typing import Annotated, Any

from pydantic import BeforeValidator

# ---------------------------------------------------------------------------
# Dangerous character stripping
# ---------------------------------------------------------------------------

# Strips characters that are invisible, dangerous, or have no place in
# user-supplied text:
#   - C0 controls except HT (\x09), LF (\x0a), CR (\x0d)
#   - DEL (\x7f)
#   - C1 controls (\x80-\x9f)
#   - Zero-width / invisible Unicode formatting chars
#   - BIDI override / isolate chars (used in homograph/spoofing attacks)
#   - Deprecated formatting chars
#   - BOM (U+FEFF)
#   - Interlinear annotation anchors
_DANGEROUS_CHARS_RE = re.compile(
    "["
    "\x00-\x08"  # C0: NUL..BS  (HT=\x09 kept)
    "\x0b\x0c"  # C0: VT, FF   (LF=\x0a, CR=\x0d kept)
    "\x0e-\x1f"  # C0: SO..US
    "\x7f"  # DEL
    "\x80-\x9f"  # C1 controls
    "\u0300-\u036f"  # Combining Diacritical Marks (Zalgo main range)
    "\u1dc0-\u1dff"  # Combining Diacritical Marks Supplement
    "\u20d0-\u20ff"  # Combining Diacritical Marks for Symbols
    "\ufe20-\ufe2f"  # Combining Half Marks
    "\u200b-\u200f"  # zero-width space/non-joiner/joiner, LRM, RLM
    "\u2028\u2029"  # line/paragraph separator (can break JS parsers)
    "\u202a-\u202e"  # BIDI embedding/override chars
    "\u2060-\u2064"  # word joiner, function application, etc.
    "\u206a-\u206f"  # deprecated formatting chars
    "\ufeff"  # BOM / ZWNBSP
    "\ufff9-\ufffb"  # interlinear annotation anchors
    "]"
)

# Matches characters not allowed in name/title fields.
# Allowed: printable ASCII (U+0020–U+007E) plus precomposed Latin accented
# characters used in French and other Western European languages:
#   U+00C0–U+00D6  À–Ö  (Latin-1 uppercase, excl. × U+00D7)
#   U+00D8–U+00F6  Ø–ö  (Latin-1 mixed, excl. ÷ U+00F7)
#   U+00F8–U+017F  ø–ſ  (Latin-1 remainder + Latin Extended-A: ç œ Œ æ Æ …)
# Combining marks are already stripped by _DANGEROUS_CHARS_RE before this check.
_INVALID_NAME_CHAR_RE = re.compile(r"[^\x20-\x7e\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u017f]")


def clean_text(v: str) -> str:
    """Strip invisible/dangerous Unicode from a string.

    Keeps printable chars plus whitespace that has legitimate text use
    (tab, newline, carriage-return).
    """
    return _DANGEROUS_CHARS_RE.sub("", v)


def _sanitize_value(v: Any) -> Any:
    if isinstance(v, str):
        return clean_text(v)
    return v


# ---------------------------------------------------------------------------
# Public types / helpers
# ---------------------------------------------------------------------------

# SanitizedStr: strips all invisible/dangerous chars (including Zalgo combining marks).
# Use as the type annotation for any user-supplied text field.
SanitizedStr = Annotated[str, BeforeValidator(_sanitize_value)]


def _validate_name_value(v: Any) -> Any:
    """Reject strings containing characters outside the name allowlist.

    Folder names and material titles must contain only:
    - Printable ASCII (U+0020–U+007E)
    - Precomposed Latin accented characters (U+00C0–U+017F, covers all French)

    Zalgo/combining marks are stripped by clean_text before this check,
    so only precomposed forms (e.g. é U+00E9) pass through.
    """
    if isinstance(v, str):
        v = clean_text(v)
        if _INVALID_NAME_CHAR_RE.search(v):
            raise ValueError(
                "Only letters, digits, spaces, punctuation, and accented "
                "Latin characters are allowed in names"
            )
    return v


# NameStr: printable ASCII + precomposed Latin accents, Zalgo/emoji/etc. rejected.
# Use for folder names and material titles.
NameStr = Annotated[str, BeforeValidator(_validate_name_value)]


def strip_null_chars(v: Any) -> Any:
    """Recursively strip null bytes from strings/lists/dicts (Postgres compat).

    Also strips the full set of dangerous Unicode chars – kept as a
    convenience for recursive JSON-payload validators.
    """
    if isinstance(v, str):
        return clean_text(v)
    if isinstance(v, list):
        return [strip_null_chars(i) for i in v]
    if isinstance(v, dict):
        return {k: strip_null_chars(val) for k, val in v.items()}
    return v


def sanitize_json_payload(v: Any) -> Any:
    """Validator-compatible wrapper for strip_null_chars."""
    return strip_null_chars(v)
