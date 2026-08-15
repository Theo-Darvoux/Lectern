"""Security regressions for EuroOffice document-server file authentication."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from starlette.requests import Request

from app.config import settings
from app.core.common.exceptions import UnauthorizedError
from app.routers import eurooffice
from app.routers.eurooffice import (
    _EXT_TO_DOCTYPE,
    _create_file_grant,
    _decode_file_grant,
    _verify_document_server_token,
    get_eurooffice_config,
)

_ALGORITHM = "HS256"


def _outgoing_token(url: str) -> str:
    return jwt.encode(
        {"payload": {"url": url}},
        settings.eurooffice_jwt_secret,
        algorithm=_ALGORITHM,
    )


def test_verify_document_server_token_valid() -> None:
    url = "http://api:8000/api/eurooffice/file/mat-123"
    assert _verify_document_server_token(_outgoing_token(url), url)


def test_verify_document_server_token_wrong_url() -> None:
    expected = "http://api:8000/api/eurooffice/file/mat-123"
    other = "http://api:8000/api/eurooffice/file/mat-456"
    assert not _verify_document_server_token(_outgoing_token(other), expected)


def test_browser_config_signature_cannot_be_replayed_as_file_bearer() -> None:
    url = "http://api:8000/api/eurooffice/file/mat-123"
    browser_config_token = jwt.encode(
        {"documentType": "word", "document": {"url": url}},
        settings.eurooffice_jwt_secret,
        algorithm=_ALGORITHM,
    )
    assert not _verify_document_server_token(browser_config_token, url)


def test_verify_document_server_token_wrong_secret() -> None:
    url = "http://api:8000/api/eurooffice/file/mat-123"
    token = jwt.encode(
        {"payload": {"url": url}},
        "wrong-secret-key-that-is-at-least-32-bytes-long",
        algorithm=_ALGORITHM,
    )
    assert not _verify_document_server_token(token, url)


@pytest.mark.asyncio
async def test_eurooffice_browser_config_contains_no_replayable_file_query_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    material_id = uuid.uuid4()
    monkeypatch.setattr(
        eurooffice,
        "get_material_with_version",
        AsyncMock(
            return_value={
                "current_version_info": {
                    "file_key": "cas/object",
                    "file_name": "document.docx",
                    "version_number": 1,
                }
            }
        ),
    )
    user = SimpleNamespace(id=uuid.uuid4(), display_name="Viewer", email="viewer@example.com")

    config = await get_eurooffice_config(material_id, user, AsyncMock())
    file_url = config["document"]["url"]

    assert "?token=" not in file_url
    assert "token=" not in file_url
    parsed = urlparse(file_url)
    assert parsed.path.endswith(f"/api/eurooffice/file/{material_id}")
    grants = parse_qs(parsed.query).get("grant", [])
    assert len(grants) == 1
    assert _decode_file_grant(grants[0], str(material_id)) == 1


def test_document_server_secret_cannot_forge_unissued_material_grant() -> None:
    material_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    forged = jwt.encode(
        {
            "sub": material_id,
            "ver": 1,
            "iat": now,
            "exp": now + timedelta(minutes=1),
            "iss": "lectern-api",
            "aud": "eurooffice-file",
        },
        settings.eurooffice_jwt_secret,
        algorithm=_ALGORITHM,
    )
    assert _decode_file_grant(forged, material_id) is None


def test_eurooffice_file_grant_expiry_is_mandatory() -> None:
    material_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    no_expiry = jwt.encode(
        {
            "sub": material_id,
            "ver": 1,
            "iat": now,
            "iss": "lectern-api",
            "aud": "eurooffice-file",
        },
        settings.eurooffice_file_token_secret,
        algorithm=_ALGORITHM,
    )
    assert _decode_file_grant(no_expiry, material_id) is None

    expired = jwt.encode(
        {
            "sub": material_id,
            "ver": 1,
            "iat": now - timedelta(minutes=2),
            "exp": now - timedelta(minutes=1),
            "iss": "lectern-api",
            "aud": "eurooffice-file",
        },
        settings.eurooffice_file_token_secret,
        algorithm=_ALGORITHM,
    )
    assert _decode_file_grant(expired, material_id) is None


def test_file_grant_is_material_and_version_scoped() -> None:
    material_id = str(uuid.uuid4())
    grant = _create_file_grant(material_id, 7)
    assert _decode_file_grant(grant, material_id) == 7
    assert _decode_file_grant(grant, str(uuid.uuid4())) is None


@pytest.mark.asyncio
async def test_eurooffice_file_route_ignores_legacy_query_bearer() -> None:
    material_id = uuid.uuid4()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/api/eurooffice/file/{material_id}",
            "query_string": b"token=legacy-browser-bearer",
            "headers": [],
        }
    )
    with pytest.raises(UnauthorizedError):
        await eurooffice.serve_file_to_eurooffice(request, material_id, AsyncMock())


@pytest.mark.parametrize(
    "ext, expected_doc_type",
    [
        ("docx", "word"),
        ("doc", "word"),
        ("odt", "word"),
        ("xlsx", "cell"),
        ("xls", "cell"),
        ("ods", "cell"),
        ("pptx", "slide"),
        ("ppt", "slide"),
        ("pdf", "pdf"),
    ],
)
def test_ext_to_doctype_known_extensions(ext: str, expected_doc_type: str) -> None:
    assert _EXT_TO_DOCTYPE[ext] == expected_doc_type
