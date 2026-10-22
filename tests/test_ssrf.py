"""SSRF: the URL must be public before anything touches the network.

Static checks (scheme, ports, IP literals) plus the DNS-resolution layer that
kills ``public-name → 127.0.0.1``. Enforcement is forced on here; the engine
tests that deliberately talk to a loopback server override it themselves.
"""

from __future__ import annotations

import pytest

from linktofile.domain.errors import InvalidUrl, UnsafeUrl
from linktofile.shared import security
from linktofile.shared.security import (
    check_url_static,
    ensure_public_url,
    ip_is_public,
    is_safe_url,
)


@pytest.fixture(autouse=True)
def _enforced():
    security.set_enforcement(True)
    yield
    security.set_enforcement(None)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://127.1/x",
        "http://0x7f000001/x",
        "http://2130706433/x",
        "http://localhost:9000/x",
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "http://172.16.0.1/x",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/x",
        "http://[::ffff:127.0.0.1]/x",
        "http://printer.local/x",
        "http://db.internal/x",
        "ftp://example.com/x",
        "http://example.com:6379/x",
        "http://user:pass@example.com/x",
        "http://localhost/x",
    ],
)
def test_private_and_blocked_targets_are_refused(url):
    assert is_safe_url(url) is False
    with pytest.raises((InvalidUrl, UnsafeUrl)):
        check_url_static(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/a",
        "http://example.com/a",
        "https://cdn.example.co.uk:8443/a",
        "https://example.com/a?token=b#frag",
    ],
)
def test_public_targets_pass_the_static_gate(url):
    assert is_safe_url(url) is True
    assert check_url_static(url)


def test_a_normal_ip_literal_is_allowed():
    assert is_safe_url("http://93.184.216.34/x") is True
    assert ip_is_public("93.184.216.34") is True


def test_embedded_credentials_and_odd_ports_fail():
    with pytest.raises(UnsafeUrl):
        check_url_static("http://user:pass@example.com/x")
    with pytest.raises(UnsafeUrl):
        check_url_static("http://example.com:22/x")
    with pytest.raises(InvalidUrl):
        check_url_static("gopher://example.com/x")
    with pytest.raises(InvalidUrl):
        check_url_static("not a url at all")


def test_the_dns_layer_rejects_a_public_name_pointing_at_loopback():
    async def fake_resolver(host, port):
        return ("127.0.0.1",)

    import asyncio

    with pytest.raises(UnsafeUrl):
        asyncio.run(ensure_public_url("https://evil.example.com", resolver=fake_resolver))


def test_the_dns_layer_passes_when_every_address_is_public():
    async def fake_resolver(host, port):
        return ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")

    import asyncio

    addresses = asyncio.run(
        ensure_public_url("https://example.com", resolver=fake_resolver)
    )
    assert addresses == ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")


def test_the_dns_layer_fails_when_any_address_is_private():
    async def fake_resolver(host, port):
        return ("93.184.216.34", "10.1.2.3")

    import asyncio

    with pytest.raises(UnsafeUrl):
        asyncio.run(ensure_public_url("https://mixed.example.com", resolver=fake_resolver))


def test_an_unresolvable_host_is_invalid():
    def raising_resolver(host, port):
        raise OSError("no such host")

    with pytest.raises(Exception):  # noqa: BLE001 - resolver errors surface as InvalidUrl
        ensure_public_url_sync("https://missing.example.com", raising_resolver)


def ensure_public_url_sync(url, resolver):
    import asyncio

    return asyncio.run(ensure_public_url(url, resolver=resolver))


def test_disabling_enforcement_skips_the_gate():
    security.set_enforcement(False)
    try:
        assert is_safe_url("http://127.0.0.1/x") is True
        assert ensure_public_url_sync(
            "http://example.com", lambda host, port: ("127.0.0.1",)
        ) == ()
    finally:
        security.set_enforcement(True)


def test_loopback_in_ipv4_mapped_ipv6_is_caught():
    assert ip_is_public("::ffff:127.0.0.1") is False
    assert ip_is_public("::ffff:8.8.8.8") is True
