import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.material import Material
from app.models.pull_request import PRStatus, PullRequest, VirusScanResult
from app.models.user import User, UserRole
from app.services.pr import apply_pr


async def _create_user(db: AsyncSession, role: UserRole = UserRole.STUDENT) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@telecom-sudparis.eu",
        display_name="Tester",
        role=role,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.commit()
    return user


@pytest.mark.asyncio
async def test_apply_pr_idempotency(db_session: AsyncSession):
    """Verify that calling apply_pr twice on the same PR is safe and doesn't duplicate records."""
    user = await _create_user(db_session, UserRole.BUREAU)

    # 1. Create a PR with a simple operation
    pr_id = uuid.uuid4()
    payload = [
        {
            "op": "create_material",
            "temp_id": "new-mat",
            "title": "Idempotent Mat",
            "type": "document",
            "directory_id": None,
            "file_key": "uploads/user/file.pdf",
        }
    ]

    pr = PullRequest(
        id=pr_id,
        status=PRStatus.OPEN,
        title="Test PR",
        author_id=user.id,
        payload=payload,
        type="batch",
        summary_types=["create_material"],
        virus_scan_result=VirusScanResult.CLEAN,
    )
    db_session.add(pr)
    await db_session.commit()

    # Mock storage info and copy
    with (
        patch("app.services.pr.get_object_info", new_callable=AsyncMock) as mock_info,
        patch("app.services.pr.copy_object", new_callable=AsyncMock) as mock_copy,
        patch("app.services.pr._unique_material_slug", new_callable=AsyncMock) as mock_slug,
    ):
        mock_info.return_value = {"size": 100, "content_type": "application/pdf"}
        mock_slug.return_value = "idempotent-mat"

        # 2. Apply PR for the first time
        await apply_pr(db_session, pr)
        await db_session.commit()

        # Verify material created
        mat_count = await db_session.scalar(select(func.count(Material.id)))
        assert mat_count == 1

        # 3. Apply PR again (simulating a retry or double-click)
        # It should skip because result_id is now in the payload
        await apply_pr(db_session, pr)
        await db_session.commit()

        # Verify STILL only one material
        mat_count_after = await db_session.scalar(select(func.count(Material.id)))
        assert mat_count_after == 1

        # Check copy_object was only called once for the first application
        assert mock_copy.call_count == 1
