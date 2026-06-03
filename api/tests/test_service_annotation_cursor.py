"""
Pure-function unit tests for annotation cursor encoding/decoding.
No DB required — these exercise only the base64 cursor helpers.
"""

import base64
import uuid
from datetime import UTC, datetime

import pytest

from app.core.exceptions import BadRequestError
from app.services.annotation import _decode_cursor, _encode_cursor


def test_cursor_roundtrip() -> None:
    ts = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    uid = uuid.uuid4()

    cursor = _encode_cursor(ts, uid)
    decoded_ts, decoded_id = _decode_cursor(cursor)

    assert decoded_id == uid
    assert decoded_ts == ts


def test_cursor_is_url_safe_base64() -> None:
    ts = datetime(2024, 1, 1, tzinfo=UTC)
    uid = uuid.uuid4()
    cursor = _encode_cursor(ts, uid)
    # urlsafe_b64encode replaces + and / with - and _ but keeps = padding
    assert "+" not in cursor
    assert "/" not in cursor


def test_decode_cursor_raises_on_invalid_string() -> None:
    with pytest.raises(BadRequestError):
        _decode_cursor("not-a-valid-cursor")


def test_decode_cursor_raises_on_empty_string() -> None:
    with pytest.raises(BadRequestError):
        _decode_cursor("")


def test_decode_cursor_raises_on_missing_pipe_separator() -> None:
    payload = base64.urlsafe_b64encode(b"nodivider").decode().rstrip("=")
    with pytest.raises(BadRequestError):
        _decode_cursor(payload)


def test_decode_cursor_raises_on_invalid_uuid_in_payload() -> None:
    raw = "2024-01-01T00:00:00+00:00|not-a-uuid"
    payload = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    with pytest.raises(BadRequestError):
        _decode_cursor(payload)
