from pathlib import Path

import pytest
from fastapi import Response
from jwt import InvalidTokenError

from app.core.security.security import (
    BROWSER_READ_COOKIE,
    create_browser_read_token,
    decode_token,
)
from app.routers.auth import _set_browser_read_cookie

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_browser_read_token_is_not_a_general_access_token() -> None:
    token = create_browser_read_token("user-id", expire_days=1)
    payload = decode_token(token, expected_type="browser_read")
    assert payload["type"] == "browser_read"
    assert payload["sub"] == "user-id"

    with pytest.raises(InvalidTokenError):
        decode_token(token, expected_type="access")


def test_browser_read_cookie_is_httponly_secure_strict_and_scoped() -> None:
    response = Response()
    _set_browser_read_cookie(response, "user-id", expire_days=1)
    header = response.headers["set-cookie"]
    assert f"{BROWSER_READ_COOKIE}=" in header
    assert "HttpOnly" in header
    assert "Secure" in header
    assert "SameSite=strict" in header
    assert "Path=/api/" in header


def test_read_routes_have_no_query_bearer_fallback() -> None:
    auth = (REPO_ROOT / "api/app/dependencies/auth.py").read_text(encoding="utf-8")
    materials = (REPO_ROOT / "api/app/routers/materials.py").read_text(encoding="utf-8")
    directories = (REPO_ROOT / "api/app/routers/directories.py").read_text(encoding="utf-8")

    assert "QueryTokenUser" not in auth
    assert "token: Annotated[str | None, Query()]" not in auth
    assert "token: Annotated[str | None, Query()]" not in materials
    assert "token: Annotated[str | None, Query()]" not in directories


def test_frontend_never_places_access_bearer_in_browser_urls() -> None:
    web_root = REPO_ROOT / "web/src"
    for path in web_root.rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        source = path.read_text(encoding="utf-8")
        assert "/sse?token=${encodeURIComponent(token)}" not in source, path
        assert "/file?token=${encodeURIComponent(token)}" not in source, path
        assert "?token=${encodeURIComponent(token)}" not in source, path


def test_nginx_access_log_omits_request_arguments_and_referer() -> None:
    nginx = (REPO_ROOT / "infra/nginx/nginx.conf.template").read_text(encoding="utf-8")
    assert "log_format request_without_args" in nginx
    log_format = nginx.split("log_format request_without_args", 1)[1].split(";", 1)[0]
    assert "$uri" in log_format
    assert "$request " not in log_format
    assert "$request_uri" not in log_format
    assert "$http_referer" not in log_format


def test_directory_zip_generator_has_no_archive_or_object_sized_buffer() -> None:
    source = (REPO_ROOT / "api/app/routers/directories.py").read_text(encoding="utf-8")
    function = source.split("async def _generate_zip", 1)[1].split(
        '@router.get("/root/download-chunks"', 1
    )[0]
    assert "io.BytesIO" not in function
    assert "data = bytearray()" not in function
    assert "member.write(chunk)" in function
