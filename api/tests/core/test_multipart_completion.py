from unittest.mock import AsyncMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from app.core.storage.multipart_completion import (
    MultipartCompletionError,
    complete_multipart_verified,
)


@pytest.mark.asyncio
async def test_completion_success_is_verified() -> None:
    with (
        patch(
            "app.core.storage.multipart_completion.complete_multipart_upload",
            new_callable=AsyncMock,
        ) as complete,
        patch(
            "app.core.storage.multipart_completion.get_object_info",
            new_callable=AsyncMock,
            return_value={"size": 12},
        ),
    ):
        result = await complete_multipart_verified(
            "quarantine/key", "upload-id", [{"PartNumber": 1, "ETag": "etag"}], expected_size=12
        )

    assert result.size == 12
    assert result.recovered_after_error is False
    complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_ambiguous_error_recovers_from_authoritative_object() -> None:
    with (
        patch(
            "app.core.storage.multipart_completion.complete_multipart_upload",
            new_callable=AsyncMock,
            side_effect=EndpointConnectionError(endpoint_url="http://s3"),
        ),
        patch(
            "app.core.storage.multipart_completion.get_object_info",
            new_callable=AsyncMock,
            return_value={"size": 12},
        ),
    ):
        result = await complete_multipart_verified(
            "quarantine/key", "upload-id", [{"PartNumber": 1, "ETag": "etag"}], expected_size=12
        )

    assert result.recovered_after_error is True


@pytest.mark.asyncio
async def test_no_such_upload_without_object_is_definitive() -> None:
    error = ClientError(
        {
            "Error": {"Code": "NoSuchUpload", "Message": "missing"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        },
        "CompleteMultipartUpload",
    )
    with (
        patch(
            "app.core.storage.multipart_completion.complete_multipart_upload",
            new_callable=AsyncMock,
            side_effect=error,
        ),
        patch(
            "app.core.storage.multipart_completion.get_object_info",
            new_callable=AsyncMock,
            side_effect=ClientError(
                {
                    "Error": {"Code": "NoSuchKey", "Message": "missing"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadObject",
            ),
        ),
        pytest.raises(MultipartCompletionError) as raised,
    ):
        await complete_multipart_verified(
            "quarantine/key", "upload-id", [{"PartNumber": 1, "ETag": "etag"}], expected_size=12
        )

    assert raised.value.retryable is False


@pytest.mark.asyncio
async def test_size_mismatch_deletes_completed_object_immediately() -> None:
    with (
        patch(
            "app.core.storage.multipart_completion.complete_multipart_upload",
            new_callable=AsyncMock,
        ),
        patch(
            "app.core.storage.multipart_completion.get_object_info",
            new_callable=AsyncMock,
            return_value={"size": 99},
        ),
        patch(
            "app.core.storage.multipart_completion.delete_object",
            new_callable=AsyncMock,
        ) as delete,
        pytest.raises(MultipartCompletionError) as raised,
    ):
        await complete_multipart_verified(
            "quarantine/key", "upload-id", [{"PartNumber": 1, "ETag": "etag"}], expected_size=12
        )

    assert raised.value.retryable is False
    delete.assert_awaited_once_with("quarantine/key")


@pytest.mark.asyncio
async def test_cleanup_failure_remains_retryable() -> None:
    with (
        patch(
            "app.core.storage.multipart_completion.complete_multipart_upload",
            new_callable=AsyncMock,
        ),
        patch(
            "app.core.storage.multipart_completion.get_object_info",
            new_callable=AsyncMock,
            return_value={"size": 99},
        ),
        patch(
            "app.core.storage.multipart_completion.delete_object",
            new_callable=AsyncMock,
            side_effect=RuntimeError("storage unavailable"),
        ),
        pytest.raises(MultipartCompletionError) as raised,
    ):
        await complete_multipart_verified(
            "quarantine/key", "upload-id", [{"PartNumber": 1, "ETag": "etag"}], expected_size=12
        )

    assert raised.value.retryable is True


@pytest.mark.asyncio
async def test_no_such_upload_with_unavailable_head_is_retryable() -> None:
    error = ClientError(
        {
            "Error": {"Code": "NoSuchUpload", "Message": "missing"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        },
        "CompleteMultipartUpload",
    )
    with (
        patch(
            "app.core.storage.multipart_completion.complete_multipart_upload",
            new_callable=AsyncMock,
            side_effect=error,
        ),
        patch(
            "app.core.storage.multipart_completion.get_object_info",
            new_callable=AsyncMock,
            side_effect=EndpointConnectionError(endpoint_url="http://s3"),
        ),
        pytest.raises(MultipartCompletionError) as raised,
    ):
        await complete_multipart_verified(
            "quarantine/key",
            "upload-id",
            [{"PartNumber": 1, "ETag": "etag"}],
            expected_size=12,
        )

    assert raised.value.retryable is True


@pytest.mark.asyncio
async def test_success_ack_with_authoritatively_missing_object_is_retryable() -> None:
    missing = ClientError(
        {
            "Error": {"Code": "NoSuchKey", "Message": "missing"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        },
        "HeadObject",
    )
    with (
        patch(
            "app.core.storage.multipart_completion.complete_multipart_upload",
            new_callable=AsyncMock,
        ),
        patch(
            "app.core.storage.multipart_completion.get_object_info",
            new_callable=AsyncMock,
            side_effect=missing,
        ),
        pytest.raises(MultipartCompletionError) as raised,
    ):
        await complete_multipart_verified(
            "quarantine/key",
            "upload-id",
            [{"PartNumber": 1, "ETag": "etag"}],
            expected_size=12,
        )

    assert raised.value.retryable is True


@pytest.mark.asyncio
async def test_unknown_completion_error_without_status_is_retryable() -> None:
    error = ClientError(
        {
            "Error": {"Code": "VendorSpecificFailure", "Message": "unknown"},
            "ResponseMetadata": {},
        },
        "CompleteMultipartUpload",
    )
    missing = ClientError(
        {
            "Error": {"Code": "NoSuchKey", "Message": "missing"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        },
        "HeadObject",
    )
    with (
        patch(
            "app.core.storage.multipart_completion.complete_multipart_upload",
            new_callable=AsyncMock,
            side_effect=error,
        ),
        patch(
            "app.core.storage.multipart_completion.get_object_info",
            new_callable=AsyncMock,
            side_effect=missing,
        ),
        pytest.raises(MultipartCompletionError) as raised,
    ):
        await complete_multipart_verified(
            "quarantine/key",
            "upload-id",
            [{"PartNumber": 1, "ETag": "etag"}],
            expected_size=12,
        )

    assert raised.value.retryable is True


@pytest.mark.asyncio
async def test_cancellation_is_not_converted_to_terminal_failure() -> None:
    import asyncio

    with (
        patch(
            "app.core.storage.multipart_completion.complete_multipart_upload",
            new_callable=AsyncMock,
            side_effect=asyncio.CancelledError,
        ),
        patch(
            "app.core.storage.multipart_completion.get_object_info",
            new_callable=AsyncMock,
        ) as probe,
        pytest.raises(asyncio.CancelledError),
    ):
        await complete_multipart_verified(
            "quarantine/key",
            "upload-id",
            [{"PartNumber": 1, "ETag": "etag"}],
            expected_size=12,
        )

    probe.assert_not_awaited()
