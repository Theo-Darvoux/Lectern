import zipfile
from unittest.mock import MagicMock

import pytest

from app.core.security.file_security._office import _zip_strip_file
from app.core.security.file_security._zip import (
    _read_zip_entry_bounded,
    _recompress_zip_path,
    _register_zip_name,
    _sanitize_zip_entry_name,
    _validate_zip_name_conflicts,
)
from app.core.security.file_security.errors import SanitizationError, UnsafeFileError


def test_legacy_zip_name_registration_contract_is_preserved() -> None:
    registry: dict[str, bool | None] = {}

    _register_zip_name(registry, "folder/", is_dir=True)
    _register_zip_name(registry, "folder/file.txt", is_dir=False)

    with pytest.raises(ValueError, match="conflicts|duplicate"):
        _register_zip_name(registry, "FOLDER", is_dir=False)

    assert registry == {"folder": True, "folder/file.txt": False}


def test_zip_name_validation_retains_one_name_per_real_entry() -> None:
    entries = [
        (f"root/{index:04d}/" + "/".join(["nested"] * 30) + "/file.txt", False)
        for index in range(2_000)
    ]

    validated_count = _validate_zip_name_conflicts(entries)

    assert validated_count == len(entries)


def test_explicit_directory_and_descendant_are_allowed() -> None:
    validated_count = _validate_zip_name_conflicts([("docs/file.txt", False), ("docs/", True)])

    assert validated_count == 2


def test_file_directory_conflict_is_rejected_independent_of_order() -> None:
    for entries in (
        [("docs", False), ("docs/file.txt", False)],
        [("docs/file.txt", False), ("docs", False)],
    ):
        with pytest.raises(ValueError, match="conflicts with descendant"):
            _validate_zip_name_conflicts(entries)


def test_canonical_duplicate_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate sanitized"):
        _validate_zip_name_conflicts([("Docs/File.txt", False), ("docs/file.TXT", False)])


def test_aggregate_zip_name_budget_is_enforced(monkeypatch) -> None:
    import app.core.security.file_security._zip as zip_security

    monkeypatch.setattr(zip_security, "_ZIP_MAX_TOTAL_NAME_CHARS", 10)
    with pytest.raises(ValueError, match="aggregate character limit"):
        _validate_zip_name_conflicts([("123456", False), ("abcdef", False)])


def test_sorted_validation_finds_descendant_after_similar_sibling() -> None:
    with pytest.raises(ValueError, match="conflicts with descendant"):
        _validate_zip_name_conflicts(
            [("docs", False), ("docs-notes.txt", False), ("docs/file.txt", False)]
        )


def test_recompress_enforces_aggregate_name_budget(tmp_path, monkeypatch) -> None:
    import app.core.security.file_security._zip as zip_security

    monkeypatch.setattr(zip_security, "_ZIP_MAX_TOTAL_NAME_CHARS", 10)
    source = tmp_path / "names.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("123456", b"a")
        archive.writestr("abcdef", b"b")

    with pytest.raises(ValueError, match="aggregate character limit"):
        _recompress_zip_path(source)


def test_transform_limit_rejects_declared_size_before_decompression() -> None:
    item = zipfile.ZipInfo("large.png")
    item.file_size = 100
    archive = MagicMock()

    with pytest.raises(ValueError, match="transform limit"):
        _read_zip_entry_bounded(
            archive,
            item,
            0,
            max_entry_bytes=10,
        )

    archive.open.assert_not_called()


def test_office_relationship_limit_is_applied_before_xml_parse(
    tmp_path,
    monkeypatch,
) -> None:
    import app.core.security.file_security._office as office

    monkeypatch.setattr(office, "_MAX_PACKAGE_XML_BYTES", 16)
    source = tmp_path / "document.docx"
    output = tmp_path / "clean.docx"

    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("_rels/.rels", b"x" * 17)

    with pytest.raises(SanitizationError):
        _zip_strip_file(
            source,
            output,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )


def test_root_ooxml_relationship_file_is_security_checked(tmp_path) -> None:
    source = tmp_path / "document.docx"
    output = tmp_path / "clean.docx"
    relationships = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship
      Id="rId1"
      Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate"
      Target="https://example.invalid/template.dotm"
      TargetMode="External" />
</Relationships>
"""

    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("_rels/.rels", relationships)

    with pytest.raises(UnsafeFileError, match="prohibited external relationship"):
        _zip_strip_file(
            source,
            output,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )


def test_zip_path_component_limit_rejects_extreme_depth() -> None:
    path = "/".join(["a"] * 65) + "/file.txt"

    with pytest.raises(ValueError, match="too many components"):
        _sanitize_zip_entry_name(path)


def test_zip_path_total_length_limit_rejects_oversized_name() -> None:
    component = "a" * 255
    path = "/".join([component] * 17)

    with pytest.raises(ValueError, match="path is too long"):
        _sanitize_zip_entry_name(path)
