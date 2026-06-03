"""Tests for app.core.sanitization — NameStr validator and sanitize_json_payload.

clean_text and strip_null_chars are already covered in test_input_validation.py.
This file covers the gaps:
- NameStr: printable ASCII + precomposed Latin accents only; emoji, Arabic,
  Chinese, Greek, and other non-allowlist chars are rejected.
- SanitizedStr: strips dangerous chars but allows emoji (wider than NameStr).
- sanitize_json_payload: delegates to strip_null_chars on nested structures.
"""

import pytest
from pydantic import BaseModel, ValidationError

from app.core.sanitization import NameStr, SanitizedStr, sanitize_json_payload


class _NameModel(BaseModel):
    name: NameStr


class _SanitizedModel(BaseModel):
    text: SanitizedStr


def _validate_name(name: str) -> str:
    return _NameModel(name=name).name


def _should_reject_name(name: str) -> None:
    with pytest.raises(ValidationError):
        _NameModel(name=name)


# ── NameStr: valid inputs ────────────────────────────────────────────────────


def test_name_str_plain_ascii_passes() -> None:
    assert _validate_name("Hello World 123!") == "Hello World 123!"


def test_name_str_punctuation_passes() -> None:
    assert (
        _validate_name("Lecture 1 - Introduction (v2).pdf") == "Lecture 1 - Introduction (v2).pdf"
    )


def test_name_str_french_lowercase_accents_pass() -> None:
    for char in "éàèêëùûüôîïçœæ":
        assert _validate_name(char) == char


def test_name_str_french_uppercase_accents_pass() -> None:
    for char in "ÉÀÈÊËÙÛÜÔÎÏÇŒÆ":
        assert _validate_name(char) == char


def test_name_str_latin_extended_a_passes() -> None:
    # U+00F8–U+017F region (ø, ā, etc.)
    assert _validate_name("ø") == "ø"


def test_name_str_realistic_course_name() -> None:
    # Note: em dash (U+2014) is outside the ASCII+Latin allowlist; use hyphen-minus instead.
    name = "Mathematiques - Algebre lineaire (2A)"
    assert _validate_name(name) == name


# ── NameStr: rejected inputs ─────────────────────────────────────────────────


def test_name_str_emoji_rejected() -> None:
    _should_reject_name("Hello 🎉")


def test_name_str_arabic_rejected() -> None:
    _should_reject_name("مرحبا")


def test_name_str_chinese_rejected() -> None:
    _should_reject_name("你好")


def test_name_str_greek_letters_rejected() -> None:
    # Greek: U+0370–U+03FF — outside the Latin allowlist
    _should_reject_name("αβγ")


def test_name_str_cyrillic_rejected() -> None:
    _should_reject_name("Привет")


def test_name_str_combining_mark_then_bad_char_rejected() -> None:
    # Combining grave accent is stripped, but Chinese chars that follow are still rejected
    _should_reject_name("̀你好")


# ── NameStr: Zalgo strip-then-pass ────────────────────────────────────────────


def test_name_str_zalgo_stripped_leaving_valid_ascii() -> None:
    # Zalgo combining marks (U+0300+) are stripped by clean_text; the
    # underlying ASCII chars remain valid and pass the NameStr check.
    zalgo = "h̀él̂l̃o"
    result = _validate_name(zalgo)
    assert result == "hello"


def test_name_str_c1_controls_stripped_leaving_valid_text() -> None:
    # C1 control chars (U+0080–U+009F) are stripped before the allowlist check.
    result = _validate_name("hello\x80world")
    assert result == "helloworld"


# ── SanitizedStr: wider than NameStr ─────────────────────────────────────────


def test_sanitized_str_allows_emoji() -> None:
    # SanitizedStr strips only dangerous/invisible chars, not emoji (U+1F300+).
    result = _SanitizedModel(text="Hello 🎉").text
    assert "🎉" in result


def test_sanitized_str_strips_bidi_override() -> None:
    result = _SanitizedModel(text="safe‮revesed").text
    assert "‮" not in result


# ── sanitize_json_payload ─────────────────────────────────────────────────────


def test_sanitize_json_payload_strips_null_from_nested_dict() -> None:
    result = sanitize_json_payload({"key": "val\x00ue", "list": ["a\x07b"]})
    assert "\x00" not in result["key"]
    assert "\x07" not in result["list"][0]


def test_sanitize_json_payload_deeply_nested() -> None:
    result = sanitize_json_payload({"a": {"b": ["c‮"]}})
    assert "‮" not in result["a"]["b"][0]


def test_sanitize_json_payload_passes_through_non_string() -> None:
    assert sanitize_json_payload(42) == 42
    assert sanitize_json_payload(None) is None
    assert sanitize_json_payload(3.14) == pytest.approx(3.14)
