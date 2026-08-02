"""Outbound HTTPS validation with DNS pinning for SSRF prevention."""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlparse

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedHttpsUrl:
    url: str
    hostname: str
    addresses: tuple[str, ...]


@dataclass(frozen=True)
class PinnedHttpResponse:
    status_code: int

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300


class PinnedRequestError(RuntimeError):
    pass


def resolve_safe_url(url: str) -> ResolvedHttpsUrl | None:
    """Resolve an HTTPS URL and reject every non-global destination."""
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None:
            return None
        hostname = parsed.hostname
        if not hostname:
            return None
        # Force validation of malformed/out-of-range explicit ports.
        _ = parsed.port

        addresses: tuple[str, ...]
        try:
            literal = ip_address(hostname)
            addresses = (str(literal),)
        except ValueError:
            try:
                infos = socket.getaddrinfo(
                    hostname, parsed.port or 443, type=socket.SOCK_STREAM
                )
            except OSError as exc:
                logger.warning("Outbound URL blocked: DNS resolution failed for %s: %s", hostname, exc)
                return None
            addresses = tuple(dict.fromkeys(str(info[4][0]) for info in infos))

        if not addresses:
            return None
        for raw_address in addresses:
            address = ip_address(raw_address)
            if not address.is_global or address.is_multicast:
                logger.warning(
                    "Outbound URL blocked: %s resolves to non-global address %s",
                    hostname,
                    address,
                )
                return None
        return ResolvedHttpsUrl(url=url, hostname=hostname, addresses=addresses)
    except (TypeError, ValueError) as exc:
        logger.warning("Outbound URL blocked as malformed: %s", exc)
        return None


def is_safe_url(url: str) -> bool:
    """Compatibility predicate; callers making requests must use the resolved target."""
    return resolve_safe_url(url) is not None


async def resolve_safe_url_async(url: str) -> ResolvedHttpsUrl | None:
    return await asyncio.to_thread(resolve_safe_url, url)


async def is_safe_url_async(url: str) -> bool:
    return await asyncio.to_thread(is_safe_url, url)


class _PinnedResolver(AbstractResolver):
    def __init__(self, target: ResolvedHttpsUrl) -> None:
        self._target = target

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[ResolveResult]:
        if host != self._target.hostname:
            raise OSError("Unexpected hostname in pinned request")
        return [
            ResolveResult(
                hostname=host,
                host=address,
                port=port,
                family=socket.AF_INET6 if ":" in address else socket.AF_INET,
                proto=socket.IPPROTO_TCP,
                flags=socket.AI_NUMERICHOST,
            )
            for address in self._target.addresses
        ]

    async def close(self) -> None:
        return None


async def post_pinned_https(
    target: ResolvedHttpsUrl,
    *,
    content: bytes,
    headers: dict[str, str],
    timeout: float,
) -> PinnedHttpResponse:
    """POST using the already-validated addresses while retaining TLS SNI."""
    connector = aiohttp.TCPConnector(
        resolver=_PinnedResolver(target),
        use_dns_cache=True,
        force_close=True,
    )
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=timeout),
            trust_env=False,
        ) as client:
            async with client.post(
                target.url,
                data=content,
                headers=headers,
                allow_redirects=False,
            ) as response:
                return PinnedHttpResponse(response.status)
    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        raise PinnedRequestError(str(exc)) from exc
