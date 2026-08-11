from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import ConflictError
from app.models.directory import Directory, DirectoryType
from app.models.pull_request import PullRequest
from app.services.pr import _exec_undelete_directory


def test_directory_model_declares_live_root_slug_unique_index() -> None:
    index = next(idx for idx in Directory.__table__.indexes if idx.name == "uq_directory_root_slug")
    assert index.unique is True
    assert [column.name for column in index.columns] == ["slug"]
    assert "parent_id IS NULL AND deleted_at IS NULL" in str(
        index.dialect_options["postgresql"]["where"]
    )


@pytest.mark.asyncio
async def test_restore_rejects_reused_root_slug_before_mutation(db_session: AsyncSession) -> None:
    slug = "restored-root-conflict"
    original = Directory(
        name="Original",
        slug=slug,
        type=DirectoryType.FOLDER,
        deleted_at=datetime.now(UTC),
    )
    replacement = Directory(name="Replacement", slug=slug, type=DirectoryType.FOLDER)
    db_session.add_all([original, replacement])
    await db_session.flush()

    detached_pr = cast(PullRequest, SimpleNamespace(author_id=None))
    with pytest.raises(ConflictError, match="already in use"):
        await _exec_undelete_directory(
            db_session,
            {"directory_id": str(original.id)},
            detached_pr,
            {},
        )

    assert original.deleted_at is not None
    assert replacement.deleted_at is None
