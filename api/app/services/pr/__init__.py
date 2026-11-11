"""Pull-request service: creation, review transitions, application and revert.

Each submodule owns one concern; this package re-exports the public entry points.
"""

from app.services.pr.apply import apply_pr
from app.services.pr.create import create_pull_request_service
from app.services.pr.operations import topo_sort_operations
from app.services.pr.queries import (
    count_prs_service,
    get_pr_diff_service,
    get_pr_preview_service,
    list_prs_for_item_service,
    list_prs_service,
)
from app.services.pr.resources import get_pr_all_file_keys, get_pr_staging_files
from app.services.pr.revert import revert_pr
from app.services.pr.transitions import (
    approve_pr_service,
    cancel_pr_service,
    reject_pr_service,
    revert_pr_service,
)

__all__ = [
    "apply_pr",
    "approve_pr_service",
    "cancel_pr_service",
    "count_prs_service",
    "create_pull_request_service",
    "get_pr_all_file_keys",
    "get_pr_diff_service",
    "get_pr_preview_service",
    "get_pr_staging_files",
    "list_prs_for_item_service",
    "list_prs_service",
    "reject_pr_service",
    "revert_pr",
    "revert_pr_service",
    "topo_sort_operations",
]
