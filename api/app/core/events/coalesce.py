from enum import StrEnum
from typing import Any


class JobKind(StrEnum):
    """Background job kind identifiers used in post-commit and worker queues."""

    INDEX_MATERIAL = "index_material"
    INDEX_MATERIALS_BATCH = "index_materials_batch"
    INDEX_DIRECTORY = "index_directory"
    INDEX_DIRECTORIES_BATCH = "index_directories_batch"


def coalesce_index_jobs(jobs: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    """Coalesce consecutive index_material / index_directory jobs into batch calls.

    Preserves relative order of non-index jobs (delete_indexed_item,
    delete_storage_objects, etc.) so that deletes always execute before or after
    the adjacent index operations as originally ordered.
    """
    result: list[tuple[Any, ...]] = []
    i = 0
    while i < len(jobs):
        kind = jobs[i][0]
        if kind == JobKind.INDEX_MATERIAL:
            batch: list[Any] = [jobs[i][1]]
            i += 1
            while i < len(jobs) and jobs[i][0] == JobKind.INDEX_MATERIAL:
                batch.append(jobs[i][1])
                i += 1
            if len(batch) == 1:
                result.append((JobKind.INDEX_MATERIAL, batch[0]))
            else:
                result.append((JobKind.INDEX_MATERIALS_BATCH, batch))
        elif kind == JobKind.INDEX_DIRECTORY:
            batch = [jobs[i][1]]
            i += 1
            while i < len(jobs) and jobs[i][0] == JobKind.INDEX_DIRECTORY:
                batch.append(jobs[i][1])
                i += 1
            if len(batch) == 1:
                result.append((JobKind.INDEX_DIRECTORY, batch[0]))
            else:
                result.append((JobKind.INDEX_DIRECTORIES_BATCH, batch))
        else:
            result.append(jobs[i])
            i += 1
    return result
