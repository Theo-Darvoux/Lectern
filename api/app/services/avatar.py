"""Avatar reference invariants.

User.avatar_url is a presentation reference with exactly two supported forms:
- a server-generated object under avatars/{user_id}/<uuid>.webp; or
- a trusted Google OAuth profile-image HTTPS URL.

Application storage namespaces such as cas/, materials/, and quarantine/ are
never valid persisted avatar references and must never reach a presigner.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit
from uuid import UUID

_AVATAR_OBJECT_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.webp$"
)
_GOOGLE_AVATAR_HOST = "lh3.googleusercontent.com"


def avatar_storage_prefix(user_id: UUID | str) -> str:
    return f"avatars/{user_id}/"


def is_owned_avatar_storage_key(value: str | None, user_id: UUID | str) -> bool:
    """Return True only for the server-generated avatar namespace of this user."""
    if not value:
        return False
    prefix = avatar_storage_prefix(user_id)
    if not value.startswith(prefix):
        return False
    leaf = value[len(prefix) :]
    return bool(_AVATAR_OBJECT_RE.fullmatch(leaf))


def is_trusted_external_avatar_url(value: str | None) -> bool:
    """Accept only HTTPS Google-hosted OAuth profile pictures."""
    if not value:
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https":
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if port not in (None, 443):
        return False
    if hostname != _GOOGLE_AVATAR_HOST:
        return False
    return bool(parsed.path)


def is_safe_avatar_reference(value: str | None, user_id: UUID | str) -> bool:
    return is_owned_avatar_storage_key(value, user_id) or is_trusted_external_avatar_url(value)
