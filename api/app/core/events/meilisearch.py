import inspect
import logging

from meilisearch_python_sdk import AsyncClient
from meilisearch_python_sdk.errors import MeilisearchApiError
from meilisearch_python_sdk.models.client import KeyCreate
from meilisearch_python_sdk.models.settings import (
    MeilisearchSettings,
    MinWordSizeForTypos,
    TypoTolerance,
)

from app.config import settings

logger = logging.getLogger(__name__)

_SEARCH_KEY_NAME = "lectern-search-key"

meili_admin_client = AsyncClient(settings.meili_url, settings.meili_master_key)
meili_search_client: AsyncClient | None = None


def get_search_client() -> AsyncClient:
    """Safe accessor ensuring the restricted client is fully initialized."""
    if meili_search_client is None:
        raise RuntimeError("Meilisearch search client was accessed before initialization.")
    return meili_search_client


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


async def _ensure_search_key() -> str:
    """Return a valid search-only API key, auto-provisioning one if needed."""
    if settings.meili_search_key:
        try:
            async with AsyncClient(settings.meili_url, settings.meili_search_key) as probe:
                # Health is intentionally unauthenticated. A real search proves
                # that the configured key exists and has the required action.
                await probe.index("materials").search("")
            return settings.meili_search_key
        except MeilisearchApiError:
            logger.warning(
                "MEILI_SEARCH_KEY is invalid (key may have been lost after a volume reset) "
                "— auto-provisioning a replacement."
            )

    keys_result = await meili_admin_client.get_keys()
    existing = keys_result.results if keys_result is not None else []
    for key in existing:
        if key.name == _SEARCH_KEY_NAME:
            logger.info("Reusing existing auto-provisioned Meilisearch search key.")
            return key.key

    new_key = await meili_admin_client.create_key(
        KeyCreate(
            name=_SEARCH_KEY_NAME,
            description="Auto-provisioned search-only key for the API",
            actions=["search"],
            indexes=["materials", "directories"],
            expires_at=None,
        )
    )
    logger.info("Auto-provisioned new Meilisearch search key.")
    return new_key.key


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

    global meili_search_client
    search_key = await _ensure_search_key()
    new_search_client = AsyncClient(settings.meili_url, search_key)
    old_search_client = meili_search_client
    meili_search_client = new_search_client
    if old_search_client is not None and old_search_client is not new_search_client:
        close_result = old_search_client.aclose()
        if inspect.isawaitable(close_result):
            await close_result

    logger.info("Meilisearch setup complete.")
