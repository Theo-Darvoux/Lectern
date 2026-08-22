import re

from app.models.directory import Directory
from app.models.material import Material

_ALPHA_NUM = re.compile(r"([a-zA-Z]+)(\d+)")
_NUM_ALPHA = re.compile(r"(\d+)([a-zA-Z]+)")


def split_identifiers(text: str) -> str:
    if not text:
        return ""
    value = _ALPHA_NUM.sub(r"\1 \2", text)
    return _NUM_ALPHA.sub(r"\1 \2", value)


def build_material_search_document(
    material: Material,
    ancestor_path: str,
    browse_path: str,
) -> dict:  # type: ignore[type-arg]
    file_name = None
    file_mime_type = None
    for version in material.versions:
        if version.version_number == material.current_version:
            file_name = version.file_name
            file_mime_type = version.file_mime_type
            break

    extra = " ".join(
        (
            split_identifiers(material.title),
            split_identifiers(file_name or ""),
            split_identifiers(ancestor_path),
        )
    )

    return {
        "id": str(material.id),
        "title": material.title,
        "slug": material.slug,
        "description": material.description or "",
        "type": material.type,
        "status": material.status,
        "tags": [tag.name for tag in material.tags] if material.tags else [],
        "authorName": material.author.display_name if material.author else None,
        "directory_id": str(material.directory_id) if material.directory_id else None,
        "created_at": material.created_at.isoformat() if material.created_at is not None else None,
        "ancestor_path": ancestor_path,
        "extra_searchable": extra,
        "browse_path": browse_path,
        "total_views": material.total_views,
        "views_today": material.views_today,
        "like_count": material.like_count,
        "file_name": file_name,
        "file_mime_type": file_mime_type,
        "url": str((material.metadata_ or {}).get("url") or ""),
        "metadata": material.metadata_ or {},
    }


def build_directory_search_document(
    directory: Directory,
    ancestor_path: str,
    browse_path: str,
) -> dict:  # type: ignore[type-arg]
    metadata = directory.metadata_ or {}
    code = metadata.get("code") or ""
    extra = " ".join(
        (
            split_identifiers(directory.name),
            split_identifiers(str(code)),
            split_identifiers(ancestor_path),
        )
    )

    return {
        "id": str(directory.id),
        "name": directory.name,
        "slug": directory.slug,
        "type": directory.type.value if directory.type else "folder",
        "status": directory.status,
        "description": directory.description or "",
        "tags": [tag.name for tag in directory.tags] if directory.tags else [],
        "code": code,
        "parent_id": str(directory.parent_id) if directory.parent_id else None,
        "created_at": directory.created_at.isoformat()
        if directory.created_at is not None
        else None,
        "ancestor_path": ancestor_path,
        "extra_searchable": extra,
        "browse_path": browse_path,
        "like_count": directory.like_count,
        "metadata": metadata,
    }
