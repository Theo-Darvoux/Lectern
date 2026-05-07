import logging

from meilisearch_python_sdk import AsyncClient
from meilisearch_python_sdk.errors import MeilisearchApiError
from meilisearch_python_sdk.models.settings import (
    MeilisearchSettings,
    MinWordSizeForTypos,
    TypoTolerance,
)

from app.config import settings

logger = logging.getLogger("wikint.meilisearch")

# Admin client — used by setup_meilisearch, index workers, and reindex scripts.
meili_admin_client = AsyncClient(settings.meili_url, settings.meili_master_key)

# Search-only client — used by the public search route.
# Falls back to master key in dev when MEILI_SEARCH_KEY is not set.
if settings.meili_search_key:
    meili_search_client = AsyncClient(settings.meili_url, settings.meili_search_key)
else:
    if not settings.is_dev:
        logger.warning(
            "MEILI_SEARCH_KEY is not set; falling back to master key for search. "
            "Provision a search-only key and set MEILI_SEARCH_KEY in production."
        )
    meili_search_client = meili_admin_client

# Backward-compat alias — workers imported `meili_client` before the split.
meili_client = meili_admin_client

_MATERIALS_RANKING_RULES = [
    "words",
    "typo",
    "proximity",
    "attribute",
    "sort",
    "exactness",
    "like_count:desc",
    "total_views:desc",
]
_DIRECTORIES_RANKING_RULES = [
    "words",
    "typo",
    "proximity",
    "attribute",
    "sort",
    "exactness",
    "like_count:desc",
]


def _settings_changed(current: MeilisearchSettings, desired: MeilisearchSettings) -> list[str]:
    """Return list of field names that differ between current and desired settings."""
    changed: list[str] = []
    for field in (
        "searchable_attributes",
        "filterable_attributes",
        "sortable_attributes",
        "ranking_rules",
    ):
        if getattr(current, field) != getattr(desired, field):
            changed.append(field)

    # Compare typo tolerance sub-fields that we explicitly set
    ct = current.typo_tolerance
    dt = desired.typo_tolerance
    if dt is not None:
        if ct is None or ct.enabled != dt.enabled:
            changed.append("typo_tolerance.enabled")
        elif (
            dt.min_word_size_for_typos is not None
            and ct.min_word_size_for_typos != dt.min_word_size_for_typos
        ):
            changed.append("typo_tolerance.min_word_size_for_typos")

    return changed


async def _apply_settings_if_changed(index_uid: str, desired: MeilisearchSettings) -> None:
    """Fetch current settings and call update_settings only when something differs."""
    try:
        current = await meili_admin_client.index(index_uid).get_settings()
    except Exception as e:
        logger.warning(
            "Could not fetch settings for '%s': %s — applying unconditionally", index_uid, e
        )
        await meili_admin_client.index(index_uid).update_settings(desired)
        return

    changed = _settings_changed(current, desired)
    if changed:
        logger.info("Updating '%s' settings (changed: %s)", index_uid, changed)
        await meili_admin_client.index(index_uid).update_settings(desired)
    else:
        logger.debug("'%s' settings up-to-date — skipping update_settings", index_uid)


async def setup_meilisearch() -> None:
    """Ensure Meilisearch indexes and settings are configured correctly."""

    typo_config = TypoTolerance(
        enabled=True,
        min_word_size_for_typos=MinWordSizeForTypos(one_typo=5, two_typos=9),
    )

    indexes = await meili_admin_client.get_indexes()
    existing_uids = [idx.uid for idx in indexes] if indexes else []

    # 1. Materials index
    if "materials" not in existing_uids:
        try:
            await meili_admin_client.create_index("materials", primary_key="id")
            logger.info("Created 'materials' index in Meilisearch")
        except MeilisearchApiError as e:
            if e.code != "index_already_exists":
                logger.error("Error creating 'materials' index: %s", e)
                raise

    materials_settings = MeilisearchSettings(
        searchable_attributes=[
            "title",
            "description",
            "tags",
            "slug",
            "type",
            "authorName",
            "ancestor_path",
            "extra_searchable",
        ],
        filterable_attributes=["type", "directory_id"],
        sortable_attributes=["like_count", "total_views", "created_at"],
        ranking_rules=_MATERIALS_RANKING_RULES,
        typo_tolerance=typo_config,
    )
    await _apply_settings_if_changed("materials", materials_settings)

    # 2. Directories index
    if "directories" not in existing_uids:
        try:
            await meili_admin_client.create_index("directories", primary_key="id")
            logger.info("Created 'directories' index in Meilisearch")
        except MeilisearchApiError as e:
            if e.code != "index_already_exists":
                logger.error("Error creating 'directories' index: %s", e)
                raise

    directories_settings = MeilisearchSettings(
        searchable_attributes=[
            "name",
            "description",
            "slug",
            "type",
            "tags",
            "code",
            "ancestor_path",
            "extra_searchable",
        ],
        filterable_attributes=["parent_id", "type"],
        sortable_attributes=["like_count", "created_at"],
        ranking_rules=_DIRECTORIES_RANKING_RULES,
        typo_tolerance=typo_config,
    )
    await _apply_settings_if_changed("directories", directories_settings)

    logger.info("Meilisearch setup complete.")
