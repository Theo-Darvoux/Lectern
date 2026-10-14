"""Viewer-scoped projection of pull requests with anonymous authorship.

An anonymous PR keeps ``author_id`` in the database (quotas, ownership checks,
moderation) but the author is only ever serialized to moderators. Regular
members, including the author, see the PR as anonymous; the author can still
recognise it through ``is_own``.
"""

import re
import typing

from app.models.user import User
from app.schemas.pull_request import PullRequestOut

# Staging and legacy storage keys embed the uploader's id as their second segment.
_USER_SCOPED_KEY = re.compile(r"^(uploads|materials|quarantine)/[0-9a-fA-F-]{36}/")


def can_see_anonymous_authors(viewer: User | None) -> bool:
    return viewer is not None and viewer.is_moderator


def redact_user_scoped_key(key: str) -> str:
    return _USER_SCOPED_KEY.sub(r"\1/anonymous/", key)


def anonymous_material_key(file_key: str) -> str:
    """Final ``materials/`` key for a legacy upload key, without the uploader's id.

    ``uploads/{user_id}/{upload_id}/{name}`` becomes
    ``materials/anonymous/{upload_id}/{name}``; the upload id keeps it unique.
    """
    return redact_user_scoped_key(file_key).replace("uploads/", "materials/", 1)


def _redact(value: typing.Any) -> typing.Any:
    if isinstance(value, str):
        return redact_user_scoped_key(value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    return value


def project_pr_for_viewer(pr: object, viewer: User | None) -> PullRequestOut:
    """Serialize a PR (ORM row or PullRequestOut) for ``viewer``."""
    out = pr if isinstance(pr, PullRequestOut) else PullRequestOut.model_validate(pr)
    author_id = out.author_id
    is_own = viewer is not None and author_id is not None and author_id == viewer.id
    out = out.model_copy(update={"is_own": is_own})
    if not out.is_anonymous or can_see_anonymous_authors(viewer):
        return out

    update: dict[str, typing.Any] = {"author_id": None, "author": None}
    # Self-approved PRs (auto-approve) would reveal the author as reviewer.
    if out.reviewed_by is not None and out.reviewed_by == author_id:
        update["reviewed_by"] = None
    if not is_own:
        # Text edits are staged under uploads/{user_id}/...; the author's own
        # client still needs the real key to preview them.
        update["payload"] = _redact(out.payload)
        update["applied_result"] = _redact(out.applied_result)
    return out.model_copy(update=update)
