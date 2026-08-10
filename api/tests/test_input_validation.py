"""
Comprehensive input validation tests.

Covers every user-supplied text field across all schemas:
  - Length limits (min, max, boundary)
  - Control-character / dangerous-Unicode stripping
  - Null-byte sanitization
  - Allowlist enforcement (enum fields, type fields)
  - File-key / filename path-traversal prevention
  - UUID format enforcement
  - SQL-injection / XSS payloads (should sanitize or reject)

All tests are pure unit-tests against Pydantic schemas — no DB or HTTP
needed, so they are fast and fully isolated.
"""

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Payloads that stress-test injection resistance.
# Schema-level validation doesn't need to neutralise SQL — that's the ORM's
# job — but it *must* reject or strip non-printable / oversized input.
SQL_INJECTIONS = [
    "'; DROP TABLE users; --",
    "1 OR 1=1",
    "' UNION SELECT * FROM users --",
]
XSS_PAYLOADS = [
    "<script>alert(1)</script>",
    "javascript:alert(1)",
    '"><img src=x onerror=alert(1)>',
]
# Chars that must be stripped by SanitizedStr / clean_text
CONTROL_CHARS = [
    "\x00",  # null
    "\x01",  # SOH
    "\x07",  # BEL
    "\x08",  # BS
    "\x0b",  # VT
    "\x0c",  # FF
    "\x0e",  # SO
    "\x1f",  # US
    "\x7f",  # DEL
    "\x80",  # C1
    "\x9f",  # C1
]
BIDI_CHARS = [
    "\u200b",  # zero-width space
    "\u202e",  # right-to-left override
    "\ufeff",  # BOM
    "\u2028",  # line separator
    "\u2029",  # paragraph separator
]


# ===========================================================================
# 1. core/sanitization — clean_text
# ===========================================================================


class TestCleanText:
    """The clean_text helper must strip dangerous chars and leave normal text."""

    def setup_method(self):
        from app.core.sanitization import clean_text

        self.clean = clean_text

    def test_strips_null_byte(self):
        assert self.clean("hel\x00lo") == "hello"

    def test_strips_c0_controls_except_whitespace(self):
        # \t (0x09), \n (0x0a), \r (0x0d) must be kept
        assert "\t" in self.clean("col1\tcol2")
        assert "\n" in self.clean("line1\nline2")
        assert "\r" in self.clean("win\r\nline")

    @pytest.mark.parametrize("char", CONTROL_CHARS)
    def test_strips_control_char(self, char):
        result = self.clean(f"before{char}after")
        assert char not in result
        assert "before" in result
        assert "after" in result

    @pytest.mark.parametrize("char", BIDI_CHARS)
    def test_strips_bidi_and_invisible(self, char):
        result = self.clean(f"text{char}here")
        assert char not in result

    def test_preserves_unicode_letters(self):
        text = "Héllo Wörld — 日本語 — Ñoño"
        assert self.clean(text) == text

    def test_preserves_emoji(self):
        text = "Hello 👋 world"
        assert self.clean(text) == text

    def test_strips_multiple_bad_chars(self):
        result = self.clean("\x00\x01\x07safe\x1f\x7f")
        assert result == "safe"

    def test_empty_string(self):
        assert self.clean("") == ""


# ===========================================================================
# 2. schemas/auth — RequestCodeIn, VerifyCodeIn, VerifyMagicLinkIn
# ===========================================================================


class TestRequestCodeIn:
    def _make(self, email: str):
        from app.schemas.auth import RequestCodeIn

        return RequestCodeIn(email=email)

    def test_valid_primary_domain(self):
        m = self._make("alice@example.com")
        assert m.email == "alice@example.com"

    def test_valid_secondary_domain(self):
        m = self._make("bob@example.org")
        assert m.email == "bob@example.org"

    def test_strips_and_lowercases(self):
        m = self._make("  Alice@Example.COM  ")
        assert m.email == "alice@example.com"

    def test_gmail_passes_schema_domain_policy_enforced_at_service_layer(self):
        # Domain whitelist is enforced asynchronously in the service layer (Phase 2).
        # The Pydantic schema only validates email format, not domain policy.
        # See test_auth_config.py::test_request_code_disallowed_domain for the full flow.
        m = self._make("user@gmail.com")
        assert m.email == "user@gmail.com"

    def test_rejects_plus_alias(self):
        with pytest.raises(ValidationError):
            self._make("alice+spam@example.com")

    def test_rejects_too_long(self):
        local = "a" * 250
        with pytest.raises(ValidationError):
            self._make(f"{local}@example.com")

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            self._make("")


class TestVerifyCodeIn:
    def _make(self, email: str, code: str):
        from app.schemas.auth import VerifyCodeIn

        return VerifyCodeIn(email=email, code=code)

    def test_valid(self):
        m = self._make("alice@example.com", "ABCDEF23")
        assert m.code == "ABCDEF23"

    def test_gmail_passes_schema_domain_policy_enforced_at_service_layer(self):
        # Domain whitelist is enforced asynchronously in the service layer (Phase 2).
        # The Pydantic schema only validates email format, not domain policy.
        m = self._make("alice@gmail.com", "ABCDEF23")
        assert m.email == "alice@gmail.com"

    def test_rejects_short_code(self):
        with pytest.raises(ValidationError):
            self._make("alice@example.com", "ABC")

    def test_rejects_long_code(self):
        with pytest.raises(ValidationError):
            self._make("alice@example.com", "ABCDEF2300")

    def test_rejects_lowercase_code(self):
        with pytest.raises(ValidationError):
            self._make("alice@example.com", "abcdef23")

    def test_rejects_digits_only_code(self):
        # '1' and '0' are not in the OTP alphabet
        with pytest.raises(ValidationError):
            self._make("alice@example.com", "00000000")

    def test_rejects_code_with_symbols(self):
        with pytest.raises(ValidationError):
            self._make("alice@example.com", "ABCDE!23")

    def test_rejects_plus_alias(self):
        with pytest.raises(ValidationError):
            self._make("alice+x@example.com", "ABCDEF23")


class TestVerifyMagicLinkIn:
    def _make(self, token: str):
        from app.schemas.auth import VerifyMagicLinkIn

        return VerifyMagicLinkIn(token=token)

    def test_valid_token(self):
        token = "a" * 64
        m = self._make(token)
        assert m.token == token

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            self._make("")

    def test_rejects_too_long(self):
        with pytest.raises(ValidationError):
            self._make("a" * 200)


# ===========================================================================
# 3. schemas/user — OnboardIn, UserUpdateIn
# ===========================================================================


class TestOnboardIn:
    def _make(self, **kw):
        from app.schemas.user import OnboardIn

        defaults = {"display_name": "Alice", "academic_year": "1A", "gdpr_consent": True}
        return OnboardIn(**{**defaults, **kw})

    def test_valid(self):
        m = self._make()
        assert m.display_name == "Alice"

    def test_strips_null_byte_from_display_name(self):
        m = self._make(display_name="Ali\x00ce")
        assert "\x00" not in m.display_name

    def test_strips_bidi_from_display_name(self):
        m = self._make(display_name="Ali\u202ece")
        assert "\u202e" not in m.display_name

    def test_rejects_empty_display_name(self):
        with pytest.raises(ValidationError):
            self._make(display_name="")

    def test_rejects_display_name_too_long(self):
        with pytest.raises(ValidationError):
            self._make(display_name="a" * 65)

    def test_display_name_max_length_ok(self):
        m = self._make(display_name="a" * 64)
        assert len(m.display_name) == 64

    def test_rejects_invalid_academic_year(self):
        with pytest.raises(ValidationError):
            self._make(academic_year="4A")

    def test_valid_academic_years(self):
        for year in ("1A", "2A", "3A+"):
            m = self._make(academic_year=year)
            assert m.academic_year == year


class TestUserUpdateIn:
    def _make(self, **kw):
        from app.schemas.user import UserUpdateIn

        return UserUpdateIn(**kw)

    def test_all_none_is_valid(self):
        m = self._make()
        assert m.display_name is None

    def test_strips_control_chars_from_display_name(self):
        m = self._make(display_name="Bo\x07b")
        assert "\x07" not in m.display_name

    def test_rejects_display_name_too_long(self):
        with pytest.raises(ValidationError):
            self._make(display_name="x" * 65)

    def test_rejects_empty_display_name(self):
        with pytest.raises(ValidationError):
            self._make(display_name="")

    def test_bio_max_length_ok(self):
        m = self._make(bio="b" * 500)
        assert len(m.bio) == 500

    def test_rejects_bio_too_long(self):
        with pytest.raises(ValidationError):
            self._make(bio="b" * 501)

    def test_strips_control_chars_from_bio(self):
        m = self._make(bio="hello\x00world")
        assert "\x00" not in m.bio

    def test_rejects_invalid_academic_year(self):
        with pytest.raises(ValidationError):
            self._make(academic_year="5A")

    def test_valid_academic_years(self):
        for year in ("1A", "2A", "3A+"):
            m = self._make(academic_year=year)
            assert m.academic_year == year

    def test_none_academic_year_ok(self):
        m = self._make(academic_year=None)
        assert m.academic_year is None

    @pytest.mark.parametrize(
        "value",
        [
            "https://lh3.googleusercontent.com/photo.jpg",
            "cas/abc123",
            "materials/abc123",
            "quarantine/user/upload/avatar.png",
            "avatars/00000000-0000-0000-0000-000000000000/foreign.webp",
            "http://evil.com/track.gif",
            "javascript:alert(1)",
        ],
    )
    def test_rejects_caller_supplied_avatar_reference(self, value):
        with pytest.raises(ValidationError):
            self._make(avatar_url=value)

    def test_none_avatar_url_ok_for_clear(self):
        m = self._make(avatar_url=None)
        assert m.avatar_url is None

    def test_avatar_upload_id_accepts_uuid(self):
        import uuid

        upload_id = uuid.uuid4()
        m = self._make(avatar_upload_id=upload_id)
        assert m.avatar_upload_id == upload_id

    def test_rejects_avatar_clear_and_upload_together(self):
        import uuid

        with pytest.raises(ValidationError):
            self._make(avatar_url=None, avatar_upload_id=uuid.uuid4())


# ===========================================================================
# 4. schemas/annotation — AnnotationCreateIn, AnnotationUpdateIn
# ===========================================================================


class TestAnnotationCreateIn:
    def _make(self, **kw):
        from app.schemas.annotation import AnnotationCreateIn

        defaults = {"body": "Hello"}
        return AnnotationCreateIn(**{**defaults, **kw})

    def test_valid(self):
        m = self._make()
        assert m.body == "Hello"

    def test_strips_null_from_body(self):
        m = self._make(body="hel\x00lo")
        assert "\x00" not in m.body

    def test_rejects_empty_body(self):
        with pytest.raises(ValidationError):
            self._make(body="")

    def test_rejects_body_too_long(self):
        with pytest.raises(ValidationError):
            self._make(body="a" * 1001)

    def test_body_max_length_ok(self):
        m = self._make(body="a" * 1000)
        assert len(m.body) == 1000

    def test_selection_text_max_length_ok(self):
        m = self._make(selection_text="s" * 1000)
        assert len(m.selection_text) == 1000

    def test_rejects_selection_text_too_long(self):
        with pytest.raises(ValidationError):
            self._make(selection_text="s" * 1001)

    def test_strips_bidi_from_selection_text(self):
        m = self._make(selection_text="text\u202ehere")
        assert "\u202e" not in m.selection_text

    def test_none_selection_text_ok(self):
        m = self._make(selection_text=None)
        assert m.selection_text is None

    def test_valid_uuid_reply_to(self):
        import uuid

        uid = str(uuid.uuid4())
        m = self._make(reply_to_id=uid)
        assert m.reply_to_id == uid

    def test_rejects_non_uuid_reply_to(self):
        with pytest.raises(ValidationError):
            self._make(reply_to_id="not-a-uuid")

    def test_rejects_reply_to_too_long(self):
        with pytest.raises(ValidationError):
            self._make(reply_to_id="a" * 37)

    def test_none_reply_to_ok(self):
        m = self._make(reply_to_id=None)
        assert m.reply_to_id is None

    def test_valid_position_data(self):
        m = self._make(position_data={"x": 1, "y": 2})
        assert m.position_data is not None
        assert m.position_data.model_extra == {"x": 1, "y": 2}

    def test_rejects_position_data_too_many_keys(self):
        with pytest.raises(ValidationError):
            self._make(position_data={f"k{i}": i for i in range(21)})

    def test_position_data_at_limit_ok(self):
        m = self._make(position_data={f"k{i}": i for i in range(20)})
        assert m.position_data is not None
        assert len(m.position_data.model_extra) == 20

    def test_page_must_be_non_negative(self):
        with pytest.raises(ValidationError):
            self._make(page=-1)

    def test_page_zero_ok(self):
        m = self._make(page=0)
        assert m.page == 0

    def test_page_too_large(self):
        with pytest.raises(ValidationError):
            self._make(page=100_001)


class TestAnnotationUpdateIn:
    def _make(self, body: str):
        from app.schemas.annotation import AnnotationUpdateIn

        return AnnotationUpdateIn(body=body)

    def test_valid(self):
        m = self._make("Updated body")
        assert m.body == "Updated body"

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            self._make("")

    def test_rejects_too_long(self):
        with pytest.raises(ValidationError):
            self._make("a" * 1001)

    def test_strips_null(self):
        m = self._make("hel\x00lo")
        assert "\x00" not in m.body


# ===========================================================================
# 5. schemas/flag — FlagCreateIn
# ===========================================================================


class TestFlagCreateIn:
    def _make(self, **kw):
        import uuid

        from app.schemas.flag import FlagCreateIn

        defaults = {
            "target_type": "material",
            "target_id": uuid.uuid4(),
            "reason": "spam",
        }
        return FlagCreateIn(**{**defaults, **kw})

    def test_valid(self):
        m = self._make()
        assert m.reason == "spam"

    def test_rejects_invalid_target_type(self):
        with pytest.raises(ValidationError):
            self._make(target_type="user")

    def test_rejects_invalid_reason(self):
        with pytest.raises(ValidationError):
            self._make(reason="bad-reason")

    def test_description_max_length_ok(self):
        m = self._make(description="d" * 1000)
        assert len(m.description) == 1000

    def test_rejects_description_too_long(self):
        with pytest.raises(ValidationError):
            self._make(description="d" * 1001)

    def test_strips_null_from_description(self):
        m = self._make(description="evil\x00desc")
        assert "\x00" not in m.description

    def test_none_description_ok(self):
        m = self._make(description=None)
        assert m.description is None

    @pytest.mark.parametrize(
        "target_type", ["material", "annotation", "pull_request", "comment", "pr_comment"]
    )
    def test_all_valid_target_types(self, target_type):
        m = self._make(target_type=target_type)
        assert m.target_type == target_type

    @pytest.mark.parametrize("reason", ["inappropriate", "copyright", "spam", "incorrect", "other"])
    def test_all_valid_reasons(self, reason):
        m = self._make(reason=reason)
        assert m.reason == reason


# ===========================================================================
# 6. schemas/pull_request — PullRequestCreate, RejectRequest, MoveItemOp
# ===========================================================================


class TestPullRequestCreate:
    def _make(self, **kw):
        from app.schemas.pull_request import CreateMaterialOp, PullRequestCreate

        op = CreateMaterialOp(
            title="My Doc",
            type="document",
        )
        defaults = {"title": "My PR", "operations": [op]}
        return PullRequestCreate(**{**defaults, **kw})

    def test_valid(self):
        m = self._make()
        assert m.title == "My PR"

    def test_rejects_empty_title(self):
        with pytest.raises(ValidationError):
            self._make(title="")

    def test_rejects_title_too_short(self):
        with pytest.raises(ValidationError):
            self._make(title="ab")

    def test_rejects_title_too_long(self):
        with pytest.raises(ValidationError):
            self._make(title="a" * 301)

    def test_title_max_length_ok(self):
        m = self._make(title="a" * 300)
        assert len(m.title) == 300

    def test_strips_null_from_title(self):
        m = self._make(title="My\x00PR")
        assert "\x00" not in m.title

    def test_strips_bidi_from_title(self):
        m = self._make(title="Title\u202eHere")
        assert "\u202e" not in m.title

    def test_rejects_empty_operations(self):
        with pytest.raises(ValidationError):
            from app.schemas.pull_request import PullRequestCreate

            PullRequestCreate(title="Valid Title", operations=[])

    def test_description_max_length_ok(self):
        from app.schemas.pull_request import MAX_PR_DESCRIPTION_LENGTH

        m = self._make(description="d" * MAX_PR_DESCRIPTION_LENGTH)
        assert len(m.description) == MAX_PR_DESCRIPTION_LENGTH

    def test_rejects_description_too_long(self):
        from app.schemas.pull_request import MAX_PR_DESCRIPTION_LENGTH

        with pytest.raises(ValidationError):
            self._make(description="d" * (MAX_PR_DESCRIPTION_LENGTH + 1))

    @pytest.mark.parametrize("payload", SQL_INJECTIONS + XSS_PAYLOADS)
    def test_injection_payload_passes_through_sanitized(self, payload):
        # SQL/XSS payloads in title: if <= 300 chars and no control chars,
        # they should be stored (escaping is the ORM / template layer's job).
        # The key is they must NOT cause a crash and must not contain
        # dangerous invisible chars after sanitization.
        if len(payload) >= 3:
            m = self._make(title=payload[:300])
            for char in CONTROL_CHARS + BIDI_CHARS:
                assert char not in m.title


class TestRejectRequest:
    def _make(self, reason: str):
        from app.schemas.pull_request import RejectRequest

        return RejectRequest(reason=reason)

    def test_valid(self):
        m = self._make("This is my detailed rejection reason.")
        assert m.reason

    def test_rejects_too_short(self):
        with pytest.raises(ValidationError):
            self._make("short")

    def test_rejects_too_long(self):
        with pytest.raises(ValidationError):
            self._make("a" * 1001)

    def test_strips_null_byte(self):
        m = self._make("This is a valid\x00 rejection reason text")
        assert "\x00" not in m.reason

    def test_strips_bidi(self):
        m = self._make("Valid reason with \u202e BIDI and more text")
        assert "\u202e" not in m.reason


class TestMoveItemOp:
    def _make(self, **kw):
        import uuid

        from app.schemas.pull_request import MoveItemOp

        defaults = {
            "target_type": "material",
            "target_id": uuid.uuid4(),
            "new_parent_id": None,
        }
        return MoveItemOp(**{**defaults, **kw})

    def test_valid(self):
        m = self._make()
        assert m.target_type == "material"

    def test_rejects_target_name_too_long(self):
        with pytest.raises(ValidationError):
            self._make(target_name="n" * 129)

    def test_target_name_max_length_ok(self):
        m = self._make(target_name="n" * 128)
        assert len(m.target_name) == 128

    def test_rejects_target_title_too_long(self):
        with pytest.raises(ValidationError):
            self._make(target_title="t" * 129)

    def test_rejects_invalid_material_type(self):
        with pytest.raises(ValidationError):
            self._make(target_material_type="illegal_type")

    def test_valid_material_types(self):
        from app.schemas.pull_request import ALLOWED_MATERIAL_TYPES

        for t in ALLOWED_MATERIAL_TYPES:
            m = self._make(target_material_type=t)
            assert m.target_material_type == t

    def test_none_material_type_ok(self):
        m = self._make(target_material_type=None)
        assert m.target_material_type is None


# ===========================================================================
# 7. schemas/pull_request — op-level payloads
# ===========================================================================


class TestCreateMaterialOp:
    def _make(self, **kw):
        from app.schemas.pull_request import CreateMaterialOp

        defaults = {"title": "My Doc", "type": "document"}
        return CreateMaterialOp(**{**defaults, **kw})

    def test_valid(self):
        m = self._make()
        assert m.title == "My Doc"

    def test_rejects_empty_title(self):
        with pytest.raises(ValidationError):
            self._make(title="")

    def test_rejects_title_too_long(self):
        with pytest.raises(ValidationError):
            self._make(title="a" * 129)

    def test_title_max_ok(self):
        m = self._make(title="a" * 128)
        assert len(m.title) == 128

    def test_rejects_invalid_type(self):
        with pytest.raises(ValidationError):
            self._make(type="invalid_type")

    def test_valid_types(self):
        from app.schemas.pull_request import ALLOWED_MATERIAL_TYPES

        for t in ALLOWED_MATERIAL_TYPES:
            m = self._make(type=t)
            assert m.type == t

    def test_rejects_too_many_tags(self):
        with pytest.raises(ValidationError):
            self._make(tags=[f"tag{i}" for i in range(21)])

    def test_tags_at_limit_ok(self):
        m = self._make(tags=[f"tag{i}" for i in range(20)])
        assert len(m.tags) == 20

    def test_rejects_tag_too_long(self):
        with pytest.raises(ValidationError):
            self._make(tags=["a" * 21])

    def test_tag_max_length_ok(self):
        m = self._make(tags=["a" * 20])
        assert m.tags[0] == "a" * 20

    def test_rejects_too_many_metadata_keys(self):
        with pytest.raises(ValidationError):
            self._make(metadata={f"k{i}": i for i in range(21)})

    def test_metadata_at_limit_ok(self):
        m = self._make(metadata={f"k{i}": i for i in range(20)})
        assert len(m.metadata) == 20

    def test_rejects_file_key_traversal(self):
        with pytest.raises(ValidationError):
            self._make(file_key="../../etc/passwd")

    def test_rejects_file_key_wrong_prefix(self):
        with pytest.raises(ValidationError):
            self._make(file_key="quarantine/file.pdf")

    def test_valid_uploads_file_key(self):
        m = self._make(file_key="uploads/abc123.pdf")
        assert m.file_key == "uploads/abc123.pdf"

    def test_valid_cas_file_key(self):
        m = self._make(file_key="cas/deadbeef")
        assert m.file_key == "cas/deadbeef"

    def test_rejects_file_name_with_slash(self):
        with pytest.raises(ValidationError):
            self._make(file_name="../../etc/passwd")

    def test_rejects_file_name_too_long(self):
        with pytest.raises(ValidationError):
            self._make(file_name="a" * 256 + ".pdf")

    def test_file_name_max_length_ok(self):
        m = self._make(file_name="a" * 251 + ".pdf")
        assert m.file_name is not None

    def test_rejects_negative_file_size(self):
        with pytest.raises(ValidationError):
            self._make(file_size=-1)

    def test_zero_file_size_ok(self):
        m = self._make(file_size=0)
        assert m.file_size == 0

    def test_description_max_length_ok(self):
        m = self._make(description="d" * 1000)
        assert len(m.description) == 1000

    def test_rejects_description_too_long(self):
        with pytest.raises(ValidationError):
            self._make(description="d" * 1001)


class TestCreateDirectoryOp:
    def _make(self, **kw):
        from app.schemas.pull_request import CreateDirectoryOp

        defaults = {"name": "My Folder"}
        return CreateDirectoryOp(**{**defaults, **kw})

    def test_valid(self):
        m = self._make()
        assert m.name == "My Folder"

    def test_rejects_empty_name(self):
        with pytest.raises(ValidationError):
            self._make(name="")

    def test_rejects_name_too_long(self):
        with pytest.raises(ValidationError):
            self._make(name="n" * 129)

    def test_name_max_length_ok(self):
        m = self._make(name="n" * 128)
        assert len(m.name) == 128

    def test_rejects_invalid_directory_type(self):
        with pytest.raises(ValidationError):
            self._make(type="invalid")

    def test_valid_directory_types(self):
        from app.schemas.pull_request import ALLOWED_DIRECTORY_TYPES

        for t in ALLOWED_DIRECTORY_TYPES:
            m = self._make(type=t)
            assert m.type == t

    def test_description_max_ok(self):
        m = self._make(description="d" * 1000)
        assert len(m.description) == 1000

    def test_rejects_description_too_long(self):
        with pytest.raises(ValidationError):
            self._make(description="d" * 1001)


# ===========================================================================
# 8. schemas/material — UploadInitRequest, CheckExistsRequest, BatchStatusRequest
# ===========================================================================


class TestUploadInitRequest:
    def _make(self, **kw):
        from app.schemas.material import UploadInitRequest

        defaults = {"filename": "report.pdf", "size": 1024}
        return UploadInitRequest(**{**defaults, **kw})

    def test_valid(self):
        m = self._make()
        assert m.filename == "report.pdf"

    def test_rejects_empty_filename(self):
        with pytest.raises(ValidationError):
            self._make(filename="")

    def test_rejects_filename_too_long(self):
        with pytest.raises(ValidationError):
            self._make(filename="a" * 256)

    def test_filename_max_length_ok(self):
        m = self._make(filename="a" * 251 + ".pdf")
        assert m.filename is not None

    def test_rejects_negative_size(self):
        with pytest.raises(ValidationError):
            self._make(size=-1)

    def test_zero_size_ok(self):
        m = self._make(size=0)
        assert m.size == 0

    def test_valid_sha256(self):
        sha = "a" * 64
        m = self._make(sha256=sha)
        assert m.sha256 == sha

    def test_rejects_short_sha256(self):
        with pytest.raises(ValidationError):
            self._make(sha256="abc")

    def test_rejects_long_sha256(self):
        with pytest.raises(ValidationError):
            self._make(sha256="a" * 65)

    def test_rejects_non_hex_sha256(self):
        with pytest.raises(ValidationError):
            self._make(sha256="z" * 64)

    def test_none_sha256_ok(self):
        m = self._make(sha256=None)
        assert m.sha256 is None

    def test_mime_type_max_length_ok(self):
        m = self._make(mime_type="application/pdf")
        assert m.mime_type == "application/pdf"

    def test_rejects_mime_type_too_long(self):
        with pytest.raises(ValidationError):
            self._make(mime_type="a" * 201)


class TestCheckExistsRequest:
    def _make(self, **kw):
        from app.schemas.material import CheckExistsRequest

        defaults = {"sha256": "a" * 64, "size": 1024}
        return CheckExistsRequest(**{**defaults, **kw})

    def test_valid(self):
        m = self._make()
        assert len(m.sha256) == 64

    def test_lowercases_sha256(self):
        m = self._make(sha256="ABCDEF" + "0" * 58)
        assert m.sha256 == m.sha256.lower()

    def test_rejects_wrong_length(self):
        with pytest.raises(ValidationError):
            self._make(sha256="abc")

    def test_rejects_non_hex(self):
        with pytest.raises(ValidationError):
            self._make(sha256="z" * 64)

    def test_rejects_negative_size(self):
        with pytest.raises(ValidationError):
            self._make(size=-1)

    def test_zero_size_ok(self):
        m = self._make(size=0)
        assert m.size == 0


class TestBatchStatusRequest:
    def _make(self, file_keys: list):
        from app.schemas.material import BatchStatusRequest

        return BatchStatusRequest(file_keys=file_keys)

    def test_valid(self):
        m = self._make(["uploads/file1.pdf", "cas/abc"])
        assert len(m.file_keys) == 2

    def test_rejects_empty_list(self):
        with pytest.raises(ValidationError):
            self._make([])

    def test_rejects_list_too_long(self):
        with pytest.raises(ValidationError):
            self._make([f"uploads/f{i}.pdf" for i in range(51)])

    def test_list_at_max_ok(self):
        m = self._make([f"uploads/f{i}.pdf" for i in range(50)])
        assert len(m.file_keys) == 50

    def test_rejects_key_too_long(self):
        with pytest.raises(ValidationError):
            self._make(["uploads/" + "a" * 505])

    def test_rejects_key_with_null(self):
        with pytest.raises(ValidationError):
            self._make(["uploads/file\x00.pdf"])

    def test_rejects_key_with_traversal(self):
        with pytest.raises(ValidationError):
            self._make(["uploads/../etc/passwd"])


# ===========================================================================
# 9. schemas/comment — CommentCreateIn, CommentUpdateIn
# ===========================================================================


class TestCommentCreateIn:
    def _make(self, **kw):
        from app.schemas.comment import CommentCreateIn

        defaults = {"target_type": "material", "target_id": "some-id", "body": "Hello!"}
        return CommentCreateIn(**{**defaults, **kw})

    def test_valid(self):
        m = self._make()
        assert m.body == "Hello!"

    def test_rejects_invalid_target_type(self):
        with pytest.raises(ValidationError):
            self._make(target_type="user")

    def test_rejects_empty_body(self):
        with pytest.raises(ValidationError):
            self._make(body="")

    def test_rejects_body_too_long(self):
        with pytest.raises(ValidationError):
            self._make(body="a" * 1001)

    def test_body_max_length_ok(self):
        m = self._make(body="a" * 1000)
        assert len(m.body) == 1000

    def test_strips_null_from_body(self):
        m = self._make(body="hel\x00lo")
        assert "\x00" not in m.body

    def test_strips_bidi_from_body(self):
        m = self._make(body="text\u202emore text")
        assert "\u202e" not in m.body

    def test_preserves_newlines_in_body(self):
        m = self._make(body="line1\nline2\nline3")
        assert "\n" in m.body

    @pytest.mark.parametrize("payload", SQL_INJECTIONS)
    def test_sql_payload_passes_as_text(self, payload):
        # SQL payloads are valid text — escaping is the ORM's job
        m = self._make(body=payload[:1000])
        for char in CONTROL_CHARS + BIDI_CHARS:
            assert char not in m.body


class TestCommentUpdateIn:
    def _make(self, body: str):
        from app.schemas.comment import CommentUpdateIn

        return CommentUpdateIn(body=body)

    def test_valid(self):
        m = self._make("Updated comment")
        assert m.body == "Updated comment"

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            self._make("")

    def test_rejects_too_long(self):
        with pytest.raises(ValidationError):
            self._make("a" * 1001)

    def test_strips_null(self):
        m = self._make("hel\x00lo world")
        assert "\x00" not in m.body


# ===========================================================================
# 10. schemas/pull_request — PRCommentCreate
# ===========================================================================


class TestPRCommentCreate:
    def _make(self, **kw):
        from app.schemas.pull_request import PRCommentCreate

        defaults = {"body": "A comment"}
        return PRCommentCreate(**{**defaults, **kw})

    def test_valid(self):
        m = self._make()
        assert m.body == "A comment"

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            self._make(body="")

    def test_rejects_too_long(self):
        with pytest.raises(ValidationError):
            self._make(body="a" * 1001)

    def test_body_max_length_ok(self):
        m = self._make(body="a" * 1000)
        assert len(m.body) == 1000

    def test_strips_null(self):
        m = self._make(body="hel\x00lo")
        assert "\x00" not in m.body

    def test_strips_bidi(self):
        m = self._make(body="hello\u202eworld")
        assert "\u202e" not in m.body

    def test_preserves_newlines(self):
        m = self._make(body="line1\nline2")
        assert "\n" in m.body


# ===========================================================================
# 11. Sanitization — strip_null_chars recursive helper
# ===========================================================================


class TestStripNullChars:
    def setup_method(self):
        from app.core.sanitization import strip_null_chars

        self.fn = strip_null_chars

    def test_string_null(self):
        assert "\x00" not in self.fn("hel\x00lo")

    def test_string_control_char(self):
        assert "\x07" not in self.fn("bel\x07l")

    def test_nested_list(self):
        result = self.fn(["hel\x00lo", "wor\x07ld"])
        assert "\x00" not in result[0]
        assert "\x07" not in result[1]

    def test_nested_dict(self):
        result = self.fn({"key": "val\x00ue"})
        assert "\x00" not in result["key"]

    def test_deeply_nested(self):
        result = self.fn({"a": ["b\x00", {"c": "d\x01"}]})
        assert "\x00" not in result["a"][0]
        assert "\x01" not in result["a"][1]["c"]

    def test_non_string_passthrough(self):
        assert self.fn(42) == 42
        assert self.fn(None) is None
        assert self.fn(3.14) == 3.14

    def test_bidi_stripped_from_nested(self):
        result = self.fn({"text": "hello\u202eworld"})
        assert "\u202e" not in result["text"]


# ===========================================================================
# 12. Boundary / edge-case matrix
# ===========================================================================


class TestBoundaryMatrix:
    """Systematic boundary tests: at-limit passes, over-limit fails."""

    @pytest.mark.parametrize(
        "schema_fn,field,max_len,make_kw",
        [
            # display_name: max 64
            (
                lambda kw: __import__("app.schemas.user", fromlist=["OnboardIn"]).OnboardIn(**kw),
                "display_name",
                64,
                {"academic_year": "1A", "gdpr_consent": True},
            ),
            # bio: max 500
            (
                lambda kw: __import__("app.schemas.user", fromlist=["UserUpdateIn"]).UserUpdateIn(
                    **kw
                ),
                "bio",
                500,
                {},
            ),
            # PR title: max 300 (min 3)
            (
                lambda kw: __import__(
                    "app.schemas.pull_request", fromlist=["PullRequestCreate"]
                ).PullRequestCreate(
                    operations=[
                        __import__(
                            "app.schemas.pull_request", fromlist=["CreateMaterialOp"]
                        ).CreateMaterialOp(title="Doc", type="document")
                    ],
                    **kw,
                ),
                "title",
                300,
                {},
            ),
        ],
    )
    def test_at_max_passes(self, schema_fn, field, max_len, make_kw):
        kw = {**make_kw, field: "a" * max_len}
        obj = schema_fn(kw)
        assert len(getattr(obj, field)) == max_len

    @pytest.mark.parametrize(
        "schema_fn,field,max_len,make_kw",
        [
            (
                lambda kw: __import__("app.schemas.user", fromlist=["OnboardIn"]).OnboardIn(**kw),
                "display_name",
                64,
                {"academic_year": "1A", "gdpr_consent": True},
            ),
            (
                lambda kw: __import__("app.schemas.user", fromlist=["UserUpdateIn"]).UserUpdateIn(
                    **kw
                ),
                "bio",
                500,
                {},
            ),
        ],
    )
    def test_over_max_fails(self, schema_fn, field, max_len, make_kw):
        kw = {**make_kw, field: "a" * (max_len + 1)}
        with pytest.raises(ValidationError):
            schema_fn(kw)
