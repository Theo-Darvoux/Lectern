from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import aioboto3
import pytest
import pytest_asyncio
from aiobotocore.config import AioConfig
from botocore.exceptions import ClientError

from app.config import settings
from app.core.storage import facade
from app.core.storage.backends import SeaweedFSBackend


@dataclass(frozen=True)
class SeaweedFSTestConfig:
    endpoint: str
    access_key: str
    secret_key: str
    region: str
    bucket: str
    use_ssl: bool

    @property
    def endpoint_url(self) -> str:
        scheme = "https" if self.use_ssl else "http"
        return f"{scheme}://{self.endpoint}"


def _integration_config() -> SeaweedFSTestConfig:
    if os.getenv("SEAWEEDFS_INTEGRATION") != "1":
        pytest.skip(
            "SeaweedFS integration tests are opt-in; set SEAWEEDFS_INTEGRATION=1",
            allow_module_level=False,
        )

    raw_endpoint = os.getenv("SEAWEEDFS_TEST_ENDPOINT", "127.0.0.1:18333")
    parsed = urlparse(raw_endpoint if "://" in raw_endpoint else f"http://{raw_endpoint}")
    if (
        not parsed.hostname
        or parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        pytest.fail(f"Invalid SEAWEEDFS_TEST_ENDPOINT: {raw_endpoint!r}")

    local_hosts = {"127.0.0.1", "::1", "localhost"}
    if parsed.hostname not in local_hosts and os.getenv("SEAWEEDFS_ALLOW_REMOTE") != "1":
        pytest.fail(
            "Refusing to run destructive integration tests against a remote endpoint. "
            "Set SEAWEEDFS_ALLOW_REMOTE=1 only for a dedicated disposable test service."
        )

    endpoint = parsed.netloc
    bucket = f"lectern-it-{uuid.uuid4().hex[:20]}"
    return SeaweedFSTestConfig(
        endpoint=endpoint,
        access_key=os.getenv("SEAWEEDFS_TEST_ACCESS_KEY", "minioadmin"),
        secret_key=os.getenv("SEAWEEDFS_TEST_SECRET_KEY", "minioadmin"),
        region=os.getenv("SEAWEEDFS_TEST_REGION", "us-east-1"),
        bucket=bucket,
        use_ssl=parsed.scheme == "https",
    )


def _raw_client(
    config: SeaweedFSTestConfig,
    *,
    access_key: str | None = None,
    secret_key: str | None = None,
) -> Any:
    session = aioboto3.Session()
    return session.client(
        "s3",
        endpoint_url=config.endpoint_url,
        aws_access_key_id=access_key or config.access_key,
        aws_secret_access_key=secret_key or config.secret_key,
        region_name=config.region,
        config=AioConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 2, "mode": "standard"},
            connect_timeout=3,
            read_timeout=15,
        ),
    )


async def _wait_until_ready(config: SeaweedFSTestConfig) -> None:
    last_error: Exception | None = None
    for _ in range(60):
        try:
            async with _raw_client(config) as client:
                await client.list_buckets()
            return
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.5)
    pytest.fail(f"SeaweedFS did not become ready: {last_error}")


async def _empty_and_delete_bucket(config: SeaweedFSTestConfig) -> None:
    async with _raw_client(config) as client:
        with contextlib.suppress(ClientError):
            paginator = client.get_paginator("list_multipart_uploads")
            async for page in paginator.paginate(Bucket=config.bucket):
                for upload in page.get("Uploads", []):
                    await client.abort_multipart_upload(
                        Bucket=config.bucket,
                        Key=upload["Key"],
                        UploadId=upload["UploadId"],
                    )

        with contextlib.suppress(ClientError):
            paginator = client.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=config.bucket):
                for obj in page.get("Contents", []):
                    await client.delete_object(Bucket=config.bucket, Key=obj["Key"])

        last_error: ClientError | None = None
        for _ in range(20):
            try:
                await client.delete_bucket(Bucket=config.bucket)
                return
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code not in {"BucketNotEmpty", "409", "Conflict"}:
                    raise
                last_error = exc
                await asyncio.sleep(0.25)
        if last_error is not None:
            raise last_error


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def seaweedfs_config() -> SeaweedFSTestConfig:
    config = _integration_config()
    await _wait_until_ready(config)

    async with _raw_client(config) as client:
        await client.create_bucket(Bucket=config.bucket)

    original_settings = {
        "storage_backend": settings.storage_backend,
        "s3_endpoint": settings.s3_endpoint,
        "s3_access_key": settings.s3_access_key,
        "s3_secret_key": settings.s3_secret_key,
        "s3_bucket": settings.s3_bucket,
        "s3_region": settings.s3_region,
        "s3_use_ssl": settings.s3_use_ssl,
        "s3_public_endpoint": settings.s3_public_endpoint,
        "s3_use_accelerate_endpoint": settings.s3_use_accelerate_endpoint,
        "worker_zip_url": settings.worker_zip_url,
        "worker_zip_hmac_secret": settings.worker_zip_hmac_secret,
    }
    original_storage = facade._storage

    settings.storage_backend = "seaweedfs"
    settings.s3_endpoint = config.endpoint
    settings.s3_access_key = config.access_key
    settings.s3_secret_key = config.secret_key
    settings.s3_bucket = config.bucket
    settings.s3_region = config.region
    settings.s3_use_ssl = config.use_ssl
    settings.s3_public_endpoint = None
    settings.s3_use_accelerate_endpoint = False
    settings.worker_zip_url = ""
    settings.worker_zip_hmac_secret = ""
    facade._storage = SeaweedFSBackend()

    try:
        yield config
    finally:
        try:
            current_storage = facade._storage
            if current_storage is not None:
                with contextlib.suppress(Exception):
                    await current_storage.close_s3_client()
            await _empty_and_delete_bucket(config)
        finally:
            facade._storage = original_storage
            for key, value in original_settings.items():
                setattr(settings, key, value)


@pytest.fixture
def seaweedfs_backend(seaweedfs_config: SeaweedFSTestConfig) -> SeaweedFSBackend:
    del seaweedfs_config
    backend = facade.get_storage()
    assert isinstance(backend, SeaweedFSBackend)
    return backend


@pytest.fixture
def seaweedfs_client_factory(
    seaweedfs_config: SeaweedFSTestConfig,
) -> Callable[..., Any]:
    def factory(
        *,
        access_key: str | None = None,
        secret_key: str | None = None,
    ) -> Any:
        return _raw_client(
            seaweedfs_config,
            access_key=access_key,
            secret_key=secret_key,
        )

    return factory


@pytest.fixture
def storage_key(
    seaweedfs_config: SeaweedFSTestConfig,
) -> Callable[[str], str]:
    # Keep the dependency explicit as well as autouse, so any future test using
    # a storage key can never bypass endpoint setup or the opt-in skip guard.
    del seaweedfs_config
    prefix = f"integration/{uuid.uuid4().hex}"

    def build(name: str) -> str:
        return f"{prefix}/{name}"

    return build
