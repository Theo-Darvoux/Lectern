import pytest

from app.core.common.exceptions import BadRequestError
from app.services.pr import _assert_temp_refs_defined


def test_accepts_references_to_ops_in_the_same_batch() -> None:
    _assert_temp_refs_defined(
        [
            {"op": "create_directory", "temp_id": "$dir-1", "parent_id": None},
            {"op": "create_material", "temp_id": "$mat-1", "directory_id": "$dir-1"},
            {"op": "create_material", "directory_id": "$dir-1", "parent_material_id": "$mat-1"},
        ]
    )


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "create_material", "directory_id": "$dir-9"},
        {"op": "create_material", "directory_id": None, "parent_material_id": "$mat-9"},
        {"op": "create_directory", "parent_id": "$dir-9"},
        {"op": "delete_material", "material_id": "$mat-9"},
        {"op": "move_item", "target_id": "$mat-9", "new_parent_id": None},
        {"op": "move_item", "target_id": "11111111", "new_parent_id": "$dir-9"},
    ],
)
def test_rejects_a_reference_nothing_in_the_batch_creates(operation: dict[str, object]) -> None:
    with pytest.raises(BadRequestError):
        _assert_temp_refs_defined([operation])


def test_a_title_starting_with_a_dollar_is_not_a_reference() -> None:
    _assert_temp_refs_defined([{"op": "create_material", "directory_id": None, "title": "$HOME"}])
