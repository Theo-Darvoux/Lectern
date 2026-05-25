import contextlib
import re
import textwrap
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

import aiosmtplib

from app.config import settings
from app.models.auth_config import AuthConfig


def _html_to_plain(html: str) -> str:
    """Best-effort HTML → plain text for the text/plain alternative."""
    # Collapse whitespace and strip tags
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", text)
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return textwrap.fill(text, width=78)


async def send_email(
    to: str,
    subject: str,
    body: str,
    config: AuthConfig | None = None,
) -> None:
    # Use config from DB if provided, fallback to settings from .env
    host = config.smtp_host if config and config.smtp_host else settings.smtp_host
    ip = config.smtp_ip if config and config.smtp_ip else settings.smtp_ip
    port = config.smtp_port if config and config.smtp_port else settings.smtp_port
    user = config.smtp_user if config and config.smtp_user else settings.smtp_user
    password = config.smtp_password if config and config.smtp_password else settings.smtp_password
    from_email = config.smtp_from if config and config.smtp_from else settings.smtp_from
    sender_name = config.smtp_sender_name if config and config.smtp_sender_name else None
    use_tls = (
        config.smtp_use_tls if config and config.smtp_use_tls is not None else settings.smtp_use_tls
    )

    # Ensure HTML body is wrapped in <html> tags (SpamAssassin HTML_MIME_NO_HTML_TAG)
    html_body = body.strip()
    if not re.search(r"<html[\s>]", html_body, re.IGNORECASE):
        html_body = f"<html><body>{html_body}</body></html>"

    # Build a multipart/alternative message so SpamAssassin doesn't penalise
    # HTML-only messages (MIME_HTML_ONLY). Plain text is the first (fallback) part.
    message = EmailMessage()
    message["From"] = formataddr((sender_name, from_email)) if sender_name and from_email else from_email
    message["To"] = to
    message["Subject"] = subject
    # These headers are required — their absence is penalised heavily by SpamAssassin
    # (MISSING_DATE, MISSING_MID). aiosmtplib does not add them automatically.
    message["Date"] = formatdate(localtime=False)
    message["Message-Id"] = make_msgid(domain=host or "localhost")

    # Set plain text as the primary content, then attach HTML alternative.
    # This creates a proper multipart/alternative structure.
    message.set_content(_html_to_plain(html_body))
    message.add_alternative(html_body, subtype="html")

    # Determine connection mode.
    # - Port 587 → STARTTLS (plain connect, then upgrade)
    # - Port 465 → implicit TLS from the start
    # When an IP override is active and we need implicit TLS (port 465), we
    # cannot tell aiosmtplib which hostname to verify the cert against via
    # connect() (it has no server_hostname param). Instead we downgrade to the
    # STARTTLS path so we can call starttls(server_hostname=<real_host>), which
    # IS the only method in aiosmtplib that accepts server_hostname.
    use_implicit_tls = use_tls and port != 587
    use_starttls = use_tls and not use_implicit_tls

    if ip and use_implicit_tls:
        # Force STARTTLS path so we can inject the correct server_hostname for
        # certificate validation against the real domain, not the IP.
        use_implicit_tls = False
        use_starttls = True

    smtp = aiosmtplib.SMTP(
        hostname=ip or host,
        port=port,
        use_tls=use_implicit_tls,
        # start_tls defaults to auto-detect; we handle it manually below
        start_tls=False,
    )

    try:
        # SMTP.connect() does NOT accept server_hostname — do not pass it.
        await smtp.connect()

        if use_starttls:
            # starttls() is the only aiosmtplib method that accepts
            # server_hostname. Passing the real domain ensures the TLS cert is
            # validated against the intended hostname, not the raw IP address.
            await smtp.starttls(server_hostname=host if ip else None)

        if user and password:
            await smtp.login(user, password)

        await smtp.send_message(message)
    finally:
        with contextlib.suppress(Exception):
            await smtp.quit()
