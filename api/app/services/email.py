from app.core.email import send_email
from app.config import settings
from app.models.auth_config import AuthConfig


async def send_verification_email(
    email: str, code: str, magic_link: str, config: AuthConfig | None = None
) -> None:
    site_name = (config.site_name if config else None) or settings.site_name
    subject = f"{site_name} - Sign in to your account"
    body = f"""
    <html>
    <body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #f9fafb;">
        <table width="100%" cellpadding="0" cellspacing="0" style="padding: 40px 20px;">
            <tr>
                <td align="center">
                    <table width="480" cellpadding="0" cellspacing="0" style="background: #ffffff; border-radius: 8px; padding: 40px; border: 1px solid #e5e7eb;">
                        <tr>
                            <td align="center" style="padding-bottom: 24px;">
                                <h2 style="margin: 0; font-size: 24px; font-weight: 700; color: #111827;">{site_name}</h2>
                            </td>
                        </tr>
                        <tr>
                            <td align="center" style="padding-bottom: 32px;">
                                <p style="margin: 0 0 20px; font-size: 15px; color: #374151;">Click the button below to sign in:</p>
                                <a href="{magic_link}" style="display: inline-block; padding: 12px 32px; background-color: #111827; color: #ffffff; text-decoration: none; border-radius: 6px; font-size: 15px; font-weight: 600;">Sign in to {site_name}</a>
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
    await send_email(email, subject, body, config=config)
