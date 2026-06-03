"""
Tests for comment.validate_target — covers active vs. soft-deleted targets
and invalid target_type, verifying that comments cannot be posted on
soft-deleted materials or directories.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.directory import Directory
from app.models.material import Material
from app.services.comment import validate_target


async def test_validate_target_accepts_active_material(db_session: AsyncSession) -> None:
    material = Material(
        id=uuid.uuid4(),
        title="Active Doc",
        slug=f"active-{uuid.uuid4().hex[:8]}",
        type="document",
        current_version=1,
    )
    db_session.add(material)
    await db_session.flush()

    await validate_target(db_session, "material", str(material.id))


async def test_validate_target_rejects_soft_deleted_material(db_session: AsyncSession) -> None:
    material = Material(
        id=uuid.uuid4(),
        title="Deleted Doc",
        slug=f"deleted-{uuid.uuid4().hex[:8]}",
        type="document",
        current_version=1,
        deleted_at=datetime.now(UTC),
    )
    db_session.add(material)
    await db_session.flush()

    with pytest.raises(NotFoundError):
        await validate_target(db_session, "material", str(material.id))


async def test_validate_target_rejects_missing_material(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await validate_target(db_session, "material", str(uuid.uuid4()))


async def test_validate_target_accepts_active_directory(db_session: AsyncSession) -> None:
    directory = Directory(
        id=uuid.uuid4(),
        name="Course",
        slug=f"course-{uuid.uuid4().hex[:8]}",
        type="folder",
    )
    db_session.add(directory)
    await db_session.flush()

    await validate_target(db_session, "directory", str(directory.id))


async def test_validate_target_rejects_soft_deleted_directory(db_session: AsyncSession) -> None:
    directory = Directory(
        id=uuid.uuid4(),
        name="Deleted Dir",
        slug=f"del-dir-{uuid.uuid4().hex[:8]}",
        type="folder",
        deleted_at=datetime.now(UTC),
    )
    db_session.add(directory)
    await db_session.flush()

    with pytest.raises(NotFoundError):
        await validate_target(db_session, "directory", str(directory.id))


async def test_validate_target_rejects_invalid_target_type(db_session: AsyncSession) -> None:
    with pytest.raises(BadRequestError):
        await validate_target(db_session, "annotation", str(uuid.uuid4()))
