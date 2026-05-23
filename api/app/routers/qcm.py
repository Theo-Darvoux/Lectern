"""QCM (Multiple Choice Questions) API router.

Provides endpoints:
  POST /api/qcm/stage              – Validate + store a QCM JSON file in the CAS.
  POST /api/qcm/parse-moodle       – Convert a Moodle XML quiz file into QCM JSON.
  GET  /api/qcm/export-moodle/{id} – Export QCM as Moodle-compatible XML.
  GET  /api/qcm/export-pdf/{id}    – Export QCM as a formatted PDF.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from html.parser import HTMLParser
from io import BytesIO
from typing import Annotated, Any
from xml.etree.ElementTree import Element

from defusedxml import ElementTree
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cas import hmac_cas_key, increment_cas_ref
from app.core.database import get_db
from app.core.redis import redis_client
from app.core.storage import upload_file as storage_upload_file
from app.dependencies.auth import CurrentUser

logger = logging.getLogger("wikint.qcm")

router = APIRouter(prefix="/api/qcm", tags=["qcm"])

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

QCM_MAX_QUESTIONS: int = int(os.environ.get("QCM_MAX_QUESTIONS", "100"))
QCM_MIME_TYPE = "application/vnd.wikint.qcm+json"

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
            if not isinstance(answers, list) or len(answers) < 1 or len(answers) > 4:
                raise HTTPException(
                    status_code=422,
                    detail=f"Question {qi} in chapter {ci} must have 1–4 answers",
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


@router.post("/stage", response_model=QCMStageResponse)
async def stage_qcm(
    body: QCMStageRequest,
    user: CurrentUser,
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

    # Derive the CAS S3 key from the HMAC
    cas_redis_key = hmac_cas_key(sha256)
    hmac_digest = cas_redis_key.split(":")[-1]
    file_key = f"cas/{hmac_digest}"

    # Write to object storage (idempotent — same content → same key)
    await storage_upload_file(
        data_bytes,
        file_key,
        content_type=QCM_MIME_TYPE,
        content_disposition="attachment",
    )

    # Increment CAS ref count so the PR workflow can track the blob
    await increment_cas_ref(
        redis_client,
        sha256,
        initial_data={
            "file_key": file_key,
            "size": file_size,
            "mime_type": QCM_MIME_TYPE,
            "file_name": "qcm.qcm",
        },
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
            if len(answers) >= 4:
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
    from app.core.storage import _get_s3_settings, get_s3_client
    from app.services.material import check_material_access, get_material_with_version

    data = await get_material_with_version(db, material_id, current_user_id=user.id)
    check_material_access(user.id, data)

    version = data.get("current_version_info")
    if version is None or version.get("file_key") is None:
        raise HTTPException(status_code=404, detail="No file available for this material")

    mime = version.get("file_mime_type", "")
    if mime != QCM_MIME_TYPE:
        raise HTTPException(status_code=400, detail="Material is not a QCM")

    file_key = version["file_key"]
    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as client:
        response = await client.get_object(Bucket=cfg["bucket"], Key=file_key)  # type: ignore[call-arg]
        body: Any = response["Body"]
        try:
            raw = await body.read()
        finally:
            body.close()

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


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------

_MD_BLOCK_MATH = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_MD_INLINE_MATH = re.compile(r"\$([^$\n]+?)\$")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
_MD_ITALIC = re.compile(r"\*([^*\n]+?)\*|_([^_\n]+?)_")
_MD_CODE = re.compile(r"`([^`]+?)`")
_MD_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_MULTI_NL = re.compile(r"\n{2,}")
_XML_SPECIAL = re.compile(r'[<>&"\']')


def _to_rl_markup(text: str) -> str:
    """Convert markdown + LaTeX to ReportLab paragraph markup."""

    # Escape XML-special chars first so our injected tags survive
    def _esc(m: re.Match) -> str:
        return {"<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;"}[m.group()]

    text = _XML_SPECIAL.sub(_esc, text)
    # Restore bold/italic markers that got escaped (they use * _ not XML chars, so safe)
    text = _MD_BLOCK_MATH.sub(lambda m: f"<i>[{m.group(1).strip()}]</i>", text)
    text = _MD_INLINE_MATH.sub(lambda m: f"<i>[{m.group(1)}]</i>", text)
    text = _MD_BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _MD_ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)
    text = _MD_CODE.sub(lambda m: f'<font name="Courier" size="8">{m.group(1)}</font>', text)
    text = _MD_HEADING.sub("", text)
    text = _MD_MULTI_NL.sub("<br/><br/>", text)
    text = text.replace("\n", "<br/>")
    return text.strip()


def _generate_qcm_pdf(qcm_data: dict[str, Any], title: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        HRFlowable,
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    # ── Palette ────────────────────────────────────────────────────────────
    INDIGO = HexColor("#4F46E5")
    INDIGO_LIGHT = HexColor("#EEF2FF")
    GREEN_BG = HexColor("#F0FDF4")
    GREEN_BORDER = HexColor("#86EFAC")
    BLUE_BG = HexColor("#EFF6FF")
    BLUE_BORDER = HexColor("#BFDBFE")
    BLUE_LABEL = HexColor("#1D4ED8")
    BLUE_TEXT = HexColor("#1E3A8A")
    SEPARATOR = HexColor("#E5E7EB")
    MUTED = HexColor("#6B7280")
    DARK = HexColor("#111827")

    # ── Page geometry ──────────────────────────────────────────────────────
    PAGE_W, _ = A4
    MARGIN = 2.0 * cm
    CW = PAGE_W - 2 * MARGIN  # usable content width

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=2.2 * cm,
    )

    # ── Styles ─────────────────────────────────────────────────────────────
    def S(name: str, **kw: Any) -> ParagraphStyle:
        return ParagraphStyle(name, **kw)

    title_s = S(
        "Title",
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=26,
        textColor=DARK,
        alignment=TA_LEFT,
        spaceAfter=2,
    )
    meta_s = S(
        "Meta", fontName="Helvetica", fontSize=9, leading=13, textColor=MUTED, alignment=TA_LEFT
    )
    ch_s = S("Chapter", fontName="Helvetica-Bold", fontSize=11, leading=15, textColor=colors.white)
    q_num_s = S("QNum", fontName="Helvetica-Bold", fontSize=10, leading=15, textColor=INDIGO)
    q_text_s = S("QText", fontName="Helvetica", fontSize=10, leading=15, textColor=DARK)
    ans_s = S("Ans", fontName="Helvetica", fontSize=9.5, leading=14)
    expl_label_s = S(
        "ExplLabel", fontName="Helvetica-Bold", fontSize=8.5, leading=12, textColor=BLUE_LABEL
    )
    expl_s = S("Expl", fontName="Helvetica", fontSize=9, leading=13, textColor=BLUE_TEXT)

    # ── Page footer ────────────────────────────────────────────────────────
    def _footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(PAGE_W - MARGIN, 0.9 * cm, f"Page {doc.page}")
        canvas.restoreState()

    # ── Build story ────────────────────────────────────────────────────────
    story: list[Any] = []

    story.append(Paragraph(title, title_s))

    chapters = qcm_data.get("chapters", [])
    total_q = sum(len(ch.get("questions", [])) for ch in chapters)
    total_ch = len(chapters)
    q_word = "question" if total_q == 1 else "questions"
    ch_word = "chapter" if total_ch == 1 else "chapters"
    story.append(Paragraph(f"{total_q} {q_word} · {total_ch} {ch_word}", meta_s))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width=CW, thickness=0.75, color=SEPARATOR, spaceAfter=14))

    q_global = 0

    for chapter in chapters:
        # Chapter header
        ch_title = _to_rl_markup(chapter.get("title") or "Chapter")
        ch_table = Table([[Paragraph(ch_title, ch_s)]], colWidths=[CW])
        ch_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), INDIGO),
                    ("TOPPADDING", (0, 0), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                    ("LEFTPADDING", (0, 0), (-1, -1), 14),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                ]
            )
        )
        story.append(ch_table)
        story.append(Spacer(1, 12))

        for question in chapter.get("questions", []):
            q_global += 1
            q_text = _to_rl_markup(question.get("text", ""))

            # Question header row: number | text
            NUM_W = 26
            q_row = Table(
                [[Paragraph(f"Q{q_global}", q_num_s), Paragraph(q_text, q_text_s)]],
                colWidths=[NUM_W, CW - NUM_W],
            )
            q_row.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("TOPPADDING", (0, 0), (-1, -1), 0),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (0, -1), 2),
                        ("RIGHTPADDING", (1, 0), (1, -1), 0),
                    ]
                )
            )

            block: list[Any] = [q_row, Spacer(1, 7)]

            # Answer rows
            for i, answer in enumerate(question.get("answers", [])):
                is_correct = answer.get("correct", False)
                a_text = _to_rl_markup(answer.get("text", ""))

                if is_correct:
                    icon_markup = '<font color="#16A34A"><b>✓</b></font>'
                    text_markup = f'<font color="#166534">{a_text}</font>'
                    bg = GREEN_BG
                    border = GREEN_BORDER
                else:
                    icon_markup = '<font color="#9CA3AF">–</font>'
                    text_markup = f'<font color="#4B5563">{a_text}</font>'
                    bg = colors.white
                    border = SEPARATOR

                ICON_W = 22
                a_row = Table(
                    [[Paragraph(icon_markup, ans_s), Paragraph(text_markup, ans_s)]],
                    colWidths=[ICON_W, CW - ICON_W],
                )
                a_row.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, -1), bg),
                            ("BOX", (0, 0), (-1, -1), 0.5, border),
                            ("TOPPADDING", (0, 0), (-1, -1), 5),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                            ("LEFTPADDING", (0, 0), (0, -1), 9),
                            ("RIGHTPADDING", (0, 0), (0, -1), 3),
                            ("LEFTPADDING", (1, 0), (1, -1), 0),
                            ("RIGHTPADDING", (1, 0), (1, -1), 9),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ]
                    )
                )

                if i > 0:
                    block.append(Spacer(1, 3))
                block.append(a_row)

            # Explanation
            explanation = (question.get("explanation") or "").strip()
            if explanation:
                expl_text = _to_rl_markup(explanation)
                expl_inner = Table(
                    [
                        [Paragraph("Explanation", expl_label_s)],
                        [Paragraph(expl_text, expl_s)],
                    ],
                    colWidths=[CW],
                )
                expl_inner.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, -1), BLUE_BG),
                            ("BOX", (0, 0), (-1, -1), 0.5, BLUE_BORDER),
                            ("TOPPADDING", (0, 0), (-1, 0), 7),
                            ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
                            ("TOPPADDING", (0, 1), (-1, -1), 2),
                            ("BOTTOMPADDING", (0, 1), (-1, -1), 7),
                            ("LEFTPADDING", (0, 0), (-1, -1), 11),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 11),
                        ]
                    )
                )
                # Indigo left accent stripe
                accent_row = Table(
                    [[Paragraph("", expl_s), expl_inner]],
                    colWidths=[4, CW - 4],
                )
                accent_row.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (0, -1), INDIGO_LIGHT),
                            ("LEFTPADDING", (0, 0), (-1, -1), 0),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                            ("TOPPADDING", (0, 0), (-1, -1), 0),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ]
                    )
                )
                block.append(Spacer(1, 6))
                block.append(accent_row)

            block.append(Spacer(1, 16))
            story.append(KeepTogether(block))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


@router.get("/export-pdf/{material_id}")
async def export_qcm_pdf(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Export a QCM material as a formatted PDF."""
    from app.core.storage import _get_s3_settings, get_s3_client
    from app.services.material import check_material_access, get_material_with_version

    data = await get_material_with_version(db, material_id, current_user_id=user.id)
    check_material_access(user.id, data)

    version = data.get("current_version_info")
    if version is None or version.get("file_key") is None:
        raise HTTPException(status_code=404, detail="No file available for this material")

    mime = version.get("file_mime_type", "")
    if mime != QCM_MIME_TYPE:
        raise HTTPException(status_code=400, detail="Material is not a QCM")

    file_key = version["file_key"]
    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as client:
        response = await client.get_object(Bucket=cfg["bucket"], Key=file_key)  # type: ignore[call-arg]
        body: Any = response["Body"]
        try:
            raw = await body.read()
        finally:
            body.close()

    qcm_data = json.loads(raw)
    title = (data.get("title") or "QCM").strip()
    safe_title = re.sub(r"[^\w\- ]", "", title).strip().replace(" ", "-") or "qcm"
    filename = f"{safe_title}.pdf"

    pdf_bytes = _generate_qcm_pdf(qcm_data, title)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
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

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:  # 10 MB hard cap
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")

    return _parse_moodle_xml(content)
