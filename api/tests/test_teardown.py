import pytest


@pytest.fixture(autouse=True, scope="session")
async def cleanup_database_engine():
    yield
    import app.core.database.database as c_db

    await c_db.engine.dispose()
