import re
import unicodedata
from typing import Annotated, Any

from pydantic import BeforeValidator

# Strips characters that are invisible, dangerous, or otherwise not useful in user-supplied text.
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
_INVALID_NAME_CHAR_RE = re.compile(r"[^\x20-\x7e\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u017f]")


def clean_text(v: str) -> str:
    """Strip dangerous Unicode, then normalize the remaining text."""
    cleaned = _DANGEROUS_CHARS_RE.sub("", v)
    return unicodedata.normalize("NFC", cleaned)


def _sanitize_value(v: Any) -> Any:
    if isinstance(v, str):
        return clean_text(v)
    return v


# ---------------------------------------------------------------------------
# Public types / helpers
# ---------------------------------------------------------------------------

# SanitizedStr: strips all invisible/dangerous chars (including Zalgo combining marks).
SanitizedStr = Annotated[str, BeforeValidator(_sanitize_value)]


def _validate_name_value(v: Any) -> Any:
    """Reject strings containing characters outside the name allowlist."""
    if isinstance(v, str):
        v = clean_text(v)
        if _INVALID_NAME_CHAR_RE.search(v):
            raise ValueError(
                "Only letters, digits, spaces, punctuation, and accented "
                "Latin characters are allowed in names"
            )
    return v


# NameStr: printable ASCII + precomposed Latin accents, Zalgo/emoji/etc. rejected.
NameStr = Annotated[str, BeforeValidator(_validate_name_value)]


_MAX_JSON_DEPTH = 20
_MAX_JSON_NODES = 2_000
_MAX_JSON_CONTAINER_ITEMS = 200


def _consume_json_budget(budget: list[int]) -> None:
    budget[0] += 1
    if budget[0] > _MAX_JSON_NODES:
        raise ValueError(f"JSON payload exceeds node limit ({_MAX_JSON_NODES})")


def _sanitize_json_value(v: Any, *, depth: int, budget: list[int]) -> Any:
    if depth > _MAX_JSON_DEPTH:
        raise ValueError(f"JSON payload exceeds depth limit ({_MAX_JSON_DEPTH})")

    _consume_json_budget(budget)

    if isinstance(v, str):
        return clean_text(v)

    if isinstance(v, list):
        if len(v) > _MAX_JSON_CONTAINER_ITEMS:
            raise ValueError(
                f"JSON list exceeds item limit ({_MAX_JSON_CONTAINER_ITEMS})"
            )
        return [
            _sanitize_json_value(item, depth=depth + 1, budget=budget)
            for item in v
        ]

    if isinstance(v, dict):
        if len(v) > _MAX_JSON_CONTAINER_ITEMS:
            raise ValueError(
                f"JSON object exceeds key limit ({_MAX_JSON_CONTAINER_ITEMS})"
            )

        cleaned: dict[Any, Any] = {}
        for key, value in v.items():
            _consume_json_budget(budget)
            cleaned_key = clean_text(key) if isinstance(key, str) else key
            if cleaned_key in cleaned:
                raise ValueError("JSON object contains colliding keys after sanitization")
            cleaned[cleaned_key] = _sanitize_json_value(
                value,
                depth=depth + 1,
                budget=budget,
            )
        return cleaned

    return v


def strip_null_chars(v: Any) -> Any:
    """Bound and recursively sanitize JSON-compatible values and mapping keys."""
    return _sanitize_json_value(v, depth=0, budget=[0])


def sanitize_json_payload(v: Any) -> Any:
    """Validator-compatible wrapper for strip_null_chars."""
    return strip_null_chars(v)
