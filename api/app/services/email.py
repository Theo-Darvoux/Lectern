import json

from app.config import settings
from app.core.events.email import send_email


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


def _render_code_box_html(code: str) -> str:
    """Renders OTP verification code into a single styled monospace box for easy copying."""
    clean_code = code.strip()
    return f"""
    <table role="presentation" border="0" cellpadding="0" cellspacing="0" style="margin: 0 auto;">
        <tr>
            <td align="center" style="background-color: #12131d; border: 1px solid #202334; border-radius: 8px; padding: 14px 32px;">
                <span style="font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace; font-size: 24px; font-weight: 700; letter-spacing: 6px; padding-left: 6px; color: #f0eef5; text-align: center; display: inline-block; user-select: all; -webkit-user-select: all;">{clean_code}</span>
            </td>
        </tr>
    </table>
    """


async def send_verification_email(email: str, code: str, magic_link: str) -> None:
    site_name = settings.site_name
    site_name_style = settings.site_name_style
    name_html, fonts_url = _render_site_name_html(site_name, site_name_style)
    code_box_html = _render_code_box_html(code)

    google_fonts_style = f'<style>@import url("{fonts_url}");</style>' if fonts_url else ""

    subject = f"{site_name} - Sign in to your account"
    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="dark">
    <meta name="supported-color-schemes" content="dark">
    {google_fonts_style}
    <style>
        :root {{
            color-scheme: dark;
            supported-color-schemes: dark;
        }}
        body, table, td, a {{
            -webkit-text-size-adjust: 100%;
            -ms-text-size-adjust: 100%;
        }}
        body {{
            margin: 0 !important;
            padding: 0 !important;
            background-color: #06070a !important;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            color: #f0eef5 !important;
        }}
        @media only screen and (max-width: 480px) {{
            .email-card {{
                width: 100% !important;
                max-width: 100% !important;
                border-radius: 8px !important;
            }}
            .email-card-content {{
                padding: 24px 16px !important;
            }}
        }}
    </style>
</head>
<body style="margin: 0; padding: 0; background-color: #06070a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #f0eef5;">
    <!-- Outer background table -->
    <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color: #06070a; padding: 40px 16px;">
        <tr>
            <td align="center">
                <!-- Main Auth Card -->
                <table role="presentation" class="email-card" width="100%" border="0" cellpadding="0" cellspacing="0" style="max-width: 440px; background-color: #0c0d14; border: 1px solid #1c1e2b; border-radius: 12px; box-shadow: 0 24px 48px -12px rgba(0, 0, 0, 0.75);">
                    <tr>
                        <td class="email-card-content" style="padding: 36px 32px;">
                            <!-- Header: Site Name -->
                            <div style="text-align: center;">
                                <h1 style="margin: 0; font-size: 24px; font-weight: 800; letter-spacing: -0.02em; color: #f8f7fc; line-height: 1.2;">{name_html}</h1>
                                <p style="margin: 6px 0 0; font-size: 13px; font-weight: 500; letter-spacing: 0.015em; color: #918da6; line-height: 1.4;">Sign in to your account</p>
                            </div>

                            <!-- Hairline Separator -->
                            <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="margin: 22px 0 24px;">
                                <tr>
                                    <td style="height: 1px; background-color: #1e2030; font-size: 0px; line-height: 1px;">&nbsp;</td>
                                </tr>
                            </table>

                            <!-- Primary Action: Magic Link Button -->
                            <div style="text-align: center;">
                                <table role="presentation" border="0" cellpadding="0" cellspacing="0" style="margin: 0 auto; width: 100%;">
                                    <tr>
                                        <td align="center" style="border-radius: 6px; background-color: #f0eff5;">
                                            <a href="{magic_link}" target="_blank" style="display: block; padding: 13px 24px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 14px; font-weight: 600; letter-spacing: 0.01em; color: #08090f; text-decoration: none; border-radius: 6px; background-color: #f0eff5; text-align: center;">Sign in to {site_name} &rarr;</a>
                                        </td>
                                    </tr>
                                </table>
                            </div>

                            <!-- Overline Separator: OR ENTER CODE -->
                            <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="margin: 26px 0 20px;">
                                <tr>
                                    <td style="border-bottom: 1px solid #1a1c29; font-size: 0; line-height: 0;" width="28%">&nbsp;</td>
                                    <td align="center" style="padding: 0 10px; white-space: nowrap; font-family: ui-monospace, 'SF Mono', Monaco, Consolas, monospace; font-size: 10px; font-weight: 600; letter-spacing: 0.18em; text-transform: uppercase; color: #6a667d;">
                                        Verification Code
                                    </td>
                                    <td style="border-bottom: 1px solid #1a1c29; font-size: 0; line-height: 0;" width="28%">&nbsp;</td>
                                </tr>
                            </table>

                            <!-- OTP Code Box -->
                            <div style="text-align: center;">
                                {code_box_html}
                            </div>

                            <!-- Security / Expiration Notice -->
                            <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="margin-top: 24px; background-color: #12131d; border: 1px solid #1a1c29; border-radius: 6px;">
                                <tr>
                                    <td align="center" style="padding: 12px 16px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 12px; line-height: 18px; color: #828096;">
                                        <p style="margin: 0; color: #828096;">This link and code expire in <strong style="color: #dedbe8; font-weight: 600;">10 minutes</strong>.</p>
                                        <p style="margin: 4px 0 0; color: #524f64; font-size: 11px;">If you didn't request this, you can safely ignore this email.</p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                </table>

                <!-- Sub-card Footer Info -->
                <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="max-width: 440px; margin: 16px auto 0;">
                    <tr>
                        <td align="center" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 11px; color: #524f64; line-height: 16px;">
                            Secured authentication for {site_name}
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
