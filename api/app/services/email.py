import html
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
    <table role="presentation" border="0" cellpadding="0" cellspacing="0" bgcolor="#161826" style="margin: 0 auto; background-color: #161826; border: 1px solid #2e334d; border-radius: 8px;">
        <tr>
            <td align="center" bgcolor="#161826" style="background-color: #161826; border-radius: 8px; padding: 14px 32px;">
                <span style="font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace; font-size: 24px; font-weight: 700; letter-spacing: 6px; padding-left: 6px; color: #ffffff; text-align: center; display: inline-block; user-select: all; -webkit-user-select: all;">{clean_code}</span>
            </td>
        </tr>
    </table>
    """


async def send_verification_email(email: str, code: str, magic_link: str) -> None:
    site_name = settings.site_name
    site_name_style = settings.site_name_style
    name_html, fonts_url = _render_site_name_html(site_name, site_name_style)
    clean_code = code.strip()
    code_box_html = _render_code_box_html(clean_code)

    google_fonts_style = f'<style>@import url("{fonts_url}");</style>' if fonts_url else ""

    subject = f"{site_name} - Sign in to your account"

    # Dedicated high-quality plain-text part to prevent spam filter discrepancies
    plain_text = f"""Sign in to {site_name}

We received a request to sign in to your {site_name} account ({email}).

Option 1: Sign in with magic link
Open the following link in your browser:
{magic_link}

Option 2: Use verification code
Enter this verification code on the login page:
{clean_code}

Security Notice:
This link and code will expire in 10 minutes.
If you did not request this email, you can safely ignore it. Your account remains secure.

--
Automated authentication email for {site_name}
"""

    body = f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head>
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="dark">
    <meta name="supported-color-schemes" content="dark">
    <title>{subject}</title>
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
            background-color: #07080d !important;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            color: #ffffff !important;
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
<body bgcolor="#07080d" style="margin: 0; padding: 0; background-color: #07080d; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #ffffff;">
    <!-- Preheader for email clients -->
    <div style="display: none; max-height: 0px; overflow: hidden; mso-hide: all;">
        Your {site_name} verification code is {clean_code}. Click the link or enter the code to sign in.
    </div>

    <!-- Outer background table -->
    <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" bgcolor="#07080d" style="background-color: #07080d; padding: 40px 16px;">
        <tr>
            <td align="center" bgcolor="#07080d">
                <!-- Main Auth Card -->
                <table role="presentation" class="email-card" width="100%" border="0" cellpadding="0" cellspacing="0" bgcolor="#0f1019" style="max-width: 460px; background-color: #0f1019; border: 1px solid #23263b; border-radius: 12px; box-shadow: 0 24px 48px -12px rgba(0, 0, 0, 0.75);">
                    <tr>
                        <td class="email-card-content" bgcolor="#0f1019" style="padding: 36px 32px;">
                            <!-- Header: Site Name -->
                            <div style="text-align: center;">
                                <h1 style="margin: 0; font-size: 24px; font-weight: 800; letter-spacing: -0.02em; color: #ffffff; line-height: 1.2;">{name_html}</h1>
                                <p style="margin: 6px 0 0; font-size: 14px; font-weight: 500; letter-spacing: 0.01em; color: #c8c5db; line-height: 1.4;">Sign in to your account</p>
                            </div>

                            <!-- Introductory context for spam filters and accessibility -->
                            <p style="margin: 18px 0 0; font-size: 14px; line-height: 22px; color: #e0dded; text-align: center;">
                                We received a sign-in request for your <strong>{site_name}</strong> account. Click the button below to sign in instantly, or enter the verification code on the login screen.
                            </p>

                            <!-- Hairline Separator -->
                            <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="margin: 20px 0 24px;">
                                <tr>
                                    <td bgcolor="#2b2d42" style="height: 1px; background-color: #2b2d42; font-size: 0px; line-height: 1px;">&nbsp;</td>
                                </tr>
                            </table>

                            <!-- Primary Action: Magic Link Button -->
                            <div style="text-align: center;">
                                <table role="presentation" border="0" cellpadding="0" cellspacing="0" style="margin: 0 auto; width: 100%;">
                                    <tr>
                                        <td align="center" bgcolor="#f3f2f8" style="border-radius: 6px; background-color: #f3f2f8;">
                                            <a href="{magic_link}" target="_blank" style="display: block; padding: 13px 24px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 14px; font-weight: 600; letter-spacing: 0.01em; color: #08090f; text-decoration: none; border-radius: 6px; background-color: #f3f2f8; text-align: center;">Sign in to {site_name} &rarr;</a>
                                        </td>
                                    </tr>
                                </table>
                            </div>

                            <!-- Fallback Link URL -->
                            <p style="margin: 14px 0 0; font-size: 12px; line-height: 18px; color: #b0acc4; text-align: center; word-break: break-all;">
                                Button not working? Paste this link into your browser:<br>
                                <a href="{magic_link}" target="_blank" style="color: #c4a1ff; text-decoration: underline;">{magic_link}</a>
                            </p>

                            <!-- Overline Separator: OR ENTER CODE -->
                            <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="margin: 26px 0 20px;">
                                <tr>
                                    <td style="border-bottom: 1px solid #2b2d42; font-size: 0; line-height: 0;" width="25%">&nbsp;</td>
                                    <td align="center" style="padding: 0 10px; white-space: nowrap; font-family: ui-monospace, 'SF Mono', Monaco, Consolas, monospace; font-size: 11px; font-weight: 700; letter-spacing: 0.16em; text-transform: uppercase; color: #bbb8ce;">
                                        Verification Code
                                    </td>
                                    <td style="border-bottom: 1px solid #2b2d42; font-size: 0; line-height: 0;" width="25%">&nbsp;</td>
                                </tr>
                            </table>

                            <!-- OTP Code Box -->
                            <div style="text-align: center;">
                                {code_box_html}
                            </div>

                            <!-- Security / Expiration Notice -->
                            <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" bgcolor="#161826" style="margin-top: 24px; background-color: #161826; border: 1px solid #2a2e45; border-radius: 6px;">
                                <tr>
                                    <td align="center" bgcolor="#161826" style="padding: 12px 16px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 12px; line-height: 18px; color: #d4d1e2;">
                                        <p style="margin: 0; color: #d4d1e2;">This link and code expire in <strong style="color: #ffffff; font-weight: 600;">10 minutes</strong>.</p>
                                        <p style="margin: 4px 0 0; color: #b0acc4; font-size: 11px; line-height: 16px;">If you didn't request this, you can safely ignore this email.</p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                </table>

                <!-- Sub-card Footer Info -->
                <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" bgcolor="#07080d" style="max-width: 460px; margin: 16px auto 0;">
                    <tr>
                        <td align="center" bgcolor="#07080d" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 12px; color: #a5a0b8; line-height: 18px;">
                            Secured authentication for {site_name} &bull; Sent to {email}
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""
    await send_email(email, subject, body, plain_text=plain_text)


async def send_password_reset_email(email: str, reset_link: str) -> None:
    site_name = settings.site_name
    subject = f"{site_name} - Reset your password"
    safe_site_name = html.escape(site_name)
    safe_link = html.escape(reset_link, quote=True)
    plain_text = f"""Reset your {site_name} password

We received a request to reset the password for {email}.

Open this link to choose a new password:
{reset_link}

This single-use link expires in 15 minutes. If you did not request a password reset,
you can safely ignore this email.
"""
    body = f"""<!doctype html>
<html lang="en">
<body style="margin:0;padding:40px 16px;background:#07080d;color:#ffffff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
    <tr><td align="center">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:460px;background:#0f1019;border:1px solid #23263b;border-radius:12px">
        <tr><td style="padding:36px 32px;text-align:center">
          <h1 style="margin:0;font-size:24px">{safe_site_name}</h1>
          <p style="margin:10px 0 24px;color:#c8c5db">Choose a new password for your account.</p>
          <a href="{safe_link}" style="display:block;padding:13px 24px;border-radius:6px;background:#f3f2f8;color:#08090f;text-decoration:none;font-weight:600">Reset password &rarr;</a>
          <p style="margin:20px 0 0;color:#b0acc4;font-size:12px;line-height:18px">This single-use link expires in 15 minutes. If you did not request it, you can safely ignore this email.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
    await send_email(email, subject, body, plain_text=plain_text)
