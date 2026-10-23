"""The shared "is this host public" test (``acb_common.public_address``).

Fence ``public-address-one-guard``: the gateway URL guard, the image proxy, the
IMAP create route and the IMAP sync connect all use this one module. The
resolver is faked, so no network is used.
"""
from __future__ import annotations

import pytest
from acb_common import public_address as pa

PUBLIC = "93.184.216.34"


def _fake(monkeypatch, addresses):
    monkeypatch.setattr(pa, "resolve", lambda host, port: list(addresses))
    monkeypatch.setattr(pa, "is_local_address", lambda addr: False)


@pytest.mark.parametrize(
    "addr", ["127.0.0.1", "169.254.169.254", "10.1.2.3", "192.168.0.9", "172.16.0.1",
             "::1", "0.0.0.0", "224.0.0.1", "fe80::1", "fd00::1", "::ffff:10.0.0.1"])
def test_a_private_address_is_refused(monkeypatch, addr):
    _fake(monkeypatch, [addr])
    with pytest.raises(pa.PublicAddressRefused):
        pa.pinned_public_address("mail.example.test", 993)
    assert pa.host_is_public("mail.example.test") is False


def test_a_name_that_resolves_to_a_private_address_is_refused(monkeypatch):
    _fake(monkeypatch, ["10.0.0.7"])
    with pytest.raises(pa.PublicAddressRefused) as err:
        pa.pinned_public_address("innocent.example.test", 993)
    assert err.value.reason == "not public"


def test_one_private_address_among_public_ones_refuses_the_host(monkeypatch):
    _fake(monkeypatch, [PUBLIC, "127.0.0.1"])
    with pytest.raises(pa.PublicAddressRefused):
        pa.pinned_public_address("mixed.example.test", 993)


def test_a_public_host_passes_and_returns_the_first_address(monkeypatch):
    _fake(monkeypatch, [PUBLIC, "93.184.216.35"])
    assert pa.pinned_public_address("mail.example.test", 993) == PUBLIC
    assert pa.host_is_public("mail.example.test") is True


def test_a_host_that_does_not_resolve_is_refused(monkeypatch):
    def boom(host, port):
        raise OSError("no such host")

    monkeypatch.setattr(pa, "resolve", boom)
    with pytest.raises(pa.PublicAddressRefused) as err:
        pa.pinned_public_address("gone.example.test", 993)
    assert err.value.reason == "unresolvable"


def test_the_gateway_guard_uses_the_shared_module():
    from gateway import outbound_guard as g
    from gateway.routes.email.transport import attachments

    assert g._pa is pa
    assert attachments.host_is_public is pa.host_is_public
