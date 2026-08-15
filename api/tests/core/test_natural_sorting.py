"""Tests for the natural-order sort key in app.core.natural_sorting.

natural_sort_key powers folder/material ordering so that numbered names sort by
value ("Chapitre 2" < "Chapitre 10") rather than lexicographically. It mirrors
the frontend's compareNatural comparator.
"""

from app.core.common.natural_sorting import natural_sort_key


def test_numbered_names_sort_by_value() -> None:
    items = ["Chapitre 10", "Chapitre 2", "Chapitre 1"]
    assert sorted(items, key=natural_sort_key) == [
        "Chapitre 1",
        "Chapitre 2",
        "Chapitre 10",
    ]


def test_no_separator_between_text_and_number() -> None:
    items = ["TD11", "TD2", "TD1"]
    assert sorted(items, key=natural_sort_key) == ["TD1", "TD2", "TD11"]


def test_multiple_numeric_runs() -> None:
    items = ["v1.10", "v1.2", "v1.9"]
    assert sorted(items, key=natural_sort_key) == ["v1.2", "v1.9", "v1.10"]


def test_case_and_accent_insensitive_ordering() -> None:
    # "Élève" folds to "eleve"; ordering ignores case and diacritics.
    items = ["annexe", "Élève", "Chapitre 1"]
    assert sorted(items, key=natural_sort_key) == ["annexe", "Chapitre 1", "Élève"]


def test_empty_and_none_sort_first() -> None:
    items = ["b", "", "a"]
    assert sorted(items, key=natural_sort_key) == ["", "a", "b"]
    assert natural_sort_key(None) == ()


def test_pure_number_compares_numerically() -> None:
    items = ["100", "2", "20"]
    assert sorted(items, key=natural_sort_key) == ["2", "20", "100"]


def test_cached_sort_key_is_immutable() -> None:
    """A caller cannot mutate the value retained by the shared cache."""
    key = natural_sort_key("Chapter 12")

    assert isinstance(key, tuple)
