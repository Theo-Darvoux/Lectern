from typing import Any

from app.services.email import send_password_reset_email as deliver_password_reset_email


async def send_password_reset_email(
    ctx: dict[str, Any],
    *,
    email: str,
    reset_link: str,
) -> None:
    """Deliver a password-reset message outside the API request lifecycle."""
    await deliver_password_reset_email(email, reset_link)
