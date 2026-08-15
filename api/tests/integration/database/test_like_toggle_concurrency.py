from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.directory import Directory, DirectoryFavourite, DirectoryLike, DirectoryType
from app.models.material import Material, MaterialFavourite, MaterialLike
from app.models.user import User, UserRole
from app.services.directory import toggle_directory_favourite, toggle_directory_like
from app.services.material import toggle_favourite, toggle_like

DATABASE_URL = os.environ.get("REVERT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DATABASE_URL, reason="REVERT_TEST_DATABASE_URL is not configured"),
]


async def _seed_user(session_factory: async_sessionmaker) -> uuid.UUID:
    async with session_factory() as session:
        user = User(
            email=f"like-race-{uuid.uuid4()}@example.invalid",
            display_name="Like race user",
            role=UserRole.STUDENT,
        )
        session.add(user)
        await session.commit()
        return user.id


@pytest.mark.asyncio
async def test_concurrent_material_toggles_keep_membership_and_counter_consistent() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id = await _seed_user(sessions)

    async with sessions() as seed:
        material = Material(
            title="Material like race",
            slug=f"material-like-race-{uuid.uuid4().hex[:12]}",
            type="document",
            like_count=1,
        )
        seed.add(material)
        await seed.flush()
        seed.add(MaterialLike(user_id=user_id, material_id=material.id))
        await seed.commit()
        material_id = material.id

    async with sessions() as first, sessions() as second:
        first_result = await toggle_like(first, user_id, material_id)
        assert first_result is False

        competing = asyncio.create_task(toggle_like(second, user_id, material_id))
        await asyncio.sleep(0.2)
        assert not competing.done(), "competing material toggle did not wait for the pair lock"

        await first.commit()
        second_result = await asyncio.wait_for(competing, timeout=5)
        assert second_result is True
        await second.commit()

    async with sessions() as check:
        material = await check.get(Material, material_id)
        membership_count = await check.scalar(
            select(func.count())
            .select_from(MaterialLike)
            .where(MaterialLike.user_id == user_id, MaterialLike.material_id == material_id)
        )
        assert material is not None
        assert material.like_count == 1
        assert membership_count == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_directory_toggles_keep_membership_and_counter_consistent() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id = await _seed_user(sessions)

    async with sessions() as seed:
        directory = Directory(
            name="Directory like race",
            slug=f"directory-like-race-{uuid.uuid4().hex[:12]}",
            type=DirectoryType.FOLDER,
            like_count=0,
        )
        seed.add(directory)
        await seed.commit()
        directory_id = directory.id

    async with sessions() as first, sessions() as second:
        first_result = await toggle_directory_like(first, user_id, directory_id)
        assert first_result is True

        competing = asyncio.create_task(toggle_directory_like(second, user_id, directory_id))
        await asyncio.sleep(0.2)
        assert not competing.done(), "competing directory toggle did not wait for the pair lock"

        await first.commit()
        second_result = await asyncio.wait_for(competing, timeout=5)
        assert second_result is False
        await second.commit()

    async with sessions() as check:
        directory = await check.get(Directory, directory_id)
        membership_count = await check.scalar(
            select(func.count())
            .select_from(DirectoryLike)
            .where(DirectoryLike.user_id == user_id, DirectoryLike.directory_id == directory_id)
        )
        assert directory is not None
        assert directory.like_count == 0
        assert membership_count == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_material_favourite_toggles_are_linearizable() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id = await _seed_user(sessions)

    async with sessions() as seed:
        material = Material(
            title="Material favourite race",
            slug=f"material-favourite-race-{uuid.uuid4().hex[:12]}",
            type="document",
        )
        seed.add(material)
        await seed.commit()
        material_id = material.id

    async with sessions() as first, sessions() as second:
        assert await toggle_favourite(first, user_id, material_id) is True
        competing = asyncio.create_task(toggle_favourite(second, user_id, material_id))
        await asyncio.sleep(0.2)
        assert not competing.done(), "competing material favourite did not wait for the pair lock"
        await first.commit()
        assert await asyncio.wait_for(competing, timeout=5) is False
        await second.commit()

    async with sessions() as check:
        membership_count = await check.scalar(
            select(func.count())
            .select_from(MaterialFavourite)
            .where(
                MaterialFavourite.user_id == user_id,
                MaterialFavourite.material_id == material_id,
            )
        )
        assert membership_count == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_directory_favourite_toggles_are_linearizable() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id = await _seed_user(sessions)

    async with sessions() as seed:
        directory = Directory(
            name="Directory favourite race",
            slug=f"directory-favourite-race-{uuid.uuid4().hex[:12]}",
            type=DirectoryType.FOLDER,
        )
        seed.add(directory)
        await seed.flush()
        seed.add(DirectoryFavourite(user_id=user_id, directory_id=directory.id))
        await seed.commit()
        directory_id = directory.id

    async with sessions() as first, sessions() as second:
        assert await toggle_directory_favourite(first, user_id, directory_id) is False
        competing = asyncio.create_task(toggle_directory_favourite(second, user_id, directory_id))
        await asyncio.sleep(0.2)
        assert not competing.done(), "competing directory favourite did not wait for the pair lock"
        await first.commit()
        assert await asyncio.wait_for(competing, timeout=5) is True
        await second.commit()

    async with sessions() as check:
        membership_count = await check.scalar(
            select(func.count())
            .select_from(DirectoryFavourite)
            .where(
                DirectoryFavourite.user_id == user_id,
                DirectoryFavourite.directory_id == directory_id,
            )
        )
        assert membership_count == 1

    await engine.dispose()
