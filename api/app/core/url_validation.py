"""Outbound URL validation — SSRF prevention.

Use :func:`is_safe_url` before making any server-side outbound HTTP request
to a URL that was supplied by a user or stored from user input (e.g. webhook
URLs, redirect targets).

Rules enforced
--------------
* Scheme must be ``https`` (plain ``http`` rejected).
* Hostname must resolve to a public IP address.  Loopback (127.x, ::1),
  private RFC-1918 ranges, and link-local addresses (169.254.x.x, fe80::/10)
  are blocked to prevent SSRF pivoting into internal infrastructure.
* DNS resolution failures are treated as blocked (fail-closed).
"""

import logging
import socket
from ipaddress import ip_address
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def is_safe_url(url: str) -> bool:
    """Return True if *url* is safe for a server-side outbound request.

    Blocks:
    - Non-HTTPS schemes
    - Private/loopback/link-local IP addresses (both direct and via DNS)
    - Unresolvable hostnames

    Args:
        url: The URL to validate.

    Returns:
        ``True`` if the URL passes all checks, ``False`` otherwise.
    """
    try:
        parsed = urlparse(url)

        if parsed.scheme != "https":
            logger.warning("URL blocked: non-HTTPS scheme %r in %s", parsed.scheme, url)
            return False

        hostname = parsed.hostname
        if not hostname:
            logger.warning("URL blocked: missing hostname in %s", url)
            return False

        # Direct numeric IP
        try:
            ip = ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                logger.warning("URL blocked: private/loopback IP %s", hostname)
                return False
            return True
        except ValueError:
            pass  # Not an IP address — fall through to DNS resolution

        # DNS resolution
        try:
            addr_infos = socket.getaddrinfo(hostname, None)
        except OSError as exc:
            logger.warning("URL blocked: DNS resolution failed for %s: %s", hostname, exc)
            return False

        for _family, _kind, _proto, _canonname, sockaddr in addr_infos:
            ip_str = sockaddr[0]
            try:
                ip = ip_address(ip_str)
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    logger.warning("URL blocked: %s resolves to private IP %s", hostname, ip_str)
                    return False
            except ValueError:
                continue

        return True

    except Exception as exc:
        logger.warning("URL validation error for %s: %s", url, exc)
        return False
