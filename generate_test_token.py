import asyncio
import uuid
from app.core.database import SessionLocal
from app.services.directory import get_directory_download_entries
from app.config import settings
from app.core.worker_token import make_zip_token

async def main():
    async with SessionLocal() as db:
        # Assuming the root directory is just the first directory we can find,
        # or we just get_root_download_entries
        from app.services.directory import get_root_download_entries
        dir_name, entries = await get_root_download_entries(db)
        token = make_zip_token(dir_name, entries[:10], settings.worker_zip_hmac_secret)
        print(f"{settings.worker_zip_url.rstrip('/')}/zip?token={token}")

asyncio.run(main())
