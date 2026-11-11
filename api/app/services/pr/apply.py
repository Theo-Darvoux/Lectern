"""Apply an approved pull request in one transaction."""

import typing
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.common.exceptions import BadRequestError
from app.models.pull_request import PullRequest
from app.services.pr.executors import _EXECUTORS, _capture_pre_state
from app.services.pr.hierarchy import _acquire_directory_tree_lock, _build_browse_path
from app.services.pr.operations import (
    _operation_uses_directory_tree_lock,
    _resolve,
    _without_deletes_covered_by_parent,
    topo_sort_operations,
)
from app.services.pr.versions import (
    _PR_PREPARED_VERSION_FILES_ACTIVE_KEY,
    _PR_PREPARED_VERSION_FILES_KEY,
    _prepare_pr_version_files,
)


async def apply_pr(db: AsyncSession, pr: PullRequest) -> None:
    """
    Execute all operations in a batch PR.  Operations are topologically sorted
    so that temp_id producers run before consumers, then executed sequentially
    within a single DB transaction.

    The original pr.payload is never mutated.  After execution, pr.applied_result
    is set to an enriched copy of the sorted operations, each annotated with:
      - result_id:          UUID string of the created/edited/deleted item
      - result_browse_path: slug-path usable as /browse/<path>
      - pre_state:          snapshot of the entity before mutation (for edit/move ops)
    """
    if pr.applied_result is not None:
        return

    sorted_ops = await _without_deletes_covered_by_parent(
        db, topo_sort_operations(list(pr.payload))
    )
    id_map: dict[str, uuid.UUID] = {}
    result_ops: list[dict[str, typing.Any]] = []

    # Storage calls can block on S3/SeaweedFS. Finish all of them before any
    # transaction-scoped global hierarchy lock can be acquired.
    await _prepare_pr_version_files(db, sorted_ops, pr.author_id, is_anonymous=pr.is_anonymous)

    # Enforce one transaction-wide lock order for mixed-operation PRs:
    # hierarchy advisory lock -> any target-row FOR UPDATE -> mutation.  Taking
    # this once before the loop prevents an early ordinary edit from holding a
    # row lock and later attempting the advisory lock while a competing move
    # holds the advisory lock and waits on that row.
    if any(
        _operation_uses_directory_tree_lock(
            str(op.get("op") or op.get("pr_type") or ""),
            op,
        )
        for op in sorted_ops
    ):
        await _acquire_directory_tree_lock(db)

    try:
        for op in sorted_ops:
            op_type = str(op.get("op") or op.get("pr_type") or "")  # pr_type for legacy rows
            if not op_type:
                raise BadRequestError(f"Operation missing 'op' field: {op}")

            executor = _EXECUTORS.get(op_type)
            if not executor:
                raise BadRequestError(f"Unknown operation type: {op_type}")

            is_delete = "delete" in op_type

            # Capture pre-state after the PR-wide hierarchy lock (when needed),
            # so the authoritative snapshot and mutation share one lock order.
            pre_state: dict[str, typing.Any] | None = None
            if op_type in ("edit_material", "edit_directory", "move_item"):
                pre_state = await _capture_pre_state(db, op_type, op, id_map)

            # For deletes, capture the browse path BEFORE the item is removed
            pre_delete_browse_path: str | None = None
            if is_delete:
                id_field = "directory_id" if "directory" in op_type else "material_id"
                target_uuid = _resolve(op.get(id_field), id_map)
                if target_uuid:
                    pre_delete_browse_path = await _build_browse_path(db, op_type, target_uuid)

            result_id = await executor(db, op, pr, id_map)

            # Register temp_id -> real UUID for downstream inter-op references
            temp_id = str(op.get("temp_id")) if op.get("temp_id") else None
            if temp_id:
                id_map[temp_id] = result_id

            # Build the enriched operation record for applied_result
            enriched: dict[str, typing.Any] = dict(op)
            enriched["result_id"] = str(result_id)
            if pre_state is not None:
                enriched["pre_state"] = pre_state
            if is_delete and pre_delete_browse_path:
                enriched["result_browse_path"] = pre_delete_browse_path
            elif not is_delete:
                browse_path = await _build_browse_path(db, op_type, result_id)
                if browse_path:
                    enriched["result_browse_path"] = browse_path

            result_ops.append(enriched)

    finally:
        db.info.pop(_PR_PREPARED_VERSION_FILES_KEY, None)
        db.info.pop(_PR_PREPARED_VERSION_FILES_ACTIVE_KEY, None)

    pr.applied_result = result_ops
    flag_modified(pr, "applied_result")
    pr.approved_at = datetime.now(UTC)
