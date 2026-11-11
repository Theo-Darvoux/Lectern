"""Pure helpers over a PR's operation list: temp-id references, ordering, and
which operations need the directory-tree lock."""

import typing
import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.common.exceptions import BadRequestError
from app.models.directory import Directory
from app.models.material import Material


def _is_temp_id(value: str | None) -> bool:
    """Check if a value is a $-prefixed temporary inter-reference ID."""
    return isinstance(value, str) and value.startswith("$")


def _pr_directory_topics(payload: list[dict[str, typing.Any]]) -> set[str]:
    """Return the set of SSE topic strings for all real directories targeted by a PR payload.

    create_material ops use ``directory_id``; create_directory ops use ``parent_id``.
    Null → "root".  $temp-id strings are skipped (directory not yet resolved).
    """
    topics: set[str] = set()
    for op in payload:
        raw = op.get("directory_id") or op.get("parent_id")
        if raw is None:
            topics.add("root")
        elif isinstance(raw, str) and not raw.startswith("$"):
            topics.add(raw)
        elif not isinstance(raw, str):
            topics.add(str(raw))
    return topics


def _resolve(value: str | None, id_map: dict[str, uuid.UUID]) -> uuid.UUID | None:
    """Resolve a value that may be a temp ID, a real UUID string, or None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if _is_temp_id(s):
        resolved = id_map.get(s)
        if resolved is None:
            raise BadRequestError(f"Unresolved temp_id reference: {s}")
        return resolved
    try:
        return uuid.UUID(s)
    except ValueError:
        raise BadRequestError(f"Invalid UUID: {s}")


def _collect_temp_refs(op: dict[str, typing.Any]) -> set[str]:
    """Collect all $-prefixed references from an operation dict (not its own temp_id)."""
    refs: set[str] = set()
    for key, val in op.items():
        if key == "temp_id":
            continue
        if isinstance(val, str) and _is_temp_id(val):
            refs.add(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    for v2 in item.values():
                        if isinstance(v2, str) and _is_temp_id(v2):
                            refs.add(v2)
    return refs


_TEMP_REF_FIELDS = (
    "directory_id",
    "parent_id",
    "parent_material_id",
    "material_id",
    "target_id",
    "new_parent_id",
)


def _assert_temp_refs_defined(operations: list[dict[str, typing.Any]]) -> None:
    """Reject a batch referencing a $temp-id that no operation in it creates.

    Such a reference can only fail at approval, leaving a contribution that
    nobody can apply.
    """
    defined = {op["temp_id"] for op in operations if op.get("temp_id")}
    for op in operations:
        for field in _TEMP_REF_FIELDS:
            value = op.get(field)
            if _is_temp_id(value) and value not in defined:
                raise BadRequestError(
                    "This contribution refers to an item that is no longer in your draft. "
                    "Remove it from the draft and try again."
                )


def topo_sort_operations(operations: list[dict[str, typing.Any]]) -> list[dict[str, typing.Any]]:
    """
    Topologically sort operations so that any op defining a temp_id
    comes before ops referencing that temp_id.  Preserves submission
    order when there are no dependencies.
    """
    n = len(operations)
    # Build mapping: temp_id -> index of the defining op
    definer: dict[str, int] = {}
    for i, op in enumerate(operations):
        tid = op.get("temp_id")
        if tid:
            definer[tid] = i

    # Build adjacency: edges[i] = set of indices that must come before i
    deps: dict[int, set[int]] = defaultdict(set)
    for i, op in enumerate(operations):
        for ref in _collect_temp_refs(op):
            if ref in definer:
                deps[i].add(definer[ref])

    # Kahn's algorithm maintaining original order via index-based BFS
    in_degree = [0] * n
    for i in range(n):
        in_degree[i] = len(deps[i])

    queue: list[int] = [i for i in range(n) if in_degree[i] == 0]
    result: list[int] = []

    while queue:
        # Take the item with the lowest original index (stable order)
        queue.sort()
        idx = queue.pop(0)
        result.append(idx)
        # Release dependents
        for j in range(n):
            if idx in deps[j]:
                deps[j].discard(idx)
                in_degree[j] -= 1
                if in_degree[j] == 0:
                    queue.append(j)

    if len(result) != n:
        raise BadRequestError("Cyclic dependency detected among temp_id references")

    return [operations[i] for i in result]


async def _without_deletes_covered_by_parent(
    db: AsyncSession, operations: list[dict[str, typing.Any]]
) -> list[dict[str, typing.Any]]:
    """Drop deletes that another delete in the same batch already performs.

    Deleting a directory soft-deletes its subtree and deleting a material its
    attachments, so a separate delete of one of those items would not find it.
    """

    def real_id(value: object) -> uuid.UUID | None:
        try:
            return uuid.UUID(str(value))
        except ValueError:
            return None  # A temp id, or an invalid one the executor will report.

    def ids_of(op_type: str, field: str) -> set[uuid.UUID]:
        ids = {real_id(op.get(field)) for op in operations if op.get("op") == op_type}
        return {value for value in ids if value is not None}

    deleted_dirs = ids_of("delete_directory", "directory_id")
    deleted_mats = ids_of("delete_material", "material_id")
    if not deleted_dirs and not deleted_mats:
        return operations

    covered_dirs: set[uuid.UUID] = set()
    if deleted_dirs:
        child_cte = (
            select(Directory.id)
            .where(Directory.parent_id.in_(deleted_dirs), Directory.deleted_at.is_(None))
            .cte(name="covered_dirs", recursive=True)
        )
        dir_alias = aliased(Directory)
        child_cte = child_cte.union_all(
            select(dir_alias.id)
            .join(child_cte, dir_alias.parent_id == child_cte.c.id)
            .where(dir_alias.deleted_at.is_(None))
        )
        covered_dirs = set(await db.scalars(select(child_cte.c.id)))

    covered_mats: set[uuid.UUID] = set()
    if deleted_mats:
        rows = await db.execute(
            select(Material.id, Material.directory_id, Material.parent_material_id).where(
                Material.id.in_(deleted_mats)
            )
        )
        all_deleted_dirs = deleted_dirs | covered_dirs
        covered_mats = {
            row.id
            for row in rows
            if row.directory_id in all_deleted_dirs or row.parent_material_id in deleted_mats
        }

    def is_covered(op: dict[str, typing.Any]) -> bool:
        if op.get("op") == "delete_directory":
            return real_id(op.get("directory_id")) in covered_dirs
        if op.get("op") == "delete_material":
            return real_id(op.get("material_id")) in covered_mats
        return False

    return [op for op in operations if not is_covered(op)]


def _operation_uses_directory_tree_lock(op_type: str, op: dict[str, typing.Any]) -> bool:
    """Return whether an operation mutates the shared hierarchy/slug namespace.

    Reverse edit operations carry only ``pre_state`` rather than the forward
    ``name``/``title`` field.  Treat those as namespace mutations too: the
    revert executor restores the captured slug even when it happens to be
    unchanged.
    """
    if op_type in {
        "create_material",
        "create_directory",
        "delete_material",
        "delete_directory",
        "undelete_material",
        "undelete_directory",
        "move_item",
    }:
        return True
    if op_type == "edit_directory":
        return op.get("name") is not None or "pre_state" in op
    if op_type == "edit_material":
        return op.get("title") is not None or "pre_state" in op
    return False
