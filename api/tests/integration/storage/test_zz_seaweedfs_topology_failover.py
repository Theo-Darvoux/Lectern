from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Any

import pytest

from app.core.storage import facade

pytestmark = pytest.mark.integration


def _docker(*args: str) -> None:
    subprocess.run(
        ["docker", *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


async def _wait_for_read(file_key: str, expected: bytes) -> None:
    last_error: Exception | None = None
    for _ in range(20):
        try:
            async with asyncio.timeout(2):
                if await facade.read_full_object(file_key) == expected:
                    return
        except Exception as exc:
            last_error = exc
        await asyncio.sleep(0.5)
    if last_error is not None:
        raise last_error
    raise AssertionError("replicated object did not become readable")


@pytest.mark.asyncio
async def test_cross_rack_replica_survives_each_volume_outage(storage_key: Any) -> None:
    if os.getenv("SEAWEEDFS_TOPOLOGY") != "production":
        pytest.skip("requires the production-topology SeaweedFS runner")

    volume1 = os.environ["SEAWEEDFS_TOPOLOGY_VOLUME1"]
    volume2 = os.environ["SEAWEEDFS_TOPOLOGY_VOLUME2"]
    key = storage_key("cross-rack-failover.bin")
    payload = bytes(range(251)) * 8192
    await facade.upload_file(payload, key, content_type="application/octet-stream")
    assert await facade.read_full_object(key) == payload

    try:
        _docker("stop", volume1)
        await _wait_for_read(key, payload)
        _docker("start", volume1)
        await asyncio.sleep(4)
        await _wait_for_read(key, payload)

        _docker("stop", volume2)
        await _wait_for_read(key, payload)
    finally:
        for container in (volume1, volume2):
            try:
                _docker("start", container)
            except subprocess.CalledProcessError:
                pass
        await asyncio.sleep(2)
