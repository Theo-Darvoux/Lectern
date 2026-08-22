import contextlib
import re
import textwrap
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from html.parser import HTMLParser

import aiosmtplib

from app.config import settings


class HTMLTextExtractor(HTMLParser):
    """Safely extracts plain text from HTML, preserving links and ignoring scripts/styles."""

    def __init__(self) -> None:
        super().__init__()
        self.result: list[str] = []
        self.hide_depth = 0
        self.hidden_tags = {"script", "style", "head", "title"}
        self.block_tags = {"p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "hr"}
        self._current_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.hidden_tags:
            self.hide_depth += 1
        elif tag in self.block_tags:
            self.result.append("\n")
        elif tag == "a":
            href_map = dict(attrs)
            href = href_map.get("href")
            if href and not href.startswith("mailto:") and not href.startswith("#"):
                self._current_href = href

    def handle_endtag(self, tag: str) -> None:
        if tag in self.hidden_tags and self.hide_depth > 0:
            self.hide_depth -= 1
        elif tag in self.block_tags:
            self.result.append("\n")
        elif tag == "a" and self._current_href:
            self.result.append(f" ({self._current_href}) ")
            self._current_href = None

    def handle_data(self, data: str) -> None:
        if self.hide_depth == 0:
            self.result.append(data)


def _html_to_plain(html: str) -> str:
    parser = HTMLTextExtractor()
    parser.feed(html)
    raw_text = "".join(parser.result)

    # Clean up excessive whitespace and structural newlines
    text = re.sub(r"[ \t]+", " ", raw_text)
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return textwrap.fill(text, width=78)


def get_smtp_tls_mode() -> str:
    """Return the explicit or backwards-compatible SMTP transport mode."""
    if settings.smtp_tls_mode is not None:
        return settings.smtp_tls_mode
    if not settings.smtp_use_tls:
        return "none"
    return "implicit" if settings.smtp_port == 465 else "starttls"


async def send_email(
    to: str,
    subject: str,
    body: str,
    plain_text: str | None = None,
) -> None:
    host = settings.smtp_host
    ip = settings.smtp_ip
    port = settings.smtp_port
    user = settings.smtp_user
    password = settings.smtp_password
    from_email = settings.smtp_from
    sender_name = settings.smtp_sender_name
    tls_mode = get_smtp_tls_mode()

    html_body = body.strip()
    if not re.search(r"<html[\s>]", html_body, re.IGNORECASE):
        html_body = (
            f"<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>{subject}</title></head>"
            f"<body>{html_body}</body></html>"
        )

    message = EmailMessage()
    message["From"] = (
        formataddr((sender_name, from_email)) if sender_name and from_email else from_email
    )
    message["To"] = to
    message["Subject"] = subject

    # Date header: RFC 5322 formatted with local/GMT timezone
    message["Date"] = formatdate(localtime=True)

    # Align Message-ID domain with From domain if available
    msg_domain = None
    if from_email and "@" in from_email:
        msg_domain = from_email.split("@", 1)[1].strip()
    message["Message-Id"] = make_msgid(domain=msg_domain or host or "localhost")

    # Transactional & automated delivery headers to prevent spam false positives
    message["Auto-Submitted"] = "auto-generated"
    message["X-Auto-Response-Suppress"] = "All"

    plain_content = plain_text if plain_text is not None else _html_to_plain(html_body)
    message.set_content(plain_content, subtype="plain", charset="utf-8")
    message.add_alternative(html_body, subtype="html", charset="utf-8")

    use_implicit_tls = tls_mode == "implicit"
    use_starttls = tls_mode == "starttls"
    if ip and use_implicit_tls:
        raise ValueError(
            "SMTP_IP cannot be used with implicit TLS because certificate SNI must use "
            "SMTP_HOST; use DNS or STARTTLS instead"
        )

    smtp = aiosmtplib.SMTP(
        hostname=ip or host,
        port=port,
        use_tls=use_implicit_tls,
        start_tls=False,
    )

    try:
        await smtp.connect()

        if use_starttls:
            await smtp.starttls(server_hostname=host if ip else None)

        if user and password:
            await smtp.login(user, password)

        await smtp.send_message(message)
    finally:
        with contextlib.suppress(Exception):
            await smtp.quit()
