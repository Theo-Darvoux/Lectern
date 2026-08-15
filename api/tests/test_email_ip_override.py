from unittest.mock import AsyncMock, patch

import pytest

from app.core.events.email import send_email


@pytest.mark.asyncio
async def test_send_email_with_ip_override_port_587() -> None:
    """When smtp_ip is set and port=587, connect() is called without server_hostname
    and starttls() receives server_hostname=<real host> for cert validation."""
    from app.config import settings

    with (
        patch.object(settings, "smtp_host", "mail.example.com"),
        patch.object(settings, "smtp_ip", "1.2.3.4"),
        patch.object(settings, "smtp_port", 587),
        patch.object(settings, "smtp_user", "user"),
        patch.object(settings, "smtp_password", "password"),
        patch.object(settings, "smtp_from", "noreply@example.com"),
        patch.object(settings, "smtp_use_tls", True),
        patch("aiosmtplib.SMTP", autospec=True) as mock_smtp_class,
    ):
        mock_smtp = mock_smtp_class.return_value
        mock_smtp.connect = AsyncMock()
        mock_smtp.starttls = AsyncMock()
        mock_smtp.login = AsyncMock()
        mock_smtp.send_message = AsyncMock()
        mock_smtp.close = AsyncMock()

        await send_email("to@example.com", "Subject", "Body")

        # Must connect to the IP
        init_kwargs = mock_smtp_class.call_args.kwargs
        assert init_kwargs["hostname"] == "1.2.3.4"
        assert init_kwargs["port"] == 587
        # No implicit TLS — we use STARTTLS path
        assert init_kwargs["use_tls"] is False

        # connect() must NOT receive server_hostname (it's not a valid kwarg)
        mock_smtp.connect.assert_called_once_with()

        # starttls() IS the correct place for server_hostname
        mock_smtp.starttls.assert_called_once_with(server_hostname="mail.example.com")
        mock_smtp.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_send_email_with_ip_override_port_465_is_rejected() -> None:
    """Implicit TLS cannot safely validate a certificate when connecting by IP."""
    from app.config import settings

    with (
        patch.object(settings, "smtp_host", "mail.example.com"),
        patch.object(settings, "smtp_ip", "1.2.3.4"),
        patch.object(settings, "smtp_port", 465),
        patch.object(settings, "smtp_user", "user"),
        patch.object(settings, "smtp_password", "password"),
        patch.object(settings, "smtp_from", "noreply@example.com"),
        patch.object(settings, "smtp_use_tls", True),
        patch("aiosmtplib.SMTP", autospec=True) as mock_smtp_class,
    ):
        with pytest.raises(ValueError, match="SMTP_IP cannot be used with implicit TLS"):
            await send_email("to@example.com", "Subject", "Body")

        mock_smtp_class.assert_not_called()


@pytest.mark.asyncio
async def test_port_25_uses_starttls_when_tls_enabled() -> None:
    """TLS on a non-465 port means STARTTLS, not implicit TLS."""
    from app.config import settings

    with (
        patch.object(settings, "smtp_host", "mail.example.com"),
        patch.object(settings, "smtp_ip", None),
        patch.object(settings, "smtp_port", 25),
        patch.object(settings, "smtp_use_tls", True),
        patch.object(settings, "smtp_tls_mode", None),
        patch.object(settings, "smtp_from", "noreply@example.com"),
        patch("aiosmtplib.SMTP", autospec=True) as mock_smtp_class,
    ):
        mock_smtp = mock_smtp_class.return_value
        mock_smtp.connect = AsyncMock()
        mock_smtp.starttls = AsyncMock()
        mock_smtp.send_message = AsyncMock()

        await send_email("to@example.com", "Subject", "Body")

        assert mock_smtp_class.call_args.kwargs["use_tls"] is False
        mock_smtp.starttls.assert_awaited_once_with(server_hostname=None)


@pytest.mark.asyncio
async def test_send_email_without_ip_override() -> None:
    """Without an IP override, starttls() is called with server_hostname=None
    (library uses the hostname parameter as expected)."""
    from app.config import settings

    with (
        patch.object(settings, "smtp_host", "mail.example.com"),
        patch.object(settings, "smtp_ip", None),
        patch.object(settings, "smtp_port", 587),
        patch.object(settings, "smtp_user", "user"),
        patch.object(settings, "smtp_password", "password"),
        patch.object(settings, "smtp_from", "noreply@example.com"),
        patch.object(settings, "smtp_use_tls", True),
        patch("aiosmtplib.SMTP", autospec=True) as mock_smtp_class,
    ):
        mock_smtp = mock_smtp_class.return_value
        mock_smtp.connect = AsyncMock()
        mock_smtp.starttls = AsyncMock()
        mock_smtp.login = AsyncMock()
        mock_smtp.send_message = AsyncMock()
        mock_smtp.close = AsyncMock()

        await send_email("to@example.com", "Subject", "Body")

        init_kwargs = mock_smtp_class.call_args.kwargs
        assert init_kwargs["hostname"] == "mail.example.com"

        # connect() has no server_hostname override
        mock_smtp.connect.assert_called_once_with()

        # starttls() called without override — library defaults to hostname
        mock_smtp.starttls.assert_called_once_with(server_hostname=None)
        mock_smtp.send_message.assert_called_once()
