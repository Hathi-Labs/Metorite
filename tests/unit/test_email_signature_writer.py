"""WS-17 SG-3a — the writer of a mailbox signature (D-EM-64, D-EM-71).

Spec: ``project-docs/specs/email_app_master_plan.md`` §23.3.10, §23.4 and
§23.6 ("SG-3a as built").

``GET`` and ``PUT /email/signature/settings`` (``signature_settings.py``).
This file pins, with no database:

* **Dark.** With ``EMAIL_SIGNATURE_TEMPLATES_ORGS`` off, both routes answer
  404 to any request, a bad body too, and open no session.
* **The owner check.** Both routes read the mailbox through
  ``resolve_account_filter(required=True)`` and call
  ``_assert_account_owner(level="send")`` in the session. A caller with no
  send access gets 404, and no statement writes.
* **The image rule.** An image is stored only at
  ``public_base_url() + "/email/signature-image/" + <bound tenant> + "/"``,
  in the renderer's hosted shape. Another host, another org, ``http:``,
  ``data:`` and an unset base each give 422 with a sentence.
* **The body.** ``company``, a ``fields`` object past 16384 bytes, a
  ``fields`` value that is not an object, a bad layout, a bad
  ``reply_mode`` and an unknown key each give 422. A value that the database
  refuses gives a 4xx, never 500.
* **The answer.** The PUT answer holds the saved row, and its ``html`` is the
  signature that the send of the mailbox carries.
* **One seam.** The module imports ``_tenant_session`` and opens no connection
  of its own.

``test_email_signature_settings_r8.py`` runs the SQL on a real Postgres (R8).
"""
from __future__ import annotations

import ast
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args
from unittest.mock import AsyncMock

import pytest
from acb_auth import get_current_user, require_authenticated
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common import get_settings
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from gateway.routes import email as email_pkg
from gateway.routes.email import account_ref, signature_preview, signature_render
from gateway.routes.email import signature as sig
from gateway.routes.email import signature_settings as ss
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError

from tests.unit.test_email_signature_resolver import _Db as _ResolverDb
from tests.unit.test_email_signature_resolver import _Result, _send

_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _ROOT / "apps/services/gateway/gateway/routes/email/signature_settings.py"
_SIGNATURE_PY = _ROOT / "apps/services/gateway/gateway/routes/email/signature.py"
_ORG = "5b0e7c1d-2a3f-4e5d-8c6b-7a8f9e0d1c2b"
_OTHER_ORG = "9c8d7e6f-5a4b-4c3d-8e2f-1a0b9c8d7e6f"
_ACC = "0b6f8d1e-1c4a-4f7e-9a51-3d2c1b0a9e88"
_ME = "jane@sg3a.test"
_BASE = "https://api.metorite.test"
_TOKEN = "A" * 43
_FIELDS = {"name": "Jane Doe", "title": "Head of Sales", "company": "Acme Works",
           "phone": "+91 98450 12345", "accent": "#1a73e8"}
_USER = UserContext(email=_ME, role=UserRole.EMPLOYEE, organization_id=_ORG,
                    access=EffectiveAccess(role_granted=frozenset({"feature:email"})))


def _image(org: str = _ORG, base: str = _BASE, ext: str = "png", **over: Any) -> dict:
    return {"url": f"{base}/email/signature-image/{org}/{_TOKEN}.{ext}",
            "width": 120, "height": 60, **over}


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "email_signature_templates_orgs", value,
                        raising=False)


class _PgError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


class _Db(_ResolverDb):
    """The resolver's session double, plus the upsert of the writer.

    The upsert answers the row it got, as ``RETURNING`` does, and the next
    read of ``email_signature_settings`` gives that row. ``fail`` makes the
    upsert raise, as a refusal of the database does."""

    def __init__(self, stored: str | None = "<p>Old custom</p>", settings: Any = None,
                 fail: BaseException | None = None) -> None:
        super().__init__(stored, settings)
        self.params: list[dict] = []
        self.fail = fail

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        flat = " ".join(str(statement).split())
        if flat.startswith("INSERT INTO email_signature_settings"):
            self.sql.append(flat)
            self.params.append(dict(params or {}))
            if self.fail is not None:
                raise self.fail
            row = SimpleNamespace(mode=params["mode"], layout=params["layout"],
                                  fields=json.loads(params["fields"]),
                                  reply_mode=params["reply_mode"])
            self.settings = row
            return _Result(row)
        return await super().execute(statement, params)

    def writes(self) -> list[str]:
        return [s for s in self.sql if s.startswith(("INSERT", "UPDATE", "DELETE"))]


class _Sessions:
    """Stands in for ``_tenant_session``. Each call is one opened session."""

    def __init__(self, db: _Db) -> None:
        self.db = db
        self.opened = 0

    def __call__(self, *_a: Any, **_k: Any) -> Any:
        self.opened += 1

        @asynccontextmanager
        async def _cm() -> Any:
            yield self.db

        return _cm()


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The flag on, the bound tenant, an https base, a session double and an
    owner check that passes."""
    _flag(monkeypatch, "*")
    monkeypatch.setenv("GATEWAY_PUBLIC_URL", _BASE + "/")
    monkeypatch.setattr(ss, "current_tenant", lambda: _ORG)
    db = _Db()
    sessions = _Sessions(db)
    owner = AsyncMock()
    monkeypatch.setattr(ss, "_tenant_session", sessions)
    monkeypatch.setattr(ss, "_assert_account_owner", owner)
    return SimpleNamespace(db=db, sessions=sessions, owner=owner)


def _req(**over: Any) -> ss.SignatureSettingsRequest:
    body = {"account_id": _ACC, "mode": "template", "layout": "classic",
            "fields": dict(_FIELDS), "reply_mode": "full", **over}
    return ss.SignatureSettingsRequest(**body)


async def _put(**over: Any) -> dict[str, Any]:
    return await ss.put_signature_settings(_req(**over), user=_USER)


def _client() -> TestClient:
    """The real email router, mounted as ``gateway/main.py`` mounts it."""
    app = FastAPI(dependencies=[require_authenticated(public=())])
    app.include_router(email_pkg.router)
    app.dependency_overrides[get_current_user] = lambda: _USER
    return TestClient(app)


_GOOD_BODY = {"account_id": _ACC, "mode": "template", "layout": "classic",
              "fields": _FIELDS, "reply_mode": "short"}


# ── 1. Dark: 404 with the flag off, before the body ─────────────────────────


@pytest.mark.parametrize("body", [
    _GOOD_BODY,
    {"bogus": 1},
    {**_GOOD_BODY, "organization_id": _OTHER_ORG},
    {**_GOOD_BODY, "fields": "x"},
])
def test_with_the_flag_off_both_routes_answer_404_to_any_request(
    monkeypatch: pytest.MonkeyPatch, wired: SimpleNamespace, body: dict,
) -> None:
    _flag(monkeypatch, "")
    client = _client()
    assert client.put("/email/signature/settings", json=body).status_code == 404
    assert client.get("/email/signature/settings",
                      params={"account_id": _ACC}).status_code == 404
    assert client.get("/email/signature/settings").status_code == 404
    assert wired.sessions.opened == 0 and wired.db.sql == []


def test_with_the_flag_on_the_same_bad_body_is_a_422(
    wired: SimpleNamespace,
) -> None:
    """So the 404 above comes from the flag, not from the body."""
    client = _client()
    for body in ({"bogus": 1}, {**_GOOD_BODY, "organization_id": _OTHER_ORG}):
        assert client.put("/email/signature/settings", json=body).status_code == 422
    assert client.get("/email/signature/settings").status_code == 422
    assert wired.db.writes() == []


async def test_a_direct_call_is_dark_too(
    monkeypatch: pytest.MonkeyPatch, wired: SimpleNamespace,
) -> None:
    _flag(monkeypatch, "")
    for call in (lambda: _put(), lambda: ss.get_signature_settings(
            account_id=_ACC, user=_USER)):
        with pytest.raises(HTTPException) as caught:
            await call()
        assert caught.value.status_code == 404
    assert wired.sessions.opened == 0


def test_the_flag_is_on_for_the_org_of_the_list_only(
    monkeypatch: pytest.MonkeyPatch, wired: SimpleNamespace,
) -> None:
    _flag(monkeypatch, _OTHER_ORG)
    monkeypatch.setattr("acb_common.db.current_tenant", lambda: _ORG)
    assert _client().put("/email/signature/settings", json=_GOOD_BODY).status_code == 404


# ── 2. The owner check ──────────────────────────────────────────────────────


async def test_the_put_proves_send_access_before_it_writes(wired: SimpleNamespace) -> None:
    out = await _put()
    assert wired.owner.await_args.args[1:] == (_ACC, _ME)
    assert wired.owner.await_args.kwargs == {"level": "send"}
    assert wired.sessions.opened == 1
    assert out["mode"] == "template" and wired.db.writes()


@pytest.mark.parametrize("route", ["get", "put"])
async def test_a_caller_with_no_send_access_gets_404_and_nothing_is_written(
    wired: SimpleNamespace, route: str,
) -> None:
    wired.owner.side_effect = HTTPException(status_code=404, detail="Account not found")
    with pytest.raises(HTTPException) as caught:
        if route == "put":
            await _put()
        else:
            await ss.get_signature_settings(account_id=_ACC, user=_USER)
    assert caught.value.status_code == 404
    assert wired.owner.await_args.kwargs == {"level": "send"}
    assert wired.db.writes() == []
    assert not any("email_signature_settings" in s for s in wired.db.sql)


@pytest.mark.parametrize("ref", ["all", "", "placeholder"])
async def test_the_account_is_read_through_the_one_reader(
    wired: SimpleNamespace, ref: str,
) -> None:
    """``required=True``: "all" and a value of no shape are 422, before any
    owner check or write."""
    for call in (lambda: _put(account_id=ref),
                 lambda: ss.get_signature_settings(account_id=ref, user=_USER)):
        with pytest.raises(HTTPException) as caught:
            await call()
        assert caught.value.status_code == 422
    assert wired.owner.await_count == 0 and wired.db.writes() == []


async def test_an_own_address_resolves_to_its_mailbox(
    wired: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple] = []

    async def _resolve(db: Any, raw: str, owner: str, **kw: Any) -> str:
        seen.append((raw, owner, kw))
        return _ACC

    monkeypatch.setattr(ss, "resolve_account_filter", _resolve)
    out = await _put(account_id="Jane@Sg3a.test")
    assert seen == [("Jane@Sg3a.test", _ME, {"required": True})]
    assert out["account_id"] == _ACC


# ── 3. The image rule ───────────────────────────────────────────────────────


async def test_a_hosted_image_of_the_bound_tenant_is_stored(wired: SimpleNamespace) -> None:
    fields = {**_FIELDS, "logo": _image(), "photo": _image(ext="jpg")}
    out = await _put(fields=fields)
    assert out["fields"]["logo"] == _image()
    assert _image()["url"] in out["html"]


@pytest.mark.parametrize(("value", "words"), [
    (_image(base="https://evil.test"), "not an image of your organization"),
    (_image(base=_BASE + ".evil.test"), "not an image of your organization"),
    (_image(org=_OTHER_ORG), "not an image of your organization"),
    (_image(org=_ORG.upper()), "not an image of your organization"),
    ({**_image(), "url": f"{_BASE}/email/signature-image/{_ORG}/x/email/"
                         f"signature-image/{_OTHER_ORG}/{_TOKEN}.png"},
     "not an image of your organization"),
    (_image(base="http://api.metorite.test"), "does not use https"),
    ({"url": "data:image/png;base64,iVBORw0KGgo=", "width": 10, "height": 10},
     "embedded image"),
    ("data:image/png;base64,iVBORw0KGgo=", "embedded image"),
    ({"url": "javascript:alert(1)", "width": 10, "height": 10}, "does not use https"),
    (_image(width=None), "not an uploaded image"),
    (_image(height=9000), "not an uploaded image"),
    (_image(ext="gif"), "not an uploaded image"),
    ({**_image(), "url": _image()["url"] + "?x=1"}, "not an uploaded image"),
    (_image()["url"], "not an uploaded image"),
    ({"width": 10}, "not an uploaded image"),
    (42, "not an uploaded image"),
])
@pytest.mark.parametrize("key", ss.IMAGE_FIELDS)
async def test_an_image_that_breaks_the_rule_is_a_422_that_names_the_next_step(
    wired: SimpleNamespace, value: Any, words: str, key: str,
) -> None:
    with pytest.raises(HTTPException) as caught:
        await _put(fields={**_FIELDS, key: value})
    assert caught.value.status_code == 422
    detail = caught.value.detail
    assert words in detail and key in detail, detail
    assert "Upload the picture with the image button" in detail
    assert wired.db.writes() == [] and wired.sessions.opened == 0


@pytest.mark.parametrize("base", [None, "", "http://localhost:8000", "https://"])
async def test_with_no_https_base_an_image_is_refused_and_says_why(
    wired: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, base: str | None,
) -> None:
    if base is None:
        monkeypatch.delenv("GATEWAY_PUBLIC_URL", raising=False)
    else:
        monkeypatch.setenv("GATEWAY_PUBLIC_URL", base)
    with pytest.raises(HTTPException) as caught:
        await _put(fields={**_FIELDS, "logo": _image()})
    assert caught.value.status_code == 422
    assert "cannot host signature images" in caught.value.detail
    assert "GATEWAY_PUBLIC_URL" not in caught.value.detail
    assert wired.db.writes() == []
    # With no image, the save goes on.
    assert (await _put())["mode"] == "template"


async def test_with_no_bound_tenant_an_image_is_refused(
    wired: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ss, "current_tenant", lambda: None)
    with pytest.raises(HTTPException) as caught:
        await _put(fields={**_FIELDS, "logo": _image()})
    assert caught.value.status_code == 422


@pytest.mark.parametrize("empty", [None, "", "  ", {}, []])
async def test_an_empty_image_field_is_no_image(wired: SimpleNamespace, empty: Any) -> None:
    out = await _put(fields={**_FIELDS, "logo": empty})
    assert "<img" not in out["html"]


def test_the_image_rule_is_pure_and_takes_the_org_as_an_argument() -> None:
    fields = {"logo": _image()}
    assert ss.image_refusal(fields, base=_BASE, org=_ORG) is None
    assert ss.image_refusal(fields, base=_BASE, org=_OTHER_ORG) is not None
    assert ss.image_refusal(fields, base=None, org=_ORG) is not None
    assert ss.IMAGE_PATH == "/email/signature-image/"


# ── 4. The body ─────────────────────────────────────────────────────────────


async def test_company_is_refused_until_sg4(wired: SimpleNamespace) -> None:
    with pytest.raises(HTTPException) as caught:
        await _put(mode="company")
    assert caught.value.status_code == 422
    assert caught.value.detail == ss.COMPANY_NOT_YET
    assert wired.sessions.opened == 0


async def test_a_template_needs_a_layout_and_a_field_to_show(wired: SimpleNamespace) -> None:
    with pytest.raises(HTTPException) as caught:
        await _put(layout=None)
    assert (caught.value.status_code, caught.value.detail) == (422, ss.LAYOUT_NEEDED)
    with pytest.raises(HTTPException) as caught:
        await _put(fields={"name": "   ", "accent": "#123456"})
    assert (caught.value.status_code, caught.value.detail) == (422, ss.NOTHING_TO_SHOW)
    assert wired.db.writes() == []


async def test_a_custom_row_keeps_its_layout_and_fields(wired: SimpleNamespace) -> None:
    """"Edit as HTML" (SG-3b) sets ``custom`` and keeps the layout."""
    out = await _put(mode="custom")
    assert (out["mode"], out["layout"], out["fields"]) == ("custom", "classic", _FIELDS)
    out = await _put(mode="custom", layout=None, fields={})
    assert (out["layout"], out["fields"], out["html"], out["text"]) == (None, {}, "", "")


@pytest.mark.parametrize("over", [
    {"fields": {"legal": "x" * (signature_preview.MAX_FIELDS_BYTES + 1)}},
    {"fields": ["name"]},
    {"fields": "Jane"},
    {"fields": None},
    {"fields": {"x": float("nan")}},
    {"fields": {"x": float("inf")}},
    {"layout": "fancy"},
    {"mode": "html"},
    {"reply_mode": "half"},
    {"organization_id": _OTHER_ORG},
    {"tenant": _OTHER_ORG},
    {"signature": "<p>x</p>"},
])
def test_a_bad_body_is_refused_by_the_model(over: dict) -> None:
    with pytest.raises(ValidationError):
        _req(**over)


def test_each_key_is_required_but_layout_and_fields() -> None:
    for key in ("account_id", "mode", "reply_mode"):
        body = {"account_id": _ACC, "mode": "custom", "reply_mode": "full"}
        del body[key]
        with pytest.raises(ValidationError):
            ss.SignatureSettingsRequest(**body)
    assert ss.SignatureSettingsRequest(
        account_id=_ACC, mode="custom", reply_mode="none").fields == {}


def test_the_size_bound_is_the_bound_of_the_preview_and_the_check() -> None:
    assert signature_preview.MAX_FIELDS_BYTES == 16384
    at = {"legal": "x" * (16384 - len('{"legal": ""}'))}
    assert _req(fields=at).fields == at
    with pytest.raises(ValidationError):
        _req(fields={"legal": at["legal"] + "x"})


def test_a_nul_and_a_lone_surrogate_become_storable() -> None:
    req = _req(fields={**_FIELDS, "title": "A\x00B\ud800"})
    assert req.fields["title"] == "A�B�"


def test_a_bad_body_through_the_router_is_a_4xx(wired: SimpleNamespace) -> None:
    client = _client()
    for body in ({**_GOOD_BODY, "fields": {"legal": "x" * 20000}},
                 {**_GOOD_BODY, "fields": [1]}, {**_GOOD_BODY, "layout": "fancy"},
                 {**_GOOD_BODY, "reply_mode": "half"}, {**_GOOD_BODY, "x": 1},
                 {**_GOOD_BODY, "mode": "company"}):
        answer = client.put("/email/signature/settings", json=body)
        assert answer.status_code == 422, (body, answer.text)
    answer = client.put("/email/signature/settings", content=b"{not json",
                        headers={"content-type": "application/json"})
    assert 400 <= answer.status_code < 500
    assert wired.db.writes() == []


def test_the_literals_are_the_vocabularies_of_the_resolver() -> None:
    model = ss.SignatureSettingsRequest.model_fields
    assert get_args(model["mode"].annotation) == sig.SIGNATURE_MODES
    assert get_args(model["reply_mode"].annotation) == sig.REPLY_MODES
    layouts = [a for a in get_args(model["layout"].annotation) if a is not type(None)]
    assert get_args(layouts[0]) == signature_render.LAYOUTS


# ── 5. A refusal of the database is a 4xx ───────────────────────────────────


@pytest.mark.parametrize(("state", "code"), [
    ("40P01", 409), ("40001", 409), ("55P03", 409),
    ("23514", 422), ("22P05", 422), ("22P02", 422), ("23503", 404),
])
async def test_a_refusal_of_the_database_is_a_4xx(
    wired: SimpleNamespace, state: str, code: int,
) -> None:
    wired.db.fail = DBAPIError("INSERT", {}, _PgError(state))
    with pytest.raises(HTTPException) as caught:
        await _put()
    assert caught.value.status_code == code
    assert isinstance(caught.value.detail, str) and caught.value.detail.strip()


async def test_any_other_fault_stays_a_fault(wired: SimpleNamespace) -> None:
    wired.db.fail = DBAPIError("INSERT", {}, _PgError("42501"))
    with pytest.raises(DBAPIError):
        await _put()


# ── 6. The answer, and the send ─────────────────────────────────────────────


async def test_the_put_answer_is_the_saved_row_and_its_render(wired: SimpleNamespace) -> None:
    out = await _put(reply_mode="short")
    html, text = signature_render.render("classic", _FIELDS)
    assert out == {"account_id": _ACC, "mode": "template", "layout": "classic",
                   "fields": _FIELDS, "reply_mode": "short", "html": html, "text": text}


@pytest.mark.parametrize("layout", signature_render.LAYOUTS)
async def test_the_put_answer_equals_the_signature_of_the_send(
    wired: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, layout: str,
) -> None:
    """The html of the answer is what the resolver gives each reader, and what
    ``POST /email/send`` puts in the mail."""
    out = await _put(layout=layout, fields={**_FIELDS, "logo": _image(),
                                            "photo": _image(ext="jpg")})
    db = wired.db
    assert await sig.resolve_signature(db, _ACC, "new") == out["html"]
    assert sig.signature_text(out["html"]) == out["text"]
    sent = await _send(monkeypatch, db, "Hi Kim,\n\nThe quote stands.", None)
    assert out["html"] in sent["body_html"]
    assert out["text"] in sent["body_text"]
    assert "Old custom" not in sent["body_html"]


async def test_short_is_stored_as_given(wired: SimpleNamespace) -> None:
    for reply_mode in sig.REPLY_MODES:
        out = await _put(reply_mode=reply_mode)
        assert out["reply_mode"] == reply_mode
        assert wired.db.params[-1]["reply_mode"] == reply_mode


async def test_the_upsert_takes_no_tenant_from_the_request(wired: SimpleNamespace) -> None:
    await _put()
    [sql] = wired.db.writes()
    assert sql == " ".join(sig.SAVE_SETTINGS_SQL.split())
    assert "ON CONFLICT (account_id) DO UPDATE SET" in sql
    assert "updated_at = now()" in sql
    assert "organization_id" not in sql
    params = wired.db.params[-1]
    assert set(params) == {"aid", "mode", "layout", "fields", "reply_mode"}
    assert json.loads(params["fields"]) == _FIELDS


# ── 7. The GET ──────────────────────────────────────────────────────────────


async def test_a_mailbox_with_no_row_reads_as_custom(wired: SimpleNamespace) -> None:
    out = await ss.get_signature_settings(account_id=_ACC, user=_USER)
    assert out == {"account_id": _ACC, "mode": "custom", "layout": None, "fields": {},
                   "reply_mode": "full", "layouts": list(signature_render.LAYOUTS),
                   "image_upload": True}
    assert wired.owner.await_args.kwargs == {"level": "send"}
    assert wired.db.writes() == []


async def test_the_get_reads_what_the_put_saved(
    wired: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _put(reply_mode="none")
    monkeypatch.delenv("GATEWAY_PUBLIC_URL")
    out = await ss.get_signature_settings(account_id=_ACC, user=_USER)
    assert (out["mode"], out["layout"], out["fields"], out["reply_mode"]) == (
        "template", "classic", _FIELDS, "none")
    assert out["image_upload"] is False


def test_the_two_routes_are_gated_member_routes() -> None:
    from gateway.main import PUBLIC_ROUTES
    from gateway.routes.email.core import router

    found = {(r.path, m) for r in router.routes for m in getattr(r, "methods", ())
             if getattr(r, "path", "") == "/email/signature/settings"}
    assert found == {("/email/signature/settings", "GET"),
                     ("/email/signature/settings", "PUT")}
    assert "/email/signature/settings" not in PUBLIC_ROUTES


# ── 8. One seam: no connection of its own ───────────────────────────────────

#: Each name that opens a database connection or a Redis client by itself.
_CONNECTION_NAMES = frozenset({
    "create_engine", "create_async_engine", "get_db", "_get_db", "get_session",
    "get_session_factory", "_get_session_factory", "connect", "tenant_engine_scope",
    "Redis", "from_url", "get_redis",
})


def connection_sites(source: str) -> list[str]:
    """Each call or import of a name that opens a connection by itself."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(
                f, ast.Attribute) else ""
            if name in _CONNECTION_NAMES:
                found.append(name)
        elif isinstance(node, ast.ImportFrom):
            found += [a.name for a in node.names if a.name in _CONNECTION_NAMES]
            if (node.module or "").split(".")[0] in ("asyncpg", "psycopg", "redis"):
                found.append(node.module or "")
        elif isinstance(node, ast.Import):
            found += [a.name for a in node.names
                      if a.name.split(".")[0] in ("asyncpg", "psycopg", "redis")]
    return found


def test_the_writer_takes_its_session_from_the_seam() -> None:
    source = _MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {(n.module, a.name) for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) for a in n.names}
    assert ("gateway.routes.email.core", "_tenant_session") in imported
    assert connection_sites(source) == []
    assert connection_sites(_SIGNATURE_PY.read_text(encoding="utf-8")) == []
    for fn in ("get_signature_settings", "put_signature_settings"):
        node = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == fn)
        assert "_tenant_session" in ast.unparse(node), fn


@pytest.mark.parametrize("source", [
    "from gateway.db import get_db\n",
    "import asyncpg\nasync def f():\n    return await asyncpg.connect('x')\n",
    "from sqlalchemy import create_engine\nE = create_engine('x')\n",
    "from gateway.routes.email.core import _get_db\n",
    "import redis\n",
])
def test_the_seam_scan_can_go_red(source: str) -> None:
    assert connection_sites(source) != []


def test_the_account_reader_is_the_one_of_account_ref() -> None:
    assert ss.resolve_account_filter is account_ref.resolve_account_filter
