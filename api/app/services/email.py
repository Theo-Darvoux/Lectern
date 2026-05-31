import json

from app.config import settings
from app.core.email import send_email


def _parse_name_segments(raw: str | None) -> list[dict] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and len(parsed) > 0:
            return parsed
    except Exception:
        pass
    return None


def _render_site_name_html(site_name: str, site_name_style: str | None) -> tuple[str, str]:
    """Returns (name_html, google_fonts_import_url)."""
    segments = _parse_name_segments(site_name_style)
    if not segments:
        return site_name, ""

    parts = []
    seen_fonts: set[str] = set()
    fonts_ordered: list[str] = []
    for seg in segments:
        style_parts = []
        font = seg.get("font", "")
        if font:
            style_parts.append(f"font-family: '{font}', sans-serif")
            if font not in seen_fonts:
                fonts_ordered.append(font)
                seen_fonts.add(font)
        if seg.get("color"):
            style_parts.append(f"color: {seg['color']}")
        if seg.get("bold"):
            style_parts.append("font-weight: 700")
        if seg.get("italic"):
            style_parts.append("font-style: italic")

        style = "; ".join(style_parts)
        text = seg.get("text", "")
        parts.append(f'<span style="{style}">{text}</span>' if style else text)

    name_html = "".join(parts)

    fonts_url = ""
    if fonts_ordered:
        params = "&".join(f"family={fn.replace(' ', '+')}" for fn in fonts_ordered)
        fonts_url = f"https://fonts.googleapis.com/css2?{params}&display=swap"

    return name_html, fonts_url


async def send_verification_email(email: str, code: str, magic_link: str) -> None:
    site_name = settings.site_name
    site_name_style = settings.site_name_style
    primary_color = settings.primary_color or "#111827"
    avatar_url = settings.smtp_avatar_url

    name_html, fonts_url = _render_site_name_html(site_name, site_name_style)

    google_fonts_style = f'<style>@import url("{fonts_url}");</style>' if fonts_url else ""

    avatar_html = (
        f'<img src="{avatar_url}" alt="" width="64" height="64" '
        f'style="width:64px;height:64px;border-radius:50%;object-fit:cover;display:block;margin:0 auto 16px;" />'
        if avatar_url
        else ""
    )

    subject = f"{site_name} - Sign in to your account"
    body = f"""
    <html>
    <head>
        {google_fonts_style}
    </head>
    <body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #f9fafb;">
        <table width="100%" cellpadding="0" cellspacing="0" style="padding: 40px 20px;">
            <tr>
                <td align="center">
                    <table width="480" cellpadding="0" cellspacing="0" style="background: #ffffff; border-radius: 8px; padding: 40px; border: 1px solid #e5e7eb;">
                        <tr>
                            <td align="center" style="padding-bottom: 24px;">
                                {avatar_html}<h2 style="margin: 0; font-size: 24px; font-weight: 700; color: #111827;">{name_html}</h2>
                            </td>
                        </tr>
                        <tr>
                            <td align="center" style="padding-bottom: 32px;">
                                <p style="margin: 0 0 20px; font-size: 15px; color: #374151;">Click the button below to sign in:</p>
                                <a href="{magic_link}" style="display: inline-block; padding: 12px 32px; background-color: {primary_color}; color: #ffffff; text-decoration: none; border-radius: 6px; font-size: 15px; font-weight: 600;">Sign in to {site_name}</a>
                            </td>
                        </tr>
                        <tr>
                            <td align="center" style="padding: 24px 0; border-top: 1px solid #e5e7eb;">
                                <p style="margin: 0 0 12px; font-size: 13px; color: #6b7280;">Or enter this code manually:</p>
                                <div style="font-size: 32px; letter-spacing: 8px; font-family: monospace; font-weight: 700; color: #111827;">{code}</div>
                            </td>
                        </tr>
                        <tr>
                            <td align="center" style="padding-top: 24px;">
                                <p style="margin: 0; font-size: 12px; color: #9ca3af;">This link and code expire in 10 minutes.</p>
                                <p style="margin: 8px 0 0; font-size: 12px; color: #9ca3af;">If you didn't request this, you can safely ignore this email.</p>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """
    await send_email(email, subject, body)
