"""SSRF protection: only public http(s) destinations may be fetched.

Two layers, because one is not enough:

* ``check_url_static`` inspects the URL itself — scheme, credentials, port,
  hostname shape and IP literals (including IPv4-mapped IPv6 and the cloud
  metadata addresses).
* ``ensure_public_url`` resolves the hostname and rejects the request when
  *any* returned address is private. That closes the "public DNS name pointing
  at 127.0.0.1" hole and the classic ``0x7f000001`` / ``127.1`` encodings,
  because nothing is fetched until the resolved addresses are known to be
  public.

Redirects are validated hop-by-hop by the engine, so a public link cannot 302
its way onto the loopback interface.
"""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import logging
import socket
from typing import Awaitable, Callable, Iterable, Optional, Union
from urllib.parse import urlparse

from linktofile import config
from linktofile.domain.errors import InvalidUrl, UnsafeUrl

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = {"http", "https"}

# Services that must never be reachable through a user-supplied link. File
# hosts can use any port; these are the ones that turn an SSRF into a breach.
BLOCKED_PORTS = {
    21,  # ftp
    22,  # ssh
    23,  # telnet
    25,  # smtp
    111,  # rpcbind
    135,  # msrpc
    139,  # netbios
    445,  # smb
    587,  # smtp submission
    993,  # imaps
    1433,  # mssql
    2375,  # docker api
    2376,  # docker api (tls)
    3306,  # mysql
    5432,  # postgres
    5601,  # kibana
    6379,  # redis
    9200,  # elasticsearch
    9300,  # elasticsearch transport
    10250,  # kubelet
    11211,  # memcached
    27017,  # mongodb
}

BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata",
    "metadata.google.internal",
    "instance-data",
    "metadata.azure.internal",
}

BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".home.arpa", ".lan", ".intranet")

# Explicit override used by the tests and by `SSRF_ENFORCE=0`; ``None`` means
# "follow the configuration".
_ENFORCE_OVERRIDE: Optional[bool] = None

Resolver = Callable[[str, int], Union[Iterable[str], Awaitable["Iterable[str]"]]]


def set_enforcement(enabled: Optional[bool]) -> None:
    """Force enforcement on/off (tests, or an operator on a trusted network)."""
    global _ENFORCE_OVERRIDE
    _ENFORCE_OVERRIDE = enabled


def enforcement_enabled() -> bool:
    if _ENFORCE_OVERRIDE is not None:
        return _ENFORCE_OVERRIDE
    return bool(getattr(config, "SSRF_ENFORCE", True))


def _normalise_host(hostname: str) -> str:
    host = hostname.strip().strip("[]").rstrip(".").lower()
    try:
        return host.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        return host


def ip_is_public(raw: str) -> bool:
    """True only for globally routable unicast addresses."""
    try:
        address = ipaddress.ip_address(raw.strip().strip("[]"))
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def check_url_static(url: str) -> str:
    """Validate an absolute URL without touching DNS; returns the hostname."""
    if not url or len(url) > 4096:
        raise InvalidUrl("That link is not a valid URL.")
    if any(char.isspace() for char in url):
        raise InvalidUrl("That link is not a valid URL.")
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise InvalidUrl("That link is not a valid URL.") from exc

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidUrl("Only http and https links can be fetched.")
    if not parsed.netloc or not parsed.hostname:
        raise InvalidUrl("That link is not a valid URL.")

    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise UnsafeUrl("That link uses an invalid port.") from exc
    if not 0 < port <= 65535:
        raise UnsafeUrl("That link uses an invalid port.")
    if port in BLOCKED_PORTS:
        raise UnsafeUrl("That link points at a service the bot will not contact.")
    if parsed.username or parsed.password:
        raise UnsafeUrl("Links with embedded credentials are not allowed.")

    host = _normalise_host(parsed.hostname)
    if not host:
        raise InvalidUrl("That link is not a valid URL.")
    if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES):
        raise UnsafeUrl("That link points inside the local network.")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # All-numeric dotted hosts (`127.1`, `10.0.1`) are legacy IPv4 forms
        # that resolve inside the network; only real DNS names may pass.
        if host.replace(".", "").isdigit():
            raise UnsafeUrl("That link points inside the local network.") from None
        # A DNS name. Single-label names only ever resolve inside a private
        # network, so they are refused outright.
        if "." not in host:
            raise UnsafeUrl("That link points inside the local network.") from None
        return host
    if not ip_is_public(str(address)):
        raise UnsafeUrl("That link points inside the local network.")
    return host


async def _default_resolver(host: str, port: int) -> tuple[str, ...]:
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout=10
        )
    except (socket.gaierror, asyncio.TimeoutError, UnicodeError) as exc:
        raise InvalidUrl("That host could not be resolved.") from exc
    return tuple(sorted({str(info[4][0]) for info in infos}))


async def ensure_public_url(
    url: str,
    *,
    resolver: Optional[Resolver] = None,
) -> tuple[str, ...]:
    """Raise unless ``url`` is http(s) and every resolved address is public.

    Returns the resolved addresses (empty when the DNS check is disabled or the
    host is a literal). Raises ``InvalidUrl`` / ``UnsafeUrl`` otherwise.
    """
    if not enforcement_enabled():
        # Explicit opt-out (tests, or SSRF_ENFORCE=0 on a trusted network).
        return ()
    host = check_url_static(url)
    if not getattr(config, "SSRF_DNS_CHECK", True):
        return ()

    try:
        ipaddress.ip_address(host)
        return (host,)  # literal already validated by check_url_static
    except ValueError:
        pass

    port = 443 if urlparse(url).scheme.lower() == "https" else 80
    if resolver is None:
        addresses = await _default_resolver(host, port)
    else:
        resolved = resolver(host, port)
        if inspect.isawaitable(resolved):
            resolved = await resolved
        addresses = tuple(resolved)
    if not addresses:
        raise InvalidUrl("That host could not be resolved.")
    for address in addresses:
        if not ip_is_public(address):
            logger.warning("SSRF blocked: %s resolved to private %s", host, address)
            raise UnsafeUrl("That link points inside the local network.")
    return addresses


def is_safe_url(url: str) -> bool:
    """Sync variant used by the intake handler (DNS is checked separately)."""
    if not enforcement_enabled():
        return True
    try:
        check_url_static(url)
    except (InvalidUrl, UnsafeUrl):
        return False
    return True


__all__ = [
    "ALLOWED_SCHEMES",
    "BLOCKED_HOSTNAMES",
    "BLOCKED_PORTS",
    "check_url_static",
    "enforcement_enabled",
    "ensure_public_url",
    "ip_is_public",
    "is_safe_url",
    "set_enforcement",
]
