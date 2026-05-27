import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.download_audit import DownloadAudit
from app.models.user import User

logger = logging.getLogger("audit")


async def record_download(
    db: AsyncSession,
    user_id: uuid.UUID,
    material_id: uuid.UUID,
    version_number: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    # 1. DB Record
    audit = DownloadAudit(
        user_id=user_id,
        material_id=material_id,
        version_number=version_number,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(audit)

    # 2. Structured JSON Log
    log_entry = {
        "event": "material_download",
        "timestamp": datetime.now(UTC).isoformat(),
        "user_id": str(user_id),
        "material_id": str(material_id),
        "version": version_number,
        "ip": ip_address,
        "ua": user_agent,
    }
    logger.info(json.dumps(log_entry))


async def flag_user_account(db: AsyncSession, user_id: uuid.UUID, reason: str) -> None:
    await db.execute(update(User).where(User.id == user_id).values(is_flagged=True))

    log_entry = {
        "event": "user_flagged",
        "timestamp": datetime.now(UTC).isoformat(),
        "user_id": str(user_id),
        "reason": reason,
    }
    logger.warning(json.dumps(log_entry))
