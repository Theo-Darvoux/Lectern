import pytest

import app.core.sanitization as sanitization


def test_clean_text_preserves_existing_combining_mark_policy() -> None:
    assert sanitization.clean_text("h\u0300e\u0301l\u0302l\u0303o") == "hello"


def test_clean_text_preserves_precomposed_accent() -> None:
    assert sanitization.clean_text("Café") == "Café"


def test_json_sanitizer_cleans_mapping_keys() -> None:
    assert sanitization.sanitize_json_payload({"nul\x00key": "value"}) == {"nulkey": "value"}


def test_json_sanitizer_rejects_key_collisions() -> None:
    with pytest.raises(ValueError, match="colliding keys"):
        sanitization.sanitize_json_payload(
            {
                "same": 1,
                "sa\x00me": 2,
            }
        )


def test_json_sanitizer_rejects_excessive_depth(monkeypatch) -> None:
    monkeypatch.setattr(sanitization, "_MAX_JSON_DEPTH", 3)
    payload = {"a": {"b": {"c": {"d": "too deep"}}}}

    with pytest.raises(ValueError, match="depth limit"):
        sanitization.sanitize_json_payload(payload)


def test_json_sanitizer_rejects_excessive_nodes(monkeypatch) -> None:
    monkeypatch.setattr(sanitization, "_MAX_JSON_NODES", 5)

    with pytest.raises(ValueError, match="node limit"):
        sanitization.sanitize_json_payload({"items": [1, 2, 3, 4]})
