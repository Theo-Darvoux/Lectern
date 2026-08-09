"""QCM (Multiple Choice Questions) API router.

Provides endpoints:
  POST /api/qcm/stage              – Validate + store a QCM JSON file in the CAS.
  POST /api/qcm/parse-moodle       – Convert a Moodle XML quiz file into QCM JSON.
  GET  /api/qcm/export-moodle/{id} – Export QCM as Moodle-compatible XML.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Annotated, Any
from xml.etree.ElementTree import Element

from defusedxml import ElementTree
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database.database import get_db
from app.core.database.post_commit import register_transaction_callbacks
from app.core.database.redis import redis_client
from app.core.media.mimetypes import QCM_MIME_TYPE
from app.core.security.cas import (
    CasReferenceMissingError,
    compensate_cas_increment,
    hmac_cas_key,
    increment_cas_ref,
)
from app.core.storage.facade import read_full_object
from app.core.storage.facade import upload_file as storage_upload_file
from app.dependencies.auth import CurrentUser
from app.models.cas_staging_claim import CasStagingClaim
from app.services.material import get_material_with_version

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/qcm", tags=["qcm"])

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

QCM_MAX_QUESTIONS: int = int(os.environ.get("QCM_MAX_QUESTIONS", "100"))
QCM_MAX_ANSWERS_PER_QUESTION: int = int(os.environ.get("QCM_MAX_ANSWERS_PER_QUESTION", "10"))
# Embedded image limits (kept in sync with the web client's qcm-types constants).
QCM_MAX_IMAGES: int = int(os.environ.get("QCM_MAX_IMAGES", "30"))
QCM_MAX_IMAGE_CHARS: int = int(os.environ.get("QCM_MAX_IMAGE_CHARS", "500000"))
QCM_MAX_BYTES = 20 * 1024 * 1024
MOODLE_XML_MAX_BYTES = 10 * 1024 * 1024

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_qcm_structure(data: dict[str, Any]) -> None:
    """Raise HTTPException 422 if the QCM structure is invalid."""
    if data.get("version") != 1:
        raise HTTPException(status_code=422, detail="QCM version must be 1")

    chapters = data.get("chapters")
    if not isinstance(chapters, list):
        raise HTTPException(status_code=422, detail="QCM must have a 'chapters' list")

    # Optional embedded image store: {id: data-url}
    images = data.get("images")
    if images is not None:
        if not isinstance(images, dict):
            raise HTTPException(status_code=422, detail="QCM 'images' must be an object")
        if len(images) > QCM_MAX_IMAGES:
            raise HTTPException(
                status_code=422,
                detail=f"QCM exceeds maximum of {QCM_MAX_IMAGES} images",
            )
        for key, val in images.items():
            if not isinstance(key, str) or not key:
                raise HTTPException(
                    status_code=422, detail="QCM image keys must be non-empty strings"
                )
            if not isinstance(val, str) or not val.startswith("data:image/"):
                raise HTTPException(
                    status_code=422, detail="QCM image values must be image data URLs"
                )
            if len(val) > QCM_MAX_IMAGE_CHARS:
                raise HTTPException(
                    status_code=422, detail="QCM image exceeds the maximum allowed size"
                )

    total_questions = 0

    for ci, chapter in enumerate(chapters):
        if not isinstance(chapter, dict):
            raise HTTPException(status_code=422, detail=f"Chapter {ci} must be an object")
        if not isinstance(chapter.get("id"), str) or not chapter["id"]:
            raise HTTPException(status_code=422, detail=f"Chapter {ci} missing 'id'")
        if not isinstance(chapter.get("title"), str):
            raise HTTPException(status_code=422, detail=f"Chapter {ci} missing 'title'")

        questions = chapter.get("questions")
        if not isinstance(questions, list):
            raise HTTPException(
                status_code=422, detail=f"Chapter {ci} must have a 'questions' list"
            )

        for qi, question in enumerate(questions):
            if not isinstance(question, dict):
                raise HTTPException(
                    status_code=422, detail=f"Question {qi} in chapter {ci} must be an object"
                )
            if not isinstance(question.get("id"), str) or not question["id"]:
                raise HTTPException(
                    status_code=422, detail=f"Question {qi} in chapter {ci} missing 'id'"
                )
            if not isinstance(question.get("text"), str):
                raise HTTPException(
                    status_code=422, detail=f"Question {qi} in chapter {ci} missing 'text'"
                )

            answers = question.get("answers")
            if (
                not isinstance(answers, list)
                or len(answers) < 1
                or len(answers) > QCM_MAX_ANSWERS_PER_QUESTION
            ):
                raise HTTPException(
                    status_code=422,
                    detail=f"Question {qi} in chapter {ci} must have 1–{QCM_MAX_ANSWERS_PER_QUESTION} answers",
                )

            for ai, answer in enumerate(answers):
                if not isinstance(answer, dict):
                    raise HTTPException(
                        status_code=422,
                        detail=f"Answer {ai} of question {qi} in chapter {ci} must be an object",
                    )
                if not isinstance(answer.get("id"), str) or not answer["id"]:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Answer {ai} of question {qi} in chapter {ci} missing 'id'",
                    )
                if not isinstance(answer.get("text"), str):
                    raise HTTPException(
                        status_code=422,
                        detail=f"Answer {ai} of question {qi} in chapter {ci} missing 'text'",
                    )
                if not isinstance(answer.get("correct"), bool):
                    raise HTTPException(
                        status_code=422,
                        detail=f"Answer {ai} of question {qi} in chapter {ci} 'correct' must be bool",
                    )

            total_questions += 1

    if total_questions > QCM_MAX_QUESTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"QCM exceeds maximum of {QCM_MAX_QUESTIONS} questions",
        )


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class QCMStageRequest(BaseModel):
    data: dict[str, Any]


class QCMStageResponse(BaseModel):
    file_key: str
    sha256: str
    file_size: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/limits")
async def get_qcm_limits() -> dict[str, Any]:
    return {
        "max_questions_per_qcm": QCM_MAX_QUESTIONS,
        "max_answers_per_question": QCM_MAX_ANSWERS_PER_QUESTION,
    }


@router.post("/stage", response_model=QCMStageResponse)
async def stage_qcm(
    body: QCMStageRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> QCMStageResponse:
    """Validate a QCM JSON structure, write it to the CAS, and return the file key.

    The returned ``file_key`` and ``sha256`` can be passed directly into a
    ``create_material`` or ``edit_material`` PR operation.
    """
    _validate_qcm_structure(body.data)

    # Compact, deterministic JSON serialisation
    data_bytes: bytes = json.dumps(body.data, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    sha256 = hashlib.sha256(data_bytes).hexdigest()
    file_size = len(data_bytes)
    if file_size > QCM_MAX_BYTES:
        raise HTTPException(status_code=413, detail="QCM is too large (max 20 MiB)")

    # Derive the CAS S3 key from the HMAC
    cas_redis_key = hmac_cas_key(sha256)
    hmac_digest = cas_redis_key.split(":")[-1]
    file_key = f"cas/{hmac_digest}"

    claim_id = uuid.uuid4()
    increment_operation_id = f"qcm-stage:{claim_id}"
    reference_attempted = False

    async def rollback_stage() -> None:
        if not reference_attempted:
            return
        try:
            await compensate_cas_increment(
                redis_client,
                sha256,
                operation_id=increment_operation_id,
            )
        except CasReferenceMissingError:
            # A failed increment that never reached Redis has nothing to undo.
            # Physical CAS deletion is deliberately left to safe GC because
            # the same content key may be referenced by another request.
            logger.info("QCM rollback found no CAS reference for claim %s", claim_id)

    async def finalize_stage() -> None:
        return None

    if not register_transaction_callbacks(
        db,
        on_rollback=rollback_stage,
        on_commit=finalize_stage,
    ):
        raise RuntimeError("QCM staging requires a request-managed transaction")

    # Write to object storage (idempotent — same content → same key)
    await storage_upload_file(
        data_bytes,
        file_key,
        content_type=QCM_MIME_TYPE,
        content_disposition="attachment",
    )

    # Increment CAS ref count so the PR workflow can track the blob
    reference_attempted = True
    await increment_cas_ref(
        redis_client,
        sha256,
        initial_data={
            "file_key": file_key,
            "size": file_size,
            "mime_type": QCM_MIME_TYPE,
            "file_name": "qcm.qcm",
        },
        operation_id=increment_operation_id,
    )
    db.add(
        CasStagingClaim(
            id=claim_id,
            user_id=user.id,
            file_key=file_key,
            sha256=sha256,
            expires_at=datetime.now(UTC) + timedelta(hours=48),
        )
    )

    logger.info(
        "QCM staged: key=%s sha256=%s size=%d user=%s", file_key, sha256, file_size, user.id
    )

    return QCMStageResponse(file_key=file_key, sha256=sha256, file_size=file_size)


# ---------------------------------------------------------------------------
# Moodle XML parsing helpers
# ---------------------------------------------------------------------------


class _HTMLStripper(HTMLParser):
    """Minimal HTML tag stripper."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts).strip()


def _strip_html(html: str) -> str:
    """Strip HTML tags and return plain text."""
    s = _HTMLStripper()
    try:
        s.feed(html or "")
    except Exception:
        return html or ""
    return s.get_text()


def _get_text(element: Element | None, path: str) -> str:
    """Extract text from an XML sub-element, stripping HTML."""
    if element is None:
        return ""
    found = element.find(path)
    if found is None or found.text is None:
        return ""
    return _strip_html(found.text)


def _parse_moodle_xml(xml_bytes: bytes) -> dict[str, Any]:
    """Parse Moodle XML quiz export into a QCMFile dict."""
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid XML: {exc}") from exc

    # Chapters keyed by chapter title (preserves insertion order)
    chapters_map: dict[str, list[dict[str, Any]]] = {}
    total = 0
    current_category: str | None = None  # set by <question type="category"> elements

    for question in root.findall("question"):
        q_type = (question.get("type") or "").lower()

        # Track the active Moodle category — used as the chapter title
        if q_type == "category":
            cat_raw = _get_text(question, "category/text")
            # Strip the common "$course$/top/" prefix that Moodle prepends
            if "/" in cat_raw:
                cat_raw = cat_raw.rsplit("/", 1)[-1]
            cat_raw = cat_raw.strip()
            if cat_raw:
                current_category = cat_raw
            continue

        if q_type not in ("multichoice", "multiple", "truefalse"):
            continue

        if total >= QCM_MAX_QUESTIONS:
            break
        total += 1

        # ---- Chapter detection ----
        # Primary: use the most recent category element (our export writes these).
        # Fallback: parse "Chapter: question text" from the name field (third-party
        # Moodle exports that don't use category markers).
        raw_name = _get_text(question, "name/text")
        if current_category is not None:
            chapter_title = current_category
        else:
            chapter_title = "Questions"  # default
            _sep_match = re.match(r"^(.+?):\s+(.+)$", raw_name)
            if _sep_match:
                chapter_title = _sep_match.group(1).strip()

        # ---- Question text ----
        q_text_raw = question.find("questiontext")
        if q_text_raw is not None:
            q_text_elem = q_text_raw.find("text")
            q_text = _strip_html(q_text_elem.text or "") if q_text_elem is not None else ""
        else:
            q_text = raw_name

        if not q_text:
            q_text = raw_name

        # ---- Answers ----
        answers: list[dict[str, Any]] = []
        for answer_el in question.findall("answer"):
            fraction_str = answer_el.get("fraction", "0")
            try:
                fraction = float(fraction_str)
            except ValueError:
                fraction = 0.0
            is_correct = fraction > 0

            text_el = answer_el.find("text")
            a_text = _strip_html(text_el.text or "") if text_el is not None else ""
            if not a_text:
                continue

            answers.append(
                {
                    "id": str(uuid.uuid4()),
                    "text": a_text,
                    "correct": is_correct,
                }
            )
            if len(answers) >= QCM_MAX_ANSWERS_PER_QUESTION:
                break

        if not answers:
            continue

        # ---- Explanation ----
        explanation = ""
        feedback_el = question.find("generalfeedback")
        if feedback_el is not None:
            text_el = feedback_el.find("text")
            if text_el is not None:
                explanation = _strip_html(text_el.text or "")

        q_obj: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "text": q_text,
            "answers": answers,
        }
        if explanation:
            q_obj["explanation"] = explanation

        if chapter_title not in chapters_map:
            chapters_map[chapter_title] = []
        chapters_map[chapter_title].append(q_obj)

    chapters = [
        {"id": str(uuid.uuid4()), "title": title, "questions": qs}
        for title, qs in chapters_map.items()
    ]

    if not chapters:
        chapters = [{"id": str(uuid.uuid4()), "title": "Questions", "questions": []}]

    return {"version": 1, "chapters": chapters}


# ---------------------------------------------------------------------------
# Moodle XML export helpers
# ---------------------------------------------------------------------------


def _escape_xml(text: str) -> str:
    """Escape characters that are special in XML text content."""
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _generate_moodle_xml(qcm_data: dict[str, Any]) -> bytes:
    """Convert a QCMFile dict into Moodle XML quiz export bytes."""
    lines: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>', "<quiz>"]

    for chapter in qcm_data.get("chapters", []):
        chapter_title = chapter.get("title", "Questions")
        # Emit a Moodle category marker so the chapter survives a round-trip
        lines.append('  <question type="category">')
        lines.append(
            f"    <category><text>$course$/top/{_escape_xml(chapter_title)}</text></category>"
        )
        lines.append("  </question>")

        for question in chapter.get("questions", []):
            q_text = _escape_xml(question.get("text", ""))
            name_text = _escape_xml(question.get("text", "")[:255])
            explanation = _escape_xml(question.get("explanation", ""))

            lines.append('  <question type="multichoice">')
            lines.append(f"    <name><text>{name_text}</text></name>")
            lines.append(f'    <questiontext format="html"><text>{q_text}</text></questiontext>')
            if explanation:
                lines.append(
                    f'    <generalfeedback format="html"><text>{explanation}</text></generalfeedback>'
                )

            correct_answers = [a for a in question.get("answers", []) if a.get("correct")]
            num_correct = len(correct_answers) or 1
            correct_fraction = round(100 / num_correct, 4)

            for answer in question.get("answers", []):
                fraction = correct_fraction if answer.get("correct") else 0
                a_text = _escape_xml(answer.get("text", ""))
                lines.append(f'    <answer fraction="{fraction}" format="html">')
                lines.append(f"      <text>{a_text}</text>")
                lines.append("    </answer>")

            lines.append("  </question>")

    lines.append("</quiz>")
    return "\n".join(lines).encode("utf-8")


@router.get("/export-moodle/{material_id}")
async def export_moodle_xml(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Export a QCM material as a Moodle-compatible XML file for re-import."""
    data = await get_material_with_version(db, material_id, current_user_id=user.id)

    version = data.get("current_version_info")
    if version is None or version.get("file_key") is None:
        raise HTTPException(status_code=404, detail="No file available for this material")

    mime = version.get("file_mime_type", "")
    if mime != QCM_MIME_TYPE:
        raise HTTPException(status_code=400, detail="Material is not a QCM")

    file_key = version["file_key"]
    raw = await read_full_object(file_key)
    if len(raw) > QCM_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Stored QCM is too large to export")

    qcm_data = json.loads(raw)

    title = (data.get("title") or "qcm").strip()
    safe_title = re.sub(r"[^\w\- ]", "", title).strip().replace(" ", "-") or "qcm"
    filename = f"{safe_title}.xml"

    xml_bytes = _generate_moodle_xml(qcm_data)

    return Response(
        content=xml_bytes,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/parse-moodle")
async def parse_moodle_xml(
    user: CurrentUser,
    file: UploadFile,
) -> dict[str, Any]:
    """Parse a Moodle XML quiz export and return a QCMFile JSON object.

    Nothing is saved to storage — the caller must submit the result to
    ``POST /api/qcm/stage`` to persist it.
    """
    if not (file.filename or "").lower().endswith(".xml"):
        raise HTTPException(status_code=422, detail="File must be a .xml Moodle export")

    content = await file.read(MOODLE_XML_MAX_BYTES + 1)
    if len(content) > MOODLE_XML_MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")

    return _parse_moodle_xml(content)
