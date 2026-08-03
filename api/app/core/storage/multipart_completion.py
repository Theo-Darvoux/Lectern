"""Crash-consistent verification for S3 multipart completion."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from app.core.storage.facade import (
    complete_multipart_upload,
    delete_object,
    get_object_info,
)

_MISSING_OBJECT_CODES = {"404", "NoSuchKey", "NotFound"}
_DEFINITIVE_COMPLETION_CODES = {
    "AccessDenied",
    "EntityTooSmall",
    "InvalidAccessKeyId",
    "InvalidPart",
    "InvalidPartOrder",
    "MalformedXML",
    "NoSuchBucket",
    "NoSuchUpload",
    "SignatureDoesNotMatch",
}
_AMBIGUOUS_COMPLETION_CODES = {
    "InternalError",
    "RequestTimeout",
    "RequestTimeoutException",
    "ServiceUnavailable",
    "SlowDown",
    "Throttling",
    "ThrottlingException",
}


@dataclass(frozen=True)
class MultipartCompletionResult:
    """Authoritative result of a multipart completion attempt."""

    size: int
    recovered_after_error: bool = False


class MultipartCompletionError(RuntimeError):
    """Multipart completion failed, with explicit retry semantics."""

    def __init__(self, detail: str, *, retryable: bool) -> None:
        super().__init__(detail)
        self.detail = detail
        self.retryable = retryable


class _ObjectProbeState(StrEnum):
    EXISTS = "exists"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class _ObjectProbe:
    state: _ObjectProbeState
    size: int | None = None


def _client_error_details(exc: ClientError) -> tuple[str, int]:
    response: Any = exc.response
    code = str(response.get("Error", {}).get("Code", ""))
    status = int(response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 0)
    return code, status


def _is_ambiguous_completion_error(exc: Exception) -> bool:
    """Return whether the server may have committed despite the client error."""
    if isinstance(
        exc,
        (
            asyncio.TimeoutError,
            ConnectTimeoutError,
            ConnectionClosedError,
            EndpointConnectionError,
            ReadTimeoutError,
        ),
    ):
        return True
    if isinstance(exc, ClientError):
        code, status = _client_error_details(exc)
        if code in _DEFINITIVE_COMPLETION_CODES:
            return False
        if code in _AMBIGUOUS_COMPLETION_CODES or status >= 500:
            return True
        return not 400 <= status < 500
    # Generic transport-layer OSErrors are potentially ambiguous. Application
    # validation exceptions are not classified as retryable here.
    return isinstance(exc, OSError)


async def _probe_object(file_key: str) -> _ObjectProbe:
    """Distinguish an absent object from a temporarily unavailable HEAD call."""
    try:
        info = await get_object_info(file_key)
    except ClientError as exc:
        code, status = _client_error_details(exc)
        if code in _MISSING_OBJECT_CODES or status == 404:
            return _ObjectProbe(_ObjectProbeState.MISSING)
        return _ObjectProbe(_ObjectProbeState.UNAVAILABLE)
    except Exception:
        return _ObjectProbe(_ObjectProbeState.UNAVAILABLE)

    try:
        size = int(info["size"])
    except (KeyError, TypeError, ValueError):
        return _ObjectProbe(_ObjectProbeState.UNAVAILABLE)
    return _ObjectProbe(_ObjectProbeState.EXISTS, size=size)


async def _delete_mismatched_object(file_key: str, actual_size: int, expected_size: int) -> None:
    try:
        await delete_object(file_key)
    except Exception as exc:
        raise MultipartCompletionError(
            (
                f"Completed object {file_key!r} has size {actual_size}, expected "
                f"{expected_size}, and immediate cleanup failed"
            ),
            retryable=True,
        ) from exc


async def complete_multipart_verified(
    file_key: str,
    s3_upload_id: str,
    parts: list[dict[str, int | str]],
    *,
    expected_size: int,
) -> MultipartCompletionResult:
    """Complete multipart upload and reconcile ambiguous outcomes with ``HEAD``.

    The caller must persist the exact manifest before invoking this function.
    A retry may safely call it again: if the first completion committed but its
    response was lost, the second attempt can receive ``NoSuchUpload`` and is
    recovered by the authoritative object-size check.
    """
    if expected_size < 1:
        raise ValueError("expected_size must be positive")
    if not parts:
        raise ValueError("parts must not be empty")

    completion_error: Exception | None = None
    try:
        await complete_multipart_upload(file_key, s3_upload_id, parts)
    except Exception as exc:
        completion_error = exc

    probe = await _probe_object(file_key)
    if probe.state is _ObjectProbeState.EXISTS:
        assert probe.size is not None
        if probe.size != expected_size:
            await _delete_mismatched_object(file_key, probe.size, expected_size)
            raise MultipartCompletionError(
                f"Completed object size {probe.size} does not match expected size {expected_size}",
                retryable=False,
            ) from completion_error
        return MultipartCompletionResult(
            size=probe.size,
            recovered_after_error=completion_error is not None,
        )

    if probe.state is _ObjectProbeState.UNAVAILABLE:
        raise MultipartCompletionError(
            (
                "Multipart completion outcome could not be verified because "
                "object storage is unavailable"
            ),
            retryable=True,
        ) from completion_error

    if completion_error is None:
        # S3 acknowledged completion, but an immediately authoritative HEAD says
        # the object is absent. Preserve the manifest and retry reconciliation.
        raise MultipartCompletionError(
            "Multipart completion returned success but the completed object is absent",
            retryable=True,
        )

    raise MultipartCompletionError(
        "Multipart completion failed and no completed object exists",
        retryable=_is_ambiguous_completion_error(completion_error),
    ) from completion_error
