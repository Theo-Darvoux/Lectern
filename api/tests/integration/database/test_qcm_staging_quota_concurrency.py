from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.common.exceptions import BadRequestError
from app.core.common.upload_errors import UploadErrorCode
from app.models.cas_staging_claim import CasStagingClaim
from app.models.user import User, UserRole
from app.routers.qcm import _check_qcm_staging_quota

DATABASE_URL = os.environ.get("REVERT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DATABASE_URL, reason="REVERT_TEST_DATABASE_URL is not configured"),
]


async def _seed_user(session_factory: async_sessionmaker) -> uuid.UUID:
    async with session_factory() as session:
        user = User(
            email=f"qcm-quota-{uuid.uuid4()}@example.invalid",
            display_name="QCM quota race",
            role=UserRole.STUDENT,
        )
        session.add(user)
        await session.commit()
        return user.id


def _claim(user_id: uuid.UUID, size_bytes: int) -> CasStagingClaim:
    claim_id = uuid.uuid4()
    return CasStagingClaim(
        id=claim_id,
        user_id=user_id,
        file_key=f"cas/{claim_id.hex}",
        sha256=claim_id.hex * 2,
        size_bytes=size_bytes,
        expires_at=datetime.now(UTC) + timedelta(hours=48),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("max_claims", "max_bytes", "first_size", "second_size"),
    [
        pytest.param(1, 1024, 1, 1, id="object-count"),
        pytest.param(50, 10, 6, 6, id="byte-budget"),
    ],
)
async def test_concurrent_qcm_staging_admission_cannot_oversubscribe_user_quota(
    max_claims: int,
    max_bytes: int,
    first_size: int,
    second_size: int,
) -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id = await _seed_user(sessions)

    with (
        patch("app.routers.qcm.QCM_MAX_OUTSTANDING_CLAIMS", max_claims),
        patch("app.routers.qcm.QCM_MAX_OUTSTANDING_BYTES", max_bytes),
    ):
        async with sessions() as first, sessions() as second:
            await _check_qcm_staging_quota(first, user_id, first_size)
            first.add(_claim(user_id, first_size))
            await first.flush()

            competing = asyncio.create_task(_check_qcm_staging_quota(second, user_id, second_size))
            await asyncio.sleep(0.2)
            assert not competing.done(), "second admission crossed the per-user row lock"

            await first.commit()
            with pytest.raises(BadRequestError) as exc_info:
                await asyncio.wait_for(competing, timeout=5)
            assert exc_info.value.code == UploadErrorCode.QUOTA_EXCEEDED
            await second.rollback()

    async with sessions() as check:
        row = (
            await check.execute(
                select(func.count(CasStagingClaim.id), func.sum(CasStagingClaim.size_bytes)).where(
                    CasStagingClaim.user_id == user_id,
                    CasStagingClaim.consumed_at.is_(None),
                    CasStagingClaim.expires_at > datetime.now(UTC),
                )
            )
        ).one()
        assert int(row[0]) == 1
        assert int(row[1]) == first_size

    async with sessions() as cleanup:
        await cleanup.execute(delete(User).where(User.id == user_id))
        await cleanup.commit()

    await engine.dispose()
