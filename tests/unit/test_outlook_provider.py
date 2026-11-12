"""CE-10b: the Outlook provider against a fake Http (no network, no DNS)."""
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from gateway.calendar_sync import outlook
from gateway.calendar_sync.provider import (
    CursorExpired, NeedsReconnect, OutEvent, ProviderError, Window,
)
from gateway.crm_sources.base import SourceCredential
from gateway.kb_sources.http import HttpAnswer

FIX = Path(__file__).parent / "fixtures" / "outlook_calendar"
NOW = datetime(2026, 11, 12, tzinfo=UTC)


def fx(name):
    return (FIX / name).read_bytes()


class FakeHttp:
    def __init__(self, routes):
        self.routes = routes  # list of (method, url-substring, status, body bytes)
        self.calls = []

    def _find(self, method, url):
        for m, frag, status, body in self.routes:
            if m == method and frag in url:
                return HttpAnswer(status_code=status, body=body)
        raise AssertionError(f"unrouted {method} {url}")

    async def get(self, url, *, params=None, headers=None):
        self.calls.append(("GET", url))
        return self._find("GET", url)

    async def post_form(self, url, *, form, headers=None):
        self.calls.append(("POST", url, dict(form)))
        return self._find("POST", url)

    async def send_json(self, method, url, *, body=None, headers=None):
        self.calls.append((method, url, body))
        return self._find(method, url)


def prov(http):
    return outlook.OutlookProvider(
        http, client_id="cid", client_secret="sec",
        redirect_uri="https://app.example/api/calendar/oauth/microsoft/callback",
        clock=lambda: NOW)


def cred(**kw):
    base = dict(access_token="at", refresh_token="rt", expires_at=NOW + timedelta(hours=1))
    return SourceCredential(**{**base, **kw})


def run(coro):
    return asyncio.run(coro)


def test_consent_url_has_calendar_scope_offline_access_and_state():
    url = prov(FakeHttp([])).consent_url(state="S", login_hint="a@b.co")
    q = parse_qs(urlsplit(url).query)
    assert "/common/oauth2/v2.0/authorize" in url
    assert q["state"] == ["S"] and q["login_hint"] == ["a@b.co"]
    scope = q["scope"][0]
    assert "offline_access" in scope and "Calendars.ReadWrite" in scope
    assert "Mail" not in scope


def test_exchange_refuses_missing_scope_and_missing_refresh_token():
    def tok(**b):
        return FakeHttp([("POST", "/token", 200, json.dumps(
            {"access_token": "a", "expires_in": 3600, **b}).encode())])
    with pytest.raises(ProviderError) as e:
        run(prov(tok(refresh_token="r", scope="User.Read")).exchange_code("c"))
    assert e.value.code == "scope_missing"
    with pytest.raises(ProviderError) as e:
        run(prov(tok(scope="Calendars.ReadWrite User.Read")).exchange_code("c"))
    assert e.value.code == "no_refresh_token"
    ok = run(prov(tok(refresh_token="r", scope="Calendars.ReadWrite User.Read")).exchange_code("c"))
    assert ok.refresh_token == "r"


def test_refresh_keeps_the_rotated_refresh_token_and_dead_grant_reconnects():
    h = FakeHttp([("POST", "/token", 200, json.dumps(
        {"access_token": "new", "refresh_token": "rt2", "expires_in": 3600}).encode())])
    new = run(prov(h).refresh(cred()))
    assert (new.access_token, new.refresh_token) == ("new", "rt2")
    dead = FakeHttp([("POST", "/token", 400, b'{"error":"invalid_grant"}')])
    with pytest.raises(NeedsReconnect):
        run(prov(dead).refresh(cred()))


def test_exchange_refuses_a_grant_that_holds_mail_scope():
    h = FakeHttp([("POST", "/token", 200, json.dumps(
        {"access_token": "a", "refresh_token": "r",
         "scope": "Calendars.ReadWrite Mail.Read User.Read"}).encode())])
    with pytest.raises(ProviderError) as e:
        run(prov(h).exchange_code("c"))
    assert e.value.code == "scope_extra"


def test_a_truncated_reply_fails_closed_and_no_default_calendar_fails_not_prunes():
    class Cut(FakeHttp):
        async def get(self, url, *, params=None, headers=None):
            return HttpAnswer(status_code=200, body=b'{"value":[', truncated=True)
    with pytest.raises(ProviderError) as e:
        run(prov(Cut([])).list_calendars(cred()))
    assert e.value.code == "response_too_large"
    h = FakeHttp([("GET", "/me/calendars", 200, b'{"value":[{"id":"B","name":"Shared"}]}')])
    with pytest.raises(ProviderError) as e:
        run(prov(h).sync(cred(), None, Window.around(NOW)))
    assert e.value.code == "no_calendar"


def test_every_graph_and_token_request_sends_identity_encoding():
    h = FakeHttp([("GET", "/me/calendars", 200, b'{"value":[]}')])
    seen = {}
    orig = h.get
    async def spy(url, *, params=None, headers=None):
        seen.update(headers or {})
        return await orig(url, params=params, headers=headers)
    h.get = spy
    run(prov(h).list_calendars(cred()))
    assert seen["Accept-Encoding"] == "identity"


def test_first_sync_is_full_skips_metorite_calendar_and_returns_cursor_map():
    h = FakeHttp([
        ("GET", "skiptoken=", 200, fx("delta_page2.json")),
        ("GET", "/me/calendars/AAA/calendarView/delta", 200, fx("delta_page1.json")),
        ("GET", "/me/calendars/BBB/calendarView/delta", 200,
         b'{"value":[],"@odata.deltaLink":"https://graph.microsoft.com/v1.0/me/calendars/BBB/calendarView/delta?$deltatoken=B"}'),
        ("GET", "/me/calendars", 200, fx("calendars.json")),
    ])
    # route order: the delta URLs also contain /me/calendars, so put them first
    res = run(prov(h).sync(cred(), None, Window.around(NOW)))
    assert res.full is True
    ids = [c.external_id for c in res.changes]
    assert ids.count("EV5") == 1 and {"EV1", "EV4"} <= set(ids)
    assert [c for c in res.changes if c.external_id == "EV5"][0].deleted
    cursor = json.loads(res.next_cursor)
    assert set(cursor) == {"AAA"}  # default calendar only; no shared, no Metorite
    assert not any("/CCC/" in c[1] for c in h.calls)


def test_second_sync_uses_the_stored_delta_link_and_is_not_full():
    link = "https://graph.microsoft.com/v1.0/me/calendars/AAA/calendarView/delta?$deltatoken=X"
    h = FakeHttp([
        ("GET", "deltatoken=X", 200,
         b'{"value":[],"@odata.deltaLink":"https://graph.microsoft.com/v1.0/me/calendars/AAA/calendarView/delta?$deltatoken=Y"}'),
        ("GET", "/me/calendars", 200, b'{"value":[{"id":"AAA","name":"Calendar","isDefaultCalendar":true}]}'),
    ])
    res = run(prov(h).sync(cred(), json.dumps({"AAA": link}), Window.around(NOW)))
    assert res.full is False and res.changes == []
    assert "deltatoken=Y" in json.loads(res.next_cursor)["AAA"]


def test_dead_delta_link_raises_cursor_expired():
    h = FakeHttp([
        ("GET", "deltatoken=X", 410, b'{"error":{"code":"syncStateNotFound"}}'),
        ("GET", "/me/calendars", 200, b'{"value":[{"id":"AAA","name":"Calendar","isDefaultCalendar":true}]}'),
    ])
    with pytest.raises(CursorExpired):
        run(prov(h).sync(cred(), json.dumps({"AAA": "https://graph.microsoft.com/x?$deltatoken=X"}),
                         Window.around(NOW)))


def test_401_needs_reconnect_and_403_is_a_code_not_a_token():
    h = FakeHttp([("GET", "/me/calendars", 401, b"{}")])
    with pytest.raises(NeedsReconnect):
        run(prov(h).list_calendars(cred()))
    h = FakeHttp([("GET", "/me/calendars", 403, b'{"error":{"code":"ErrorAccessDenied"}}')])
    with pytest.raises(ProviderError) as e:
        run(prov(h).list_calendars(cred()))
    assert e.value.code == "ErrorAccessDenied" and "at" != str(e.value)


def test_create_upsert_delete_and_delete_missing_is_success():
    h = FakeHttp([
        ("POST", "/me/calendars/CCC/events", 201,
         b'{"id":"E1","@odata.etag":"W/\\"e\\"","webLink":"https://o/e1"}'),
        ("PATCH", "/me/events/E1", 200, b'{"id":"E1"}'),
        ("DELETE", "/me/events/E1", 404, b"{}"),
        ("POST", "/me/calendars", 201, b'{"id":"CCC","name":"Metorite"}'),
    ])
    p = prov(h)
    assert run(p.create_calendar(cred(), "Metorite")) == "CCC"
    ev = OutEvent(title="Task", starts_at=NOW, ends_at=NOW + timedelta(hours=1),
                  task_url="https://app/t/1")
    out = run(p.upsert_event(cred(), "CCC", ev, None))
    assert out.external_id == "E1" and out.url == "https://o/e1"
    run(p.upsert_event(cred(), "CCC", ev, "E1"))
    assert h.calls[-1][0] == "PATCH"
    assert "https://app/t/1" in h.calls[1][2]["body"]["content"]
    run(p.delete_event(cred(), "CCC", "E1"))  # 404 is success


def test_revoke_does_nothing_so_the_mail_grant_stays():
    assert run(prov(FakeHttp([])).revoke(cred())) is False
