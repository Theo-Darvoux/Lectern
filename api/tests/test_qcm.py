"""Tests for the QCM router: validation, stage, and parse-moodle endpoints."""

from __future__ import annotations

import uuid
from io import BytesIO
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.user import User, UserRole
from app.routers.qcm import (
    QCM_MAX_QUESTIONS,
    _parse_moodle_xml,
    _validate_qcm_structure,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_user(role: UserRole = UserRole.STUDENT) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"user_{uuid.uuid4().hex[:6]}@test.com",
        display_name="Test User",
        role=role,
        onboarded=True,
        gdpr_consent=True,
    )


def _auth(user: User) -> dict[str, str]:
    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


def _minimal_qcm(**overrides: Any) -> dict[str, Any]:
    """Return the smallest valid QCM structure."""
    base: dict[str, Any] = {
        "version": 1,
        "chapters": [
            {
                "id": "ch1",
                "title": "Chapter 1",
                "questions": [
                    {
                        "id": "q1",
                        "text": "What is 2+2?",
                        "answers": [
                            {"id": "a1", "text": "4", "correct": True},
                            {"id": "a2", "text": "5", "correct": False},
                        ],
                    }
                ],
            }
        ],
    }
    base.update(overrides)
    return base


def _moodle_xml(questions: list[dict[str, str]]) -> bytes:
    """Build a minimal Moodle XML quiz export from a list of question dicts."""
    q_blocks = []
    for q in questions:
        q_type = q.get("type", "multichoice")
        name = q.get("name", "Question: text")
        text = q.get("text", "What is this?")
        answers = q.get(
            "answers",
            '<answer fraction="100"><text>Correct</text></answer><answer fraction="0"><text>Wrong</text></answer>',
        )
        feedback = q.get("feedback", "")
        feedback_block = (
            f"<generalfeedback format='html'><text>{feedback}</text></generalfeedback>"
            if feedback
            else ""
        )
        q_blocks.append(
            f"""<question type="{q_type}">
  <name><text>{name}</text></name>
  <questiontext format="html"><text>{text}</text></questiontext>
  {feedback_block}
  {answers}
</question>"""
        )
    return ("<?xml version='1.0' encoding='UTF-8'?><quiz>" + "".join(q_blocks) + "</quiz>").encode()


# ── _validate_qcm_structure unit tests ───────────────────────────────────────


class TestValidateQCMStructure:
    def test_valid_minimal(self):
        _validate_qcm_structure(_minimal_qcm())  # no exception

    def test_wrong_version(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _validate_qcm_structure(_minimal_qcm(version=2))
        assert exc.value.status_code == 422

    def test_missing_chapters(self):
        from fastapi import HTTPException

        data = {"version": 1}
        with pytest.raises(HTTPException) as exc:
            _validate_qcm_structure(data)
        assert exc.value.status_code == 422

    def test_chapters_not_list(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _validate_qcm_structure({"version": 1, "chapters": "bad"})

    def test_chapter_missing_id(self):
        from fastapi import HTTPException

        qcm = _minimal_qcm()
        del qcm["chapters"][0]["id"]
        with pytest.raises(HTTPException):
            _validate_qcm_structure(qcm)

    def test_chapter_missing_title(self):
        from fastapi import HTTPException

        qcm = _minimal_qcm()
        del qcm["chapters"][0]["title"]
        with pytest.raises(HTTPException):
            _validate_qcm_structure(qcm)

    def test_question_missing_id(self):
        from fastapi import HTTPException

        qcm = _minimal_qcm()
        del qcm["chapters"][0]["questions"][0]["id"]
        with pytest.raises(HTTPException):
            _validate_qcm_structure(qcm)

    def test_question_missing_text(self):
        from fastapi import HTTPException

        qcm = _minimal_qcm()
        del qcm["chapters"][0]["questions"][0]["text"]
        with pytest.raises(HTTPException):
            _validate_qcm_structure(qcm)

    def test_answer_zero_not_allowed(self):
        from fastapi import HTTPException

        qcm = _minimal_qcm()
        qcm["chapters"][0]["questions"][0]["answers"] = []
        with pytest.raises(HTTPException):
            _validate_qcm_structure(qcm)

    def test_exceeds_max_answers(self):
        from fastapi import HTTPException

        from app.routers.qcm import QCM_MAX_ANSWERS_PER_QUESTION

        qcm = _minimal_qcm()
        qcm["chapters"][0]["questions"][0]["answers"] = [
            {"id": f"a{i}", "text": f"Answer {i}", "correct": False}
            for i in range(QCM_MAX_ANSWERS_PER_QUESTION + 1)
        ]
        with pytest.raises(HTTPException):
            _validate_qcm_structure(qcm)

    def test_max_answers_exactly_allowed(self):
        from app.routers.qcm import QCM_MAX_ANSWERS_PER_QUESTION

        qcm = _minimal_qcm()
        qcm["chapters"][0]["questions"][0]["answers"] = [
            {"id": f"a{i}", "text": f"Answer {i}", "correct": i == 0}
            for i in range(QCM_MAX_ANSWERS_PER_QUESTION)
        ]
        _validate_qcm_structure(qcm)  # no exception

    def test_answer_missing_correct_field(self):
        from fastapi import HTTPException

        qcm = _minimal_qcm()
        del qcm["chapters"][0]["questions"][0]["answers"][0]["correct"]
        with pytest.raises(HTTPException):
            _validate_qcm_structure(qcm)

    def test_answer_correct_not_bool(self):
        from fastapi import HTTPException

        qcm = _minimal_qcm()
        qcm["chapters"][0]["questions"][0]["answers"][0]["correct"] = 1  # int, not bool
        with pytest.raises(HTTPException):
            _validate_qcm_structure(qcm)

    def test_exceeds_max_questions(self):
        from fastapi import HTTPException

        questions = [
            {
                "id": f"q{i}",
                "text": f"Question {i}",
                "answers": [{"id": f"a{i}", "text": "ans", "correct": True}],
            }
            for i in range(QCM_MAX_QUESTIONS + 1)
        ]
        qcm = _minimal_qcm()
        qcm["chapters"][0]["questions"] = questions
        with pytest.raises(HTTPException) as exc:
            _validate_qcm_structure(qcm)
        assert exc.value.status_code == 422
        assert "maximum" in exc.value.detail.lower()

    def test_max_questions_exactly_allowed(self):
        questions = [
            {
                "id": f"q{i}",
                "text": f"Question {i}",
                "answers": [{"id": f"a{i}", "text": "ans", "correct": True}],
            }
            for i in range(QCM_MAX_QUESTIONS)
        ]
        qcm = _minimal_qcm()
        qcm["chapters"][0]["questions"] = questions
        _validate_qcm_structure(qcm)  # no exception

    def test_multiple_chapters(self):
        qcm = _minimal_qcm()
        qcm["chapters"].append(
            {
                "id": "ch2",
                "title": "Chapter 2",
                "questions": [
                    {
                        "id": "q2",
                        "text": "Another?",
                        "answers": [{"id": "b1", "text": "Yes", "correct": True}],
                    }
                ],
            }
        )
        _validate_qcm_structure(qcm)  # no exception

    def test_optional_explanation_is_ignored(self):
        """explanation field is not required and should not cause validation failure."""
        qcm = _minimal_qcm()
        qcm["chapters"][0]["questions"][0]["explanation"] = "Some explanation"
        _validate_qcm_structure(qcm)  # no exception


# ── _parse_moodle_xml unit tests ──────────────────────────────────────────────


class TestParseMoodleXML:
    def test_basic_multichoice(self):
        xml = _moodle_xml(
            [{"name": "Maths: What is 2+2?", "text": "What is 2+2?", "type": "multichoice"}]
        )
        result = _parse_moodle_xml(xml)
        assert result["version"] == 1
        assert len(result["chapters"]) == 1
        assert result["chapters"][0]["title"] == "Maths"
        assert len(result["chapters"][0]["questions"]) == 1
        q = result["chapters"][0]["questions"][0]
        assert q["text"] == "What is 2+2?"

    def test_truefalse_question_type(self):
        xml = _moodle_xml(
            [{"type": "truefalse", "name": "Q: Is sky blue?", "text": "Is the sky blue?"}]
        )
        result = _parse_moodle_xml(xml)
        assert len(result["chapters"]) == 1

    def test_unsupported_type_skipped(self):
        xml = _moodle_xml(
            [
                {"type": "essay", "name": "Essay question"},
                {"type": "multichoice", "name": "Chapter: Real Q", "text": "Real Q?"},
            ]
        )
        result = _parse_moodle_xml(xml)
        total_questions = sum(len(ch["questions"]) for ch in result["chapters"])
        assert total_questions == 1

    def test_default_chapter_when_no_prefix(self):
        xml = _moodle_xml([{"name": "NoPrefix question", "text": "A question?"}])
        result = _parse_moodle_xml(xml)
        assert result["chapters"][0]["title"] == "Questions"

    def test_chapter_grouping(self):
        xml = _moodle_xml(
            [
                {"name": "Math: Q1", "text": "Math Q1?"},
                {"name": "Math: Q2", "text": "Math Q2?"},
                {"name": "Science: Q1", "text": "Science Q1?"},
            ]
        )
        result = _parse_moodle_xml(xml)
        titles = [ch["title"] for ch in result["chapters"]]
        assert "Math" in titles
        assert "Science" in titles
        math_ch = next(ch for ch in result["chapters"] if ch["title"] == "Math")
        assert len(math_ch["questions"]) == 2

    def test_html_stripped_from_text(self):
        # Real Moodle XML escapes HTML inside <text> as character entities.
        # Build the XML manually so the content is text, not child elements.
        xml = (
            b"<?xml version='1.0'?><quiz>"
            b"<question type='multichoice'>"
            b"<name><text>Test: Q</text></name>"
            b"<questiontext format='html'>"
            b"<text>&lt;p&gt;Bold &lt;strong&gt;answer&lt;/strong&gt; here&lt;/p&gt;</text>"
            b"</questiontext>"
            b"<answer fraction='100'><text>Correct</text></answer>"
            b"</question></quiz>"
        )
        result = _parse_moodle_xml(xml)
        q_text = result["chapters"][0]["questions"][0]["text"]
        assert "<" not in q_text
        assert "Bold" in q_text

    def test_answers_have_correct_flag(self):
        answers_xml = (
            '<answer fraction="100"><text>Right</text></answer>'
            '<answer fraction="0"><text>Wrong</text></answer>'
        )
        xml = _moodle_xml([{"name": "Test: Q", "text": "Pick one?", "answers": answers_xml}])
        result = _parse_moodle_xml(xml)
        answers = result["chapters"][0]["questions"][0]["answers"]
        correct = [a for a in answers if a["correct"]]
        wrong = [a for a in answers if not a["correct"]]
        assert len(correct) == 1
        assert len(wrong) == 1
        assert correct[0]["text"] == "Right"

    def test_empty_answer_text_skipped(self):
        answers_xml = (
            '<answer fraction="100"><text></text></answer>'
            '<answer fraction="0"><text>Valid answer</text></answer>'
        )
        xml = _moodle_xml([{"name": "Test: Q", "text": "Q?", "answers": answers_xml}])
        result = _parse_moodle_xml(xml)
        answers = result["chapters"][0]["questions"][0]["answers"]
        assert len(answers) == 1
        assert answers[0]["text"] == "Valid answer"

    def test_explanation_extracted_from_feedback(self):
        xml = _moodle_xml([{"name": "Test: Q", "text": "Q?", "feedback": "This is why."}])
        result = _parse_moodle_xml(xml)
        q = result["chapters"][0]["questions"][0]
        assert q.get("explanation") == "This is why."

    def test_no_feedback_no_explanation_field(self):
        xml = _moodle_xml([{"name": "Test: Q", "text": "Q?"}])
        result = _parse_moodle_xml(xml)
        q = result["chapters"][0]["questions"][0]
        assert "explanation" not in q

    def test_max_answers_per_question(self):
        from app.routers.qcm import QCM_MAX_ANSWERS_PER_QUESTION

        answers_xml = "".join(
            f'<answer fraction="0"><text>Answer {i}</text></answer>'
            for i in range(QCM_MAX_ANSWERS_PER_QUESTION + 5)
        )
        xml = _moodle_xml([{"name": "Test: Q", "text": "Q?", "answers": answers_xml}])
        result = _parse_moodle_xml(xml)
        answers = result["chapters"][0]["questions"][0]["answers"]
        assert len(answers) <= QCM_MAX_ANSWERS_PER_QUESTION

    def test_invalid_xml_raises_422(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _parse_moodle_xml(b"this is not xml <<< >>>")
        assert exc.value.status_code == 422

    def test_empty_quiz_returns_empty_chapter(self):
        xml = b"<?xml version='1.0'?><quiz></quiz>"
        result = _parse_moodle_xml(xml)
        assert result["version"] == 1
        assert len(result["chapters"]) == 1
        assert result["chapters"][0]["questions"] == []

    def test_question_ids_are_unique_uuids(self):
        xml = _moodle_xml(
            [
                {"name": "Ch: Q1", "text": "Q1?"},
                {"name": "Ch: Q2", "text": "Q2?"},
            ]
        )
        result = _parse_moodle_xml(xml)
        all_q_ids = [q["id"] for ch in result["chapters"] for q in ch["questions"]]
        assert len(set(all_q_ids)) == len(all_q_ids)

    def test_answer_ids_are_unique_uuids(self):
        xml = _moodle_xml([{"name": "Ch: Q", "text": "Q?"}])
        result = _parse_moodle_xml(xml)
        answers = result["chapters"][0]["questions"][0]["answers"]
        ids = [a["id"] for a in answers]
        assert len(set(ids)) == len(ids)


# ── /api/qcm/stage endpoint tests ────────────────────────────────────────────


@pytest.fixture
def user() -> User:
    return _make_user()


@pytest.mark.asyncio
class TestStageEndpoint:
    async def test_stage_valid_qcm(
        self, client: AsyncClient, db_session: AsyncSession, fake_redis_setup
    ):
        user = _make_user()
        db_session.add(user)
        await db_session.flush()

        with (
            patch("app.routers.qcm.storage_upload_file", new_callable=AsyncMock) as mock_upload,
            patch("app.routers.qcm.increment_cas_ref", new_callable=AsyncMock),
        ):
            mock_upload.return_value = None
            resp = await client.post(
                "/api/qcm/stage",
                json={"data": _minimal_qcm()},
                headers=_auth(user),
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "file_key" in body
        assert body["file_key"].startswith("cas/")
        assert len(body["sha256"]) == 64
        assert body["file_size"] > 0

    async def test_stage_requires_auth(self, client: AsyncClient):
        resp = await client.post("/api/qcm/stage", json={"data": _minimal_qcm()})
        assert resp.status_code == 401

    async def test_stage_invalid_version_returns_422(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = _make_user()
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            "/api/qcm/stage",
            json={"data": _minimal_qcm(version=99)},
            headers=_auth(user),
        )
        assert resp.status_code == 422

    async def test_stage_same_content_same_key(
        self, client: AsyncClient, db_session: AsyncSession, fake_redis_setup
    ):
        user = _make_user()
        db_session.add(user)
        await db_session.flush()

        qcm = _minimal_qcm()
        with (
            patch("app.routers.qcm.storage_upload_file", new_callable=AsyncMock),
            patch("app.routers.qcm.increment_cas_ref", new_callable=AsyncMock),
        ):
            r1 = await client.post("/api/qcm/stage", json={"data": qcm}, headers=_auth(user))
            r2 = await client.post("/api/qcm/stage", json={"data": qcm}, headers=_auth(user))

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["file_key"] == r2.json()["file_key"]
        assert r1.json()["sha256"] == r2.json()["sha256"]

    async def test_stage_different_content_different_key(
        self, client: AsyncClient, db_session: AsyncSession, fake_redis_setup
    ):
        user = _make_user()
        db_session.add(user)
        await db_session.flush()

        qcm_a = _minimal_qcm()
        qcm_b = _minimal_qcm()
        qcm_b["chapters"][0]["title"] = "Different Chapter Title"

        with (
            patch("app.routers.qcm.storage_upload_file", new_callable=AsyncMock),
            patch("app.routers.qcm.increment_cas_ref", new_callable=AsyncMock),
        ):
            r1 = await client.post("/api/qcm/stage", json={"data": qcm_a}, headers=_auth(user))
            r2 = await client.post("/api/qcm/stage", json={"data": qcm_b}, headers=_auth(user))

        assert r1.json()["sha256"] != r2.json()["sha256"]
        assert r1.json()["file_key"] != r2.json()["file_key"]

    async def test_stage_too_many_questions(self, client: AsyncClient, db_session: AsyncSession):
        user = _make_user()
        db_session.add(user)
        await db_session.flush()

        questions = [
            {
                "id": f"q{i}",
                "text": f"Q{i}",
                "answers": [{"id": f"a{i}", "text": "A", "correct": True}],
            }
            for i in range(QCM_MAX_QUESTIONS + 1)
        ]
        qcm = _minimal_qcm()
        qcm["chapters"][0]["questions"] = questions

        resp = await client.post(
            "/api/qcm/stage",
            json={"data": qcm},
            headers=_auth(user),
        )
        assert resp.status_code == 422

    async def test_stage_uploads_to_storage(
        self, client: AsyncClient, db_session: AsyncSession, fake_redis_setup
    ):
        user = _make_user()
        db_session.add(user)
        await db_session.flush()

        with (
            patch("app.routers.qcm.storage_upload_file", new_callable=AsyncMock) as mock_upload,
            patch("app.routers.qcm.increment_cas_ref", new_callable=AsyncMock),
        ):
            mock_upload.return_value = None
            resp = await client.post(
                "/api/qcm/stage",
                json={"data": _minimal_qcm()},
                headers=_auth(user),
            )

        assert resp.status_code == 200
        assert mock_upload.call_count == 1
        call_kwargs = mock_upload.call_args
        assert call_kwargs[0][1].startswith("cas/")  # file_key positional arg

    async def test_stage_increments_cas_ref(
        self, client: AsyncClient, db_session: AsyncSession, fake_redis_setup
    ):
        user = _make_user()
        db_session.add(user)
        await db_session.flush()

        with (
            patch("app.routers.qcm.storage_upload_file", new_callable=AsyncMock),
            patch("app.routers.qcm.increment_cas_ref", new_callable=AsyncMock) as mock_cas,
        ):
            resp = await client.post(
                "/api/qcm/stage",
                json={"data": _minimal_qcm()},
                headers=_auth(user),
            )

        assert resp.status_code == 200
        assert mock_cas.call_count == 1
        # Verify initial_data contains expected fields
        _, kwargs = mock_cas.call_args
        initial = kwargs.get("initial_data", {}) or mock_cas.call_args[0][2]
        assert initial.get("mime_type") == "application/vnd.wikint.qcm+json"
        assert initial.get("file_name") == "qcm.qcm"


# ── /api/qcm/parse-moodle endpoint tests ─────────────────────────────────────


@pytest.mark.asyncio
class TestParseMoodleEndpoint:
    def _xml_file(self, content: bytes, filename: str = "quiz.xml"):
        return {"file": (filename, BytesIO(content), "text/xml")}

    async def test_parse_moodle_valid(self, client: AsyncClient, db_session: AsyncSession):
        user = _make_user()
        db_session.add(user)
        await db_session.flush()

        xml = _moodle_xml([{"name": "Math: Q1", "text": "What is 2+2?"}])
        resp = await client.post(
            "/api/qcm/parse-moodle",
            files=self._xml_file(xml),
            headers=_auth(user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == 1
        assert len(body["chapters"]) >= 1

    async def test_parse_moodle_requires_auth(self, client: AsyncClient):
        xml = _moodle_xml([{"name": "Test: Q", "text": "Q?"}])
        resp = await client.post("/api/qcm/parse-moodle", files=self._xml_file(xml))
        assert resp.status_code == 401

    async def test_parse_moodle_rejects_non_xml(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = _make_user()
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            "/api/qcm/parse-moodle",
            files={"file": ("quiz.pdf", BytesIO(b"%PDF-1.4"), "application/pdf")},
            headers=_auth(user),
        )
        assert resp.status_code == 422

    async def test_parse_moodle_rejects_oversized_file(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = _make_user()
        db_session.add(user)
        await db_session.flush()

        # 11 MB of data
        big_xml = b"<?xml version='1.0'?><quiz>" + b"x" * (11 * 1024 * 1024) + b"</quiz>"
        resp = await client.post(
            "/api/qcm/parse-moodle",
            files=self._xml_file(big_xml),
            headers=_auth(user),
        )
        assert resp.status_code == 413

    async def test_parse_moodle_invalid_xml_returns_422(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = _make_user()
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            "/api/qcm/parse-moodle",
            files=self._xml_file(b"not valid xml <<", "broken.xml"),
            headers=_auth(user),
        )
        assert resp.status_code == 422

    async def test_parse_moodle_returns_valid_qcm_structure(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = _make_user()
        db_session.add(user)
        await db_session.flush()

        xml = _moodle_xml(
            [
                {"name": "Physics: Newton's law", "text": "What is F=ma?"},
                {"name": "Physics: Gravity", "text": "g = ?"},
            ]
        )
        resp = await client.post(
            "/api/qcm/parse-moodle",
            files=self._xml_file(xml),
            headers=_auth(user),
        )
        assert resp.status_code == 200
        body = resp.json()
        # Should pass our own QCM validator (result is stageable as-is)
        _validate_qcm_structure(body)


# ── MIME / extension integration ─────────────────────────────────────────────


class TestQCMMimeType:
    def test_qcm_extension_in_allowed_extensions(self):
        from app.core.mimetypes import ALLOWED_EXTENSIONS

        assert ".qcm" in ALLOWED_EXTENSIONS

    def test_qcm_mime_in_allowed_mime_types(self):
        from app.core.mimetypes import ALLOWED_MIME_TYPES

        assert "application/vnd.wikint.qcm+json" in ALLOWED_MIME_TYPES

    def test_qcm_extension_mapping(self):
        from app.core.mimetypes import EXTENSION_MAPPING

        assert "application/vnd.wikint.qcm+json" in EXTENSION_MAPPING.get(".qcm", [])

    def test_qcm_mime_to_extension(self):
        from app.core.mimetypes import MIME_TO_EXTENSION

        assert MIME_TO_EXTENSION["application/vnd.wikint.qcm+json"] == ".qcm"

    def test_mime_registry_is_supported_qcm(self):
        from app.core.mimetypes import MimeRegistry

        assert MimeRegistry.is_supported_extension(".qcm") is True

    def test_mime_registry_allowed_mime_qcm(self):
        from app.core.mimetypes import MimeRegistry

        assert MimeRegistry.is_allowed_mime("application/vnd.wikint.qcm+json") is True


# ── Schema: qcm in ALLOWED_MATERIAL_TYPES ────────────────────────────────────


class TestQCMMaterialType:
    def test_qcm_in_allowed_material_types(self):
        from app.schemas.pull_request import ALLOWED_MATERIAL_TYPES

        assert "qcm" in ALLOWED_MATERIAL_TYPES
