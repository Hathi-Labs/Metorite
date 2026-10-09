"""WS-47 WAC-1 — the link table and the link code routes on a REAL database (R8).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §5.2, §5.3, §9.

This suite runs the REAL ``/me/whatsapp-link`` routes against the phase-4
catalog of ``test_h3_rls_promotion_rehearsal``, as its NOSUPERUSER NOBYPASSRLS
role. ``tenant_engine_scope`` points the gateway engine at that role, so every
write the route makes passes FORCE row level security or fails.

R7 fences named here:

* ``wac-migration-idempotent``: a second run of the migration changes no
  column, index, constraint or policy of the table.
* ``wac-code-issue-writes-one-row``: a POST writes one ``pending`` row with
  the bound org, the session member, ``is_current`` false, the hash of the
  returned code, and an expiry 15 minutes ahead. No column holds the code.
* ``wac-new-code-revokes-old``: a second POST revokes the first pending row.
* ``wac-dark-writes-nothing``: switch off, org not listed, no
  ``feature:chat``: no row.
* ``wac-get-is-mine``: the GET shows the caller's own rows in the bound org.
* ``wac-rls-isolates``: a session bound to org B reads no row of org A, and
  cannot write one.
* ``wac-unique-phone``: two active rows for one phone in one org, and two
  current rows for one phone, are refused.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_wac_link_table.py -v -rs
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

import httpx
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from acb_common.db import bind_tenant, clear_tenant
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are fixtures used by name, so the import is
# load-bearing even though ruff reads it as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_NUMBER = "919800000000"
_TABLE = "whatsapp_member_links"


def _migration() -> Path:
    """The WAC-1 migration, found by CONTENT and never by number (R1)."""
    hits = [
        p for p in sorted((_ROOT / "infra" / "postgres").glob("*.sql"))
        if "CREATE TABLE IF NOT EXISTS whatsapp_member_links"
        in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, f"expected one WAC-1 migration, found {hits}"
    return hits[0]


@pytest.fixture(autouse=True)
def _open_channel(promoted, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    s = get_settings()
    monkeypatch.setattr(s, "whatsapp_assistant_enabled", True, raising=False)
    monkeypatch.setattr(s, "whatsapp_assistant_orgs",
                        f"{promoted.org_a},{promoted.org_b}", raising=False)
    monkeypatch.setattr(s, "whatsapp_assistant_display_number", _NUMBER,
                        raising=False)
    # The audit writer is best-effort and uses the sync engine of
    # `acb_graph`, which this suite does not point at the scratch catalog.
    import acb_audit

    monkeypatch.setattr(acb_audit, "record", lambda _event: None)


def _member() -> str:
    return f"m-{uuid.uuid4().hex[:10]}@wac1.test"


def _phone() -> str:
    return "91" + str(uuid.uuid4().int)[:10]


async def _call(p, method: str, path: str, *, org: str, email: str,
                features: tuple[str, ...] = ("feature:chat",)) -> httpx.Response:
    """The REAL route on the app role's engine, with the tenant bound the way
    ``acb_auth.deps`` binds it: from the identity, inside the request."""
    from gateway.routes.whatsapp_channel.link import router

    async def _user() -> UserContext:
        bind_tenant(org)
        return UserContext(email=email, role=UserRole.EMPLOYEE,
                           organization_id=org,
                           access=build_access(list(features)))

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = _user
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://gateway.test") as client:
                return await client.request(method, path)
    finally:
        clear_tenant()


def _rows(admin_engine, email: str) -> list:
    """Every row of one member, read as the superuser (no RLS)."""
    with admin_engine.connect() as c:
        return c.execute(text(
            f"SELECT *, row_to_json({_TABLE})::text AS js, "
            "code_expires_at - now() AS ttl "
            f"FROM {_TABLE} WHERE member_email = :e ORDER BY created_at"),
            {"e": email}).all()


def _count_as(app_url, org: str | None, email: str) -> int:
    eng = create_engine(app_url, future=True)
    try:
        with eng.connect() as c, c.begin():
            if org is not None:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": org})
            return c.execute(text(
                f"SELECT count(*) FROM {_TABLE} WHERE member_email = :e"),
                {"e": email}).scalar_one()
    finally:
        eng.dispose()


def _seed(admin_engine, **cols) -> None:
    names = ", ".join(cols)
    marks = ", ".join(f":{k}" for k in cols)
    with admin_engine.begin() as c:
        c.execute(text(f"INSERT INTO {_TABLE} ({names}) VALUES ({marks})"), cols)


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role, so RLS is bypassed"
    )


# ── wac-migration-idempotent ────────────────────────────────────────────────

def _catalog(admin_engine) -> dict:
    with admin_engine.connect() as c:
        return {
            "rls": tuple(c.execute(text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                f"WHERE oid = '{_TABLE}'::regclass")).one()),
            "columns": c.execute(text(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_name = :t "
                "ORDER BY column_name"), {"t": _TABLE}).all(),
            "indexes": c.execute(text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = :t ORDER BY indexname"), {"t": _TABLE}).all(),
            "constraints": c.execute(text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                f"WHERE conrelid = '{_TABLE}'::regclass ORDER BY conname")).all(),
            "policies": c.execute(text(
                "SELECT policyname, qual, with_check FROM pg_policies "
                "WHERE tablename = :t ORDER BY policyname"), {"t": _TABLE}).all(),
        }


def test_the_table_is_force_rls_with_a_two_sided_policy(promoted) -> None:  # noqa: F811
    cat = _catalog(promoted.admin_engine)
    assert cat["rls"] == (True, True)
    (name, qual, check), = cat["policies"]
    assert name == "whatsapp_member_links_tenant_isolation"
    assert "app.tenant_id" in qual and "app.tenant_id" in check


def test_a_second_run_of_the_migration_changes_nothing(promoted) -> None:  # noqa: F811
    before = _catalog(promoted.admin_engine)
    with promoted.admin_engine.connect() as c:
        with c.connection.dbapi_connection.cursor() as cur:
            cur.execute(_migration().read_text(encoding="utf-8"))
        c.connection.dbapi_connection.commit()
    assert _catalog(promoted.admin_engine) == before


# ── wac-code-issue-writes-one-row ───────────────────────────────────────────

async def test_a_post_writes_one_pending_row_under_the_bound_org(
    promoted, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    p, email = promoted, _member()

    res = await _call(p, "POST", "/me/whatsapp-link/code", org=p.org_a,
                      email=email)

    assert res.status_code == 201, res.text
    code = res.json()["code"]
    (row,) = _rows(p.admin_engine, email)
    assert str(row.organization_id) == p.org_a
    assert row.member_email == email
    assert row.status == "pending"
    assert row.is_current is False
    assert row.wa_id is None and row.linked_at is None
    assert 14 * 60 < row.ttl.total_seconds() <= 15 * 60
    assert row.code_hash == hashlib.sha256(code.encode()).hexdigest()
    assert code not in row.js, "a column holds the plain code"
    assert res.json()["link"] == (
        f"https://wa.me/{_NUMBER}?text=Link%20me%3A%20{code}")


# ── wac-new-code-revokes-old ────────────────────────────────────────────────

async def test_a_second_post_revokes_the_first_pending_row(
    promoted, app_engine,  # noqa: F811
) -> None:
    p, email = promoted, _member()
    first = await _call(p, "POST", "/me/whatsapp-link/code", org=p.org_a,
                        email=email)
    second = await _call(p, "POST", "/me/whatsapp-link/code", org=p.org_a,
                         email=email)
    assert first.status_code == second.status_code == 201

    old, new = _rows(p.admin_engine, email)
    assert (old.status, new.status) == ("revoked", "pending")
    assert old.revoked_at is not None
    assert old.code_hash == hashlib.sha256(first.json()["code"].encode()).hexdigest()
    assert new.code_hash == hashlib.sha256(second.json()["code"].encode()).hexdigest()


async def test_a_code_in_another_org_leaves_the_first_org_alone(
    promoted, app_engine,  # noqa: F811
) -> None:
    """One live code per member PER ORG. A member of two orgs keeps both."""
    p, email = promoted, _member()
    await _call(p, "POST", "/me/whatsapp-link/code", org=p.org_a, email=email)
    await _call(p, "POST", "/me/whatsapp-link/code", org=p.org_b, email=email)
    rows = _rows(p.admin_engine, email)
    assert sorted((str(r.organization_id), r.status) for r in rows) == sorted(
        [(p.org_a, "pending"), (p.org_b, "pending")])


# ── wac-dark-writes-nothing ─────────────────────────────────────────────────

async def test_the_switch_off_writes_no_row(promoted, app_engine,  # noqa: F811
                                            monkeypatch) -> None:
    p, email = promoted, _member()
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_enabled", False,
                        raising=False)
    res = await _call(p, "POST", "/me/whatsapp-link/code", org=p.org_a,
                      email=email)
    assert res.status_code == 404
    assert _rows(p.admin_engine, email) == []


async def test_an_org_not_on_the_list_writes_no_row(promoted, app_engine,  # noqa: F811
                                                    monkeypatch) -> None:
    p, email = promoted, _member()
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_orgs", p.org_a,
                        raising=False)
    res = await _call(p, "POST", "/me/whatsapp-link/code", org=p.org_b,
                      email=email)
    assert res.status_code == 404
    assert _rows(p.admin_engine, email) == []


async def test_no_chat_feature_writes_no_row(promoted, app_engine) -> None:  # noqa: F811
    p, email = promoted, _member()
    res = await _call(p, "POST", "/me/whatsapp-link/code", org=p.org_a,
                      email=email, features=("feature:email",))
    assert res.status_code == 403
    assert _rows(p.admin_engine, email) == []


# ── wac-get-is-mine ─────────────────────────────────────────────────────────

async def test_the_get_returns_only_this_members_links(
    promoted, app_engine,  # noqa: F811
) -> None:
    p, me, other = promoted, _member(), _member()
    mine, theirs, elsewhere = _phone(), _phone(), _phone()
    _seed(p.admin_engine, organization_id=p.org_a, member_email=me,
          wa_id=mine, status="active", is_current=True)
    _seed(p.admin_engine, organization_id=p.org_a, member_email=other,
          wa_id=theirs, status="active")
    _seed(p.admin_engine, organization_id=p.org_b, member_email=me,
          wa_id=elsewhere, status="active")

    res = await _call(p, "GET", "/me/whatsapp-link", org=p.org_a, email=me)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["enabled"] is True and body["display_number"] == _NUMBER
    assert [(lk["organization_id"], lk["status"], lk["is_current"],
             lk["phone_hint"]) for lk in body["links"]] == [
        (p.org_a, "active", True, mine[-4:])]
    assert body["links"][0]["organization_name"] == "h3rls-a"
    raw = json.dumps(body)
    assert theirs not in raw and theirs[-4:] not in raw
    assert other not in raw and elsewhere[-4:] not in raw


async def test_the_get_shows_a_live_code_and_hides_an_expired_one(
    promoted, app_engine,  # noqa: F811
) -> None:
    p, me = promoted, _member()
    _seed(p.admin_engine, organization_id=p.org_a, member_email=me,
          status="revoked", code_hash="0" * 64,
          code_expires_at="2020-01-01T00:00:00Z")
    await _call(p, "POST", "/me/whatsapp-link/code", org=p.org_a, email=me)

    res = await _call(p, "GET", "/me/whatsapp-link", org=p.org_a, email=me)

    (only,) = res.json()["links"]
    assert only["status"] == "pending" and only["expires_at"] is not None


# ── wac-rls-isolates ────────────────────────────────────────────────────────

async def test_a_session_bound_to_org_b_sees_no_row_of_org_a(
    promoted, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    p, email = promoted, _member()
    res = await _call(p, "POST", "/me/whatsapp-link/code", org=p.org_a,
                      email=email)
    assert res.status_code == 201
    assert _count_as(p.app_url, p.org_a, email) == 1
    assert _count_as(p.app_url, p.org_b, email) == 0, (
        "a session of org B reads a link row of org A")
    assert _count_as(p.app_url, None, email) == 0, (
        "an unbound session reads a link row, so the fence above is vacuous")


def test_a_session_bound_to_org_b_cannot_write_a_row_of_org_a(
    promoted, app_engine,  # noqa: F811
) -> None:
    eng = create_engine(promoted.app_url, future=True)
    try:
        with pytest.raises(DBAPIError) as err, eng.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                      {"o": promoted.org_b})
            c.execute(text(
                f"INSERT INTO {_TABLE} (organization_id, member_email, status, "
                "code_hash, code_expires_at) VALUES (CAST(:a AS uuid), :e, "
                "'pending', :h, now())"),
                {"a": promoted.org_a, "e": _member(), "h": "f" * 64})
        assert "row-level security" in str(err.value)
    finally:
        eng.dispose()


# ── wac-unique-phone ────────────────────────────────────────────────────────

def test_two_active_rows_for_one_phone_in_one_org_are_refused(promoted) -> None:  # noqa: F811
    phone = _phone()
    _seed(promoted.admin_engine, organization_id=promoted.org_a,
          member_email=_member(), wa_id=phone, status="active")
    with pytest.raises(IntegrityError) as err:
        _seed(promoted.admin_engine, organization_id=promoted.org_a,
              member_email=_member(), wa_id=phone, status="active")
    assert "uq_whatsapp_member_links_active_phone" in str(err.value)


def test_one_phone_may_be_active_in_two_orgs(promoted) -> None:  # noqa: F811
    """D-WAC-2 as amended: one link per org, for one person."""
    phone, me = _phone(), _member()
    for org in (promoted.org_a, promoted.org_b):
        _seed(promoted.admin_engine, organization_id=org, member_email=me,
              wa_id=phone, status="active")
    assert len(_rows(promoted.admin_engine, me)) == 2


def test_two_current_rows_for_one_phone_are_refused(promoted) -> None:  # noqa: F811
    phone, me = _phone(), _member()
    _seed(promoted.admin_engine, organization_id=promoted.org_a,
          member_email=me, wa_id=phone, status="active", is_current=True)
    with pytest.raises(IntegrityError) as err:
        _seed(promoted.admin_engine, organization_id=promoted.org_b,
              member_email=me, wa_id=phone, status="active", is_current=True)
    assert "uq_whatsapp_member_links_current_phone" in str(err.value)


def test_two_pending_codes_for_one_member_in_one_org_are_refused(promoted) -> None:  # noqa: F811
    me = _member()
    for i in range(2):
        cols = dict(organization_id=promoted.org_a, member_email=me,
                    status="pending", code_hash=f"{i}" * 64,
                    code_expires_at="2099-01-01T00:00:00Z")
        if i == 0:
            _seed(promoted.admin_engine, **cols)
            continue
        with pytest.raises(IntegrityError) as err:
            _seed(promoted.admin_engine, **cols)
    assert "uq_whatsapp_member_links_one_pending" in str(err.value)


@pytest.mark.parametrize(("cols", "check"), [
    ({"status": "active"}, "whatsapp_member_links_active_has_sender"),
    ({"status": "pending"}, "whatsapp_member_links_pending_has_code"),
    ({"status": "pending", "code_hash": "a" * 64,
      "code_expires_at": "2099-01-01T00:00:00Z", "is_current": True},
     "whatsapp_member_links_current_is_active"),
    ({"status": "linked"}, "whatsapp_member_links_status_known"),
    ({"status": "revoked", "wa_id": "+919990000001"},
     "whatsapp_member_links_wa_id_digits"),
])
def test_the_checks_refuse_a_row_that_breaks_a_rule(promoted, cols, check) -> None:  # noqa: F811
    with pytest.raises(IntegrityError) as err:
        _seed(promoted.admin_engine, organization_id=promoted.org_a,
              member_email=_member(), **cols)
    assert check in str(err.value)
