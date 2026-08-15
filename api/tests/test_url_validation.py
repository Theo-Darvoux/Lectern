"""Tests for app.core.security.url_validation — SSRF prevention."""

from unittest.mock import patch

import pytest

from app.core.security.url_validation import is_safe_url, resolve_safe_url

# ── Scheme checks ────────────────────────────────────────────────────────────


def test_https_public_ip_passes():
    """An HTTPS URL resolving to a public IP is allowed."""
    # We patch getaddrinfo to return a well-known public address.
    with patch(
        "app.core.security.url_validation.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 0))],
    ):
        assert is_safe_url("https://example.com/hook") is True


def test_http_is_blocked():
    """Plain HTTP is never allowed (only HTTPS)."""
    assert is_safe_url("http://example.com/hook") is False


def test_ftp_is_blocked():
    """Non-HTTP(S) schemes are blocked."""
    assert is_safe_url("ftp://example.com/hook") is False


def test_missing_scheme_is_blocked():
    """A URL with no scheme is blocked."""
    assert is_safe_url("example.com/hook") is False


# ── Private IP blocks ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "::1",
        "10.0.0.1",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.0.1",
        "100.64.0.1",  # carrier-grade NAT is not globally routable
        "224.0.0.1",  # multicast
        "192.0.2.1",  # documentation/reserved range
    ],
)
def test_private_ip_direct_is_blocked(ip):
    """Direct private/loopback/link-local IPs are blocked."""
    assert is_safe_url(f"https://{ip}/hook") is False


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.100.1",
        "169.254.169.254",  # AWS metadata service
    ],
)
def test_hostname_resolving_to_private_ip_is_blocked(ip):
    """A hostname that resolves to a private IP is blocked (DNS rebinding prevention)."""
    with patch(
        "app.core.security.url_validation.socket.getaddrinfo",
        return_value=[(None, None, None, None, (ip, 0))],
    ):
        assert is_safe_url("https://internal.example.com/hook") is False


def test_dns_failure_is_blocked():
    """A hostname that cannot be resolved is blocked (fail-closed)."""
    import socket

    with patch(
        "app.core.security.url_validation.socket.getaddrinfo",
        side_effect=socket.gaierror("Name or service not known"),
    ):
        assert is_safe_url("https://doesnotexist.invalid/hook") is False


def test_public_ip_direct_passes():
    """A direct HTTPS URL to a public IP is allowed."""
    assert is_safe_url("https://93.184.216.34/hook") is True


def test_resolution_is_returned_for_connection_pinning():
    with patch(
        "app.core.security.url_validation.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ):
        target = resolve_safe_url("https://example.com/hook")

    assert target is not None
    assert target.hostname == "example.com"
    assert target.addresses == ("93.184.216.34",)


def test_malformed_url_is_blocked():
    """Completely malformed URLs are blocked."""
    assert is_safe_url("not-a-url-at-all") is False
    assert is_safe_url("") is False
