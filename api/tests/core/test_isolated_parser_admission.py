from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from app.config import settings
from app.core.security import isolated_parser
from app.core.security.file_security import _concurrency


@pytest.mark.asyncio
async def test_parser_children_obey_the_process_wide_subprocess_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(_concurrency, "_local_subprocess_sem", asyncio.Semaphore(1))
    active = 0
    maximum_active = 0

    async def fake_sandboxed_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        payload = {
            "actual_mime": "text/plain",
            "uncompressed_size": None,
            "parser_pid": 123,
            "parser_uid": 456,
        }
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(payload).encode())

    monkeypatch.setattr(_concurrency, "async_sandboxed_run", fake_sandboxed_run)
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"

    await asyncio.gather(
        isolated_parser.inspect_upload(
            first,
            filename=first.name,
            declared_mime="text/plain",
            inspect_archive=False,
        ),
        isolated_parser.inspect_upload(
            second,
            filename=second.name,
            declared_mime="text/plain",
            inspect_archive=False,
        ),
    )

    assert maximum_active == 1
