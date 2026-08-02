import zipfile
from unittest.mock import MagicMock

import pytest

from app.core.security.file_security._office import _zip_strip_file
from app.core.security.file_security._zip import (
    _read_zip_entry_bounded,
    _register_zip_name,
)
from app.core.security.file_security.errors import SanitizationError, UnsafeFileError


class _NoIterationRegistry(dict[str, bool | None]):
    def __iter__(self):
        raise AssertionError("name registration must not scan the whole registry")


def test_zip_name_registration_is_independent_of_registry_size() -> None:
    registry: dict[str, bool | None] = _NoIterationRegistry()
    for index in range(10_000):
        registry[f"existing/{index}.txt"] = False

    _register_zip_name(registry, "new/child.txt", is_dir=False)

    assert registry["new"] is None
    assert registry["new/child.txt"] is False


def test_explicit_directory_can_follow_implicit_parent() -> None:
    registry: dict[str, bool | None] = {}

    _register_zip_name(registry, "docs/file.txt", is_dir=False)
    _register_zip_name(registry, "docs/", is_dir=True)

    assert registry["docs"] is True


def test_file_directory_conflict_is_rejected_in_both_orders() -> None:
    first: dict[str, bool | None] = {}
    _register_zip_name(first, "docs", is_dir=False)
    with pytest.raises(ValueError, match="nested beneath file"):
        _register_zip_name(first, "docs/file.txt", is_dir=False)

    second: dict[str, bool | None] = {}
    _register_zip_name(second, "docs/file.txt", is_dir=False)
    with pytest.raises(ValueError, match="duplicate sanitized"):
        _register_zip_name(second, "docs", is_dir=False)


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
