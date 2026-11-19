"""API-3: the egress fence of one API call, hermetic (task 3789, D228).

Spec: ``project-docs/specs/api_connections.md`` §2.1, §2.3 (steps 5 and 7),
§2.3a (decision g) and §2.7. The routes are ``gateway/routes/api_connections.py``.
``outbound_guard.request_https`` runs for real. Only ``send`` and the DNS
lookup are fakes (``tests/unit/_api_call_fakes.py``), so no socket opens.

Mutation table. Each row names a mutation and the test that it breaks.

| # | Mutation | Test that fails |
|---|---|---|
| E1 | the route skips ``_check_args`` | ``test_an_argument_outside_the_row_is_refused_and_nothing_goes`` |
| E2 | ``template.build`` drops the check of ``reserved_keys`` | ``test_a_context_key_as_an_argument_is_refused`` |
| E3 | ``call.check_host`` accepts any host | ``test_a_host_outside_the_template_is_refused`` |
| E4 | ``call.check_host`` skips the client list | ``test_a_host_off_the_client_list_is_refused`` |
| E5 | ``_https`` calls ``outbound_guard.request`` and not ``request_https`` | ``test_request_https_refuses_another_port_and_http`` |
| E6 | ``send`` follows a redirect | ``test_a_redirect_is_refused_and_its_target_gets_nothing`` |
| E7 | ``call.token_is_plain`` passes a CR or an LF | ``test_a_token_with_cr_or_lf_never_reaches_a_header`` |
| E8 | ``CallIn`` takes an extra key | ``test_the_body_names_no_org_and_no_host`` |
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

pytest.importorskip("gateway.routes.api_connections", reason="gateway not installed")

from gateway import outbound_guard
from gateway.connections import api as api_templates
from gateway.connections.api import call
from gateway.connections.api.zoho_books import ZOHO_BOOKS
from gateway.crm_sources.base import UntrustedHost
from gateway.crm_sources.zoho.auth import API_HOSTS

from tests.unit import _api_call_fakes as f

READ = "/connections/api/zoho_books/read"


def _query(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlsplit(url).query)


# ── Step 5: the arguments ────────────────────────────────────────────────────


@pytest.mark.parametrize("args", [
    {"evil": "1"},
    {"path": "/admin"},
    {"url": "https://evil.example.com"},
])
async def test_an_argument_outside_the_row_is_refused_and_nothing_goes(monkeypatch, args) -> None:
    from gateway.connections import shared_token

    _, wire, _ = f.hermetic(monkeypatch)

    async def _never(*a, **k):
        pytest.fail("step 5 runs before the credential is read (step 6)")

    monkeypatch.setattr(shared_token, "fresh_credential", _never)
    r = await f.call(f.user(), "POST", READ, body={"op": "list_invoices", "args": args})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "bad_argument"
    assert wire.sent == []


async def test_a_context_key_as_an_argument_is_refused(monkeypatch) -> None:
    _, wire, _ = f.hermetic(monkeypatch)
    r = await f.call(f.user(), "POST", READ,
                     body=f.read(organization_id=f.BOOKS_ORG_B))
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {"code": "bad_argument", "argument": "organization_id",
                                  "why": "context"}
    assert wire.sent == []


@pytest.mark.parametrize("value", [
    "paid\r\nX-Evil: 1", "paid\nX", "paid&organization_id=60002", "paid#x", "../paid",
])
async def test_a_query_value_outside_its_shape_is_refused(monkeypatch, value) -> None:
    _, wire, _ = f.hermetic(monkeypatch)
    r = await f.call(f.user(), "POST", READ, body=f.read(status=value))
    assert r.status_code == 422, r.text
    assert wire.sent == []


@pytest.mark.parametrize("value", ["../1", "1/2", "1%2f2", "1?x", "..", "abc"])
async def test_a_path_value_cannot_leave_its_place(monkeypatch, value) -> None:
    _, wire, _ = f.hermetic(monkeypatch)
    r = await f.call(f.user(), "POST", READ, body=f.read("get_invoice", invoice_id=value))
    assert r.status_code == 422, r.text
    assert wire.sent == []


async def test_the_gateway_sets_the_context_after_the_arguments(monkeypatch) -> None:
    _, wire, _ = f.hermetic(monkeypatch)
    r = await f.call(f.user(), "POST", READ, body=f.read(status="paid", per_page="5"))
    assert r.status_code == 200, r.text
    (sent,) = wire.sent
    query = _query(sent.url)
    assert [v for k, v in query if k == "organization_id"] == [f.BOOKS_ORG]
    assert ("status", "paid") in query and ("per_page", "5") in query
    assert urlsplit(sent.url).path == "/books/v3/invoices"


async def test_the_body_names_no_org_and_no_host(monkeypatch) -> None:
    _, wire, _ = f.hermetic(monkeypatch)
    for extra in ({"organization_id": f.ORG_B}, {"host": "https://evil.example.com"},
                  {"path": "/x"}, {"method": "DELETE"}):
        r = await f.call(f.user(), "POST", READ, body={**f.read(), **extra})
        assert r.status_code == 422, (extra, r.text)
    assert wire.sent == []


# ── Step 7: the host ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("host", [
    "https://evil.example.com", "http://www.zohoapis.in", "https://www.zohoapis.in:8443",
    "https://www.zohoapis.in.evil.com", "", "https://127.0.0.1",
])
async def test_a_host_outside_the_template_is_refused(monkeypatch, host) -> None:
    rows, wire, _ = f.hermetic(monkeypatch)
    rows.put(f.ORG_A, f.credential(api_domain=host))
    # The client list holds the host too, so only the template check refuses it.
    monkeypatch.setattr(api_templates, "CLIENT_HOSTS", {
        "zoho": frozenset({*api_templates.CLIENT_HOSTS["zoho"], host})})
    r = await f.call(f.user(), "POST", READ, body=f.read())
    assert r.status_code == 502, r.text
    assert r.json()["detail"]["code"] == "untrusted_host"
    assert wire.sent == []


async def test_a_host_off_the_client_list_is_refused(monkeypatch) -> None:
    real = api_templates.CLIENT_HOSTS
    _, wire, _ = f.hermetic(monkeypatch)
    monkeypatch.setattr(api_templates, "CLIENT_HOSTS",
                        {"zoho": frozenset({"https://www.zohoapis.eu"})})
    r = await f.call(f.user(), "POST", READ, body=f.read())
    assert r.status_code == 502, r.text
    assert wire.sent == []
    with pytest.raises(UntrustedHost):
        call.check_host(ZOHO_BOOKS, f.API_HOST, {"zoho": frozenset()})
    assert call.check_host(ZOHO_BOOKS, f.API_HOST, real) == f.API_HOST


async def test_request_https_refuses_another_port_and_http(monkeypatch) -> None:
    wire = f.use_wire(monkeypatch, f.Wire())
    for url in ("https://www.zohoapis.in:8443/books/v3/invoices",
                "http://www.zohoapis.in/books/v3/invoices"):
        with pytest.raises(outbound_guard.OutboundRefused):
            await outbound_guard.request_https("GET", url, headers={})
    assert wire.sent == []


async def test_each_call_goes_through_request_https(monkeypatch) -> None:
    from gateway.routes import api_connections as routes

    seen: list[str] = []
    real = outbound_guard.request_https

    async def _spy(method, url, **kw):
        seen.append(url)
        return await real(method, url, **kw)

    f.hermetic(monkeypatch)
    monkeypatch.setattr(outbound_guard, "request_https", _spy)
    monkeypatch.setattr(outbound_guard, "request",
                        lambda *a, **k: pytest.fail("the plain request is never used"))
    r = await f.call(f.user(), "POST", READ, body=f.read())
    assert r.status_code == 200, r.text
    assert len(seen) == 1 and seen[0].startswith(f.API_HOST + "/")
    assert routes._https.__module__ == routes.__name__


async def test_a_redirect_is_refused_and_its_target_gets_nothing(monkeypatch) -> None:
    f.hermetic(monkeypatch)
    seen = f.mock_wire(monkeypatch, lambda req: httpx.Response(
        302, headers={"Location": "https://evil.example.com/steal"}))
    r = await f.call(f.user(), "POST", READ, body=f.read())
    assert r.status_code == 502, r.text
    assert r.json()["detail"]["code"] == "refused"
    assert "redirect" in r.json()["detail"]["reason"]
    assert len(seen) == 1 and seen[0].headers["host"] == "www.zohoapis.in"


# ── The headers and the credential ───────────────────────────────────────────


async def test_the_headers_are_the_template_ones_only(monkeypatch) -> None:
    _, wire, _ = f.hermetic(monkeypatch)
    r = await f.call(f.user(), "POST", READ, body=f.read())
    assert r.status_code == 200, r.text
    (sent,) = wire.sent
    assert sent.headers == {"Authorization": f"Zoho-oauthtoken {f.S_ACCESS}",
                            "Accept": "application/json", "Accept-Encoding": "identity"}
    assert sent.ip == f.PUBLIC_IP


@pytest.mark.parametrize("token", [f"{f.S_ACCESS}\r\nX-Evil: 1", f"{f.S_ACCESS}\n", "a b", ""])
async def test_a_token_with_cr_or_lf_never_reaches_a_header(monkeypatch, token) -> None:
    rows, wire, _ = f.hermetic(monkeypatch)
    rows.put(f.ORG_A, f.credential(access=token))
    r = await f.call(f.user(), "POST", READ, body=f.read())
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == "credential_unusable"
    assert wire.sent == []
    assert f.S_ACCESS not in r.text


async def test_the_credential_never_leaves_the_client_hosts(monkeypatch) -> None:
    _, wire, _ = f.hermetic(monkeypatch)
    for body in (f.read(), f.read("get_invoice", invoice_id="1001"),
                 f.read(total=True)):
        await f.call(f.user(), "POST", READ, body=body)
    assert wire.sent
    for sent in wire.sent:
        assert sent.host in API_HOSTS
        assert sent.url.startswith("https://")


def test_the_credential_forms_cover_the_encoded_and_scheme_shapes() -> None:
    token = "ab/c+d=e f"
    forms = call.secret_forms([token], scheme="Zoho-oauthtoken")
    for text in (token, "ab%2Fc%2Bd%3De%20f", "ab%2fc%2bd%3de%20f", "ab%2Fc%2Bd%3De+f",
                 f"Zoho-oauthtoken {token}"):
        assert token not in call.scrub(f"x {text} y", forms)
        assert call.scrub(f"x {text} y", forms) == f"x {call.REMOVED} y"
