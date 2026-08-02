"""Unit tests for core/common submodules and re-export shims."""

from app.core.common.constants import MAGIC_HEADER_SIZE, PRIVILEGED_ROLES
from app.core.common.exceptions import (
    AppError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from app.core.common.natural_sorting import natural_sort_key
from app.core.common.upload_errors import UploadErrorCode


def test_constants():
    assert MAGIC_HEADER_SIZE == 2048
    assert "moderator" in PRIVILEGED_ROLES
    assert "bureau" in PRIVILEGED_ROLES
    assert "vieux" in PRIVILEGED_ROLES
    assert isinstance(PRIVILEGED_ROLES, frozenset)


def test_exceptions_hierarchy():
    err = AppError(status_code=400, detail="Custom detail", code="ERR_CUSTOM")
    assert err.status_code == 400
    assert err.detail == "Custom detail"
    assert err.code == "ERR_CUSTOM"
    assert str(err) == "Custom detail"

    not_found = NotFoundError()
    assert not_found.status_code == 404
    assert not_found.detail == "Resource not found"

    bad_req = BadRequestError("Invalid payload", code="ERR_BAD")
    assert bad_req.status_code == 400
    assert bad_req.code == "ERR_BAD"

    assert UnauthorizedError().status_code == 401
    assert ForbiddenError().status_code == 403
    assert ConflictError().status_code == 409
    assert RateLimitError().status_code == 429
    assert ServiceUnavailableError().status_code == 503


def test_natural_sorting():
    # None or empty string
    assert natural_sort_key(None) == ()
    assert natural_sort_key("") == ()

    # Pure text vs numbers
    key_doc1 = natural_sort_key("Document 2")
    key_doc10 = natural_sort_key("Document 10")
    assert key_doc1 < key_doc10

    # Sorting list of strings
    files = ["file10.txt", "file2.txt", "file1.txt"]
    files_sorted = sorted(files, key=natural_sort_key)
    assert files_sorted == ["file1.txt", "file2.txt", "file10.txt"]

    # Accents handling
    assert natural_sort_key("Éléphant 1") == natural_sort_key("elephant 1")

    # Non-Latin characters preservation
    key_cyrillic = natural_sort_key("Документ 1")
    assert len(key_cyrillic) > 0


def test_upload_error_codes():
    assert UploadErrorCode.FILE_TOO_LARGE == "ERR_FILE_TOO_LARGE"
    assert UploadErrorCode.TUS_CONCURRENCY_LIMIT == "ERR_TUS_CONCURRENCY_LIMIT"
    assert str(UploadErrorCode.MIME_MISMATCH) == "ERR_MIME_MISMATCH"
