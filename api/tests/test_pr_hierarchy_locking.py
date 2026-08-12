from app.services.pr import _operation_uses_directory_tree_lock


def test_hierarchy_lock_predicate_covers_namespace_and_revert_mutations() -> None:
    assert _operation_uses_directory_tree_lock("edit_material", {"title": "Renamed"})
    assert not _operation_uses_directory_tree_lock("edit_material", {"description": "Only text"})
    assert _operation_uses_directory_tree_lock("edit_directory", {"name": "Renamed"})
    assert not _operation_uses_directory_tree_lock("edit_directory", {"description": "Only text"})

    # Reverse edit operations restore captured slugs via pre_state rather than
    # carrying the original forward title/name field.
    assert _operation_uses_directory_tree_lock("edit_material", {"pre_state": {"slug": "old"}})
    assert _operation_uses_directory_tree_lock("edit_directory", {"pre_state": {"slug": "old"}})
    assert _operation_uses_directory_tree_lock("undelete_material", {})
    assert _operation_uses_directory_tree_lock("undelete_directory", {})
