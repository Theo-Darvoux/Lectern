"""Natural-order sorting helpers.

SQL has no portable natural sort, so list endpoints fetch rows ordered by their
primary criterion and apply :func:`natural_sort_key` in Python. This keeps
"Chapitre 2" before "Chapitre 10" instead of lexicographic "10" < "2", matching
the frontend's `compareNatural` comparator.
"""

import re
import unicodedata

_NUM_CHUNK = re.compile(r"(\d+)")


def natural_sort_key(value: str | None) -> list[tuple[int, int, str]]:
    """Return a sort key splitting `value` into text/number runs.

    Numeric runs compare by integer value, text runs case- and accent-folded so
    ordering is stable regardless of casing/diacritics. Each chunk is wrapped as
    a uniform tuple so number and text chunks never compare against each other.
    """
    if not value:
        return []
    folded = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").casefold()
    )
    key: list[tuple[int, int, str]] = []
    for chunk in _NUM_CHUNK.split(folded):
        if not chunk:
            continue
        if chunk.isdigit():
            key.append((0, int(chunk), ""))
        else:
            key.append((1, 0, chunk))
    return key
