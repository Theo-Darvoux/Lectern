"""Natural sorting helper function for alphanumeric string sorting."""

import re
import unicodedata
from functools import lru_cache

__all__ = ["natural_sort_key", "content_status_sort_key"]

_STATUS_SORT_ORDER: dict[str, int] = {
    "important": 0,
    "current": 1,
    "deprecated": 2,
    "archived": 3,
}


def content_status_sort_key(status: str | None) -> int:
    """Return numeric sort rank for material status: important (0) < current (1) < deprecated (2) < archived (3)."""
    return _STATUS_SORT_ORDER.get(str(status).lower() if status else "current", 1)


# Regular expression to match consecutive digits.
_NUM_CHUNK = re.compile(r"(\d+)")


def _strip_accents(s: str) -> str:
    """Decompose characters (NFKD) and drop combining diacritical marks."""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if unicodedata.category(c) != "Mn")


@lru_cache(maxsize=4096)
def natural_sort_key(value: str | None) -> tuple[tuple[int, int, str], ...]:
    """Generate a sort key for natural alphanumeric order.

    Numbers are sorted numerically, text chunks case-insensitively with accents stripped.
    Returns an empty tuple if value is None or empty. The immutable result is
    safe to share through the process-wide cache.
    """
    if not value:
        return ()

    # Remove accents and casefold for consistent comparison across scripts
    folded = _strip_accents(value).casefold()

    key: list[tuple[int, int, str]] = []
    for chunk in _NUM_CHUNK.split(folded):
        if not chunk:
            continue
        if chunk.isdigit():
            key.append((0, int(chunk), ""))
        else:
            key.append((1, 0, chunk))
    return tuple(key)
