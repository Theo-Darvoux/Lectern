import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services.directory import get_preview_material_ids


@pytest.mark.asyncio
async def test_preview_query_is_bounded_and_selects_only_four_per_root() -> None:
    root_id = uuid.uuid4()
    material_id = uuid.uuid4()
    db = AsyncMock()
    db.execute.return_value = [(root_id, material_id)]

    result = await get_preview_material_ids(db, [root_id])

    statement = db.execute.await_args.args[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()

    assert "row_number() over" in sql
    assert "preview_rank <= 4" in sql
    assert "preview_subtree.depth < 32" in sql
    assert result == {root_id: [str(material_id)]}
