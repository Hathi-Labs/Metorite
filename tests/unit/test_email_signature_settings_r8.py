"""WS-17 SG-2 — the signature settings table and the resolver under FORCE RLS (R8).

Spec: ``project-docs/specs/email_app_master_plan.md`` §23.2 (D-EM-64,
D-EM-66, D-EM-71), §23.3.3, §23.4 and §23.7.

Runs against a REAL Postgres, through the phase-4-promoted catalog of
``test_h3_rls_promotion_rehearsal.py``, as the non-privileged app role (no
superuser, no BYPASSRLS). It pins:

* ``email_signature_settings`` has ENABLE and FORCE row level security and one
  policy. Organization B cannot read a row of organization A, and the table
  refuses a row stamped with another org (WITH CHECK).
* The CHECKs hold the modes, the layouts and the reply modes, and a row goes
  with its mailbox (ON DELETE CASCADE).
* The resolver's SQL on a real database: a mailbox with no row gives the
  stored bytes unchanged, with the flag off and on (rule 7). A template row
  renders while the flag holds the org, and a row of another org is never
  read. The settings GET and the PUT answer carry the same bytes as main.

Each database class skips cleanly without ``TENANT_LADDER_DATABASE_URL``
(``_DB_GATE``). The migration check and the CI list check need no database.
The migration is found by CONTENT, never by number (R1).
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from gateway.routes.email import AssistantSettingsModel
from gateway.routes.email import signature as sig
from gateway.routes.email import signature_render
from gateway.routes.email.automation import assistant as assistant_mod
from gateway.routes.email.automation import drafting
from gateway.routes.email.core import _tenant_session
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_scheduler_tenancy import _assert_non_priv, _seed_account

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]
_FIELDS = {"name": "Jane Doe", "title": "Head of Sales", "company": "Acme Works",
           "phone": "+91 98450 12345"}
_STORED = "-- \nJane Doe\nAcme Works ✓"


def _migration() -> str:
    [path] = [
        p for p in (_ROOT / "infra" / "postgres").glob("[0-9]*_*.sql")
        if "CREATE TABLE IF NOT EXISTS email_signature_settings"
        in p.read_text(encoding="utf-8")
    ]
    return path.read_text(encoding="utf-8")


def test_the_migration_is_tenant_scoped_with_force_rls() -> None:
    body = _migration()
    assert "ALTER TABLE email_signature_settings ENABLE ROW LEVEL SECURITY" in body
    assert "ALTER TABLE email_signature_settings FORCE  ROW LEVEL SECURITY" in body
    assert "USING      (organization_id = current_setting('app.tenant_id', true)::uuid)" in body
    assert "WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid)" in body
    assert "account_id       UUID NOT NULL REFERENCES email_accounts (id) ON DELETE CASCADE" in body
    assert "mode IN ('custom', 'template', 'company')" in body
    assert "reply_mode IN ('full', 'short', 'none')" in body
    assert "reply_mode       TEXT NOT NULL DEFAULT 'full'" in body
    # Rule 7: no backfill, and the old store stays as it is.
    assert "INSERT INTO" not in body and "UPDATE " not in body
    assert "email_assistant_settings ADD" not in body and "DROP COLUMN" not in body


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention. The
    release gate in deploy.yml holds a copy of the list."""
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert re.search(
            r"^\s+tests/unit/test_email_signature_settings_r8\.py \\?$", workflow, re.M), name


def _app_dsn(promoted) -> str:  # noqa: F811
    return promoted.app_url.render_as_string(hide_password=False)


def _seed_settings(admin_engine, *, org: str, account_id: str, mode: str = "template",
                   layout: str | None = "classic", reply_mode: str = "full",
                   fields: dict | None = None) -> None:
    with admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO email_signature_settings (account_id, organization_id, mode, "
            "layout, fields, reply_mode) VALUES (CAST(:a AS uuid), CAST(:o AS uuid), "
            ":m, :l, CAST(:f AS jsonb), :r)"),
            {"a": account_id, "o": org, "m": mode, "l": layout,
             "f": json.dumps(_FIELDS if fields is None else fields), "r": reply_mode})


def _seed_stored(admin_engine, *, org: str, account_id: str, signature: str | None) -> None:
    with admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO email_assistant_settings (account_id, signature, organization_id) "
            "VALUES (CAST(:a AS uuid), :s, CAST(:o AS uuid))"),
            {"a": account_id, "s": signature, "o": org})


def _box(promoted) -> tuple[str, str]:  # noqa: F811
    owner = f"owner-{uuid.uuid4().hex[:8]}@sg2.test"
    return owner, _seed_account(promoted.admin_engine, org=promoted.org_b, owner=owner)


def _flag(monkeypatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "email_signature_templates_orgs", value,
                        raising=False)


async def _resolve(promoted, org: str, account_id: str, kind: str) -> str:  # noqa: F811
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(_app_dsn(promoted)):
            async with _tenant_session() as db:
                return await sig.resolve_signature(db, account_id, kind)
    finally:
        release_tenant(token)


@_DB_GATE
class TestTheTableUnderForceRls:

    def test_the_catalog_forces_rls_with_one_policy(self, promoted, app_engine):  # noqa: F811
        with promoted.admin_engine.connect() as c:
            row = c.execute(text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = 'email_signature_settings'::regclass")).one()
            policies = c.execute(text(
                "SELECT policyname FROM pg_policies "
                "WHERE tablename = 'email_signature_settings'")).scalars().all()
        assert tuple(row) == (True, True)
        assert policies == ["email_signature_settings_tenant_isolation"]

    def test_org_b_cannot_read_a_row_of_org_a(self, promoted, app_engine):  # noqa: F811
        _assert_non_priv(app_engine)
        owner = f"owner-{uuid.uuid4().hex[:8]}@sg2.test"
        acc = _seed_account(promoted.admin_engine, org=promoted.org_a, owner=owner)
        _seed_settings(promoted.admin_engine, org=promoted.org_a, account_id=acc)
        query = text("SELECT count(*) FROM email_signature_settings "
                     "WHERE account_id = CAST(:a AS uuid)")
        for org, want in ((promoted.org_b, 0), (promoted.org_a, 1)):
            with app_engine.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
                assert c.execute(query, {"a": acc}).scalar_one() == want, org

    def test_a_row_stamped_with_another_org_is_refused(self, promoted, app_engine):  # noqa: F811
        _, acc = _box(promoted)
        with app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                      {"o": promoted.org_b})
            with pytest.raises(DBAPIError):
                c.execute(text(
                    "INSERT INTO email_signature_settings (account_id, organization_id) "
                    "VALUES (CAST(:a AS uuid), CAST(:o AS uuid))"),
                    {"a": acc, "o": promoted.org_a})

    def test_a_new_row_is_custom_and_full_by_default(self, promoted, app_engine):  # noqa: F811
        _, acc = _box(promoted)
        with app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                      {"o": promoted.org_b})
            c.execute(text("INSERT INTO email_signature_settings (account_id) "
                           "VALUES (CAST(:a AS uuid))"), {"a": acc})
            row = c.execute(text(
                "SELECT organization_id::text, mode, layout, fields, reply_mode "
                "FROM email_signature_settings WHERE account_id = CAST(:a AS uuid)"),
                {"a": acc}).one()
        assert tuple(row) == (promoted.org_b, "custom", None, {}, "full")

    @pytest.mark.parametrize(("column", "value"), [
        ("mode", "'html'"), ("reply_mode", "'half'"), ("layout", "'fancy'"),
        ("fields", "'[]'::jsonb"), ("fields", "'\"x\"'::jsonb"),
    ])
    def test_the_checks_refuse_a_bad_row(self, promoted, app_engine, column, value):  # noqa: F811
        _, acc = _box(promoted)
        with promoted.admin_engine.begin() as c, pytest.raises(DBAPIError):
            c.execute(text(
                f"INSERT INTO email_signature_settings (account_id, organization_id, "
                f"{column}) VALUES (CAST(:a AS uuid), CAST(:o AS uuid), {value})"),
                {"a": acc, "o": promoted.org_b})

    def test_a_template_needs_a_layout(self, promoted, app_engine):  # noqa: F811
        _, acc = _box(promoted)
        with pytest.raises(DBAPIError):
            _seed_settings(promoted.admin_engine, org=promoted.org_b, account_id=acc,
                           layout=None)

    def test_the_row_goes_with_its_mailbox(self, promoted, app_engine):  # noqa: F811
        _, acc = _box(promoted)
        _seed_settings(promoted.admin_engine, org=promoted.org_b, account_id=acc)
        with promoted.admin_engine.begin() as c:
            c.execute(text("DELETE FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                      {"a": acc})
            left = c.execute(text(
                "SELECT count(*) FROM email_signature_settings "
                "WHERE account_id = CAST(:a AS uuid)"), {"a": acc}).scalar_one()
        assert left == 0


@_DB_GATE
class TestTheResolverOnRealSql:

    @pytest.mark.parametrize("flag", ["", "*"])
    async def test_no_row_resolves_to_the_stored_bytes(
        self, promoted, app_engine, monkeypatch, flag,  # noqa: F811
    ):
        _flag(monkeypatch, flag)
        _, acc = _box(promoted)
        _seed_stored(promoted.admin_engine, org=promoted.org_b, account_id=acc,
                     signature=_STORED)
        for kind in sig.SIGNATURE_KINDS:
            assert await _resolve(promoted, promoted.org_b, acc, kind) == _STORED
        _, bare = _box(promoted)
        assert await _resolve(promoted, promoted.org_b, bare, "new") == ""

    async def test_with_the_flag_off_a_template_row_is_not_read(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _flag(monkeypatch, "")
        _, acc = _box(promoted)
        _seed_stored(promoted.admin_engine, org=promoted.org_b, account_id=acc,
                     signature=_STORED)
        _seed_settings(promoted.admin_engine, org=promoted.org_b, account_id=acc,
                       reply_mode="none")
        for kind in sig.SIGNATURE_KINDS:
            assert await _resolve(promoted, promoted.org_b, acc, kind) == _STORED

    async def test_a_template_row_renders_while_the_flag_holds_the_org(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _flag(monkeypatch, promoted.org_b)
        _, acc = _box(promoted)
        _seed_stored(promoted.admin_engine, org=promoted.org_b, account_id=acc,
                     signature=_STORED)
        _seed_settings(promoted.admin_engine, org=promoted.org_b, account_id=acc,
                       reply_mode="short")
        full = signature_render.render("classic", _FIELDS)[0]
        short = signature_render.render("compact", _FIELDS)[0]
        assert await _resolve(promoted, promoted.org_b, acc, "new") == full
        assert await _resolve(promoted, promoted.org_b, acc, "reply") == short
        assert await _resolve(promoted, promoted.org_b, acc, "forward") == short
        # The flag names org B only, so org A reads the stored bytes.
        _flag(monkeypatch, promoted.org_a)
        assert await _resolve(promoted, promoted.org_b, acc, "new") == _STORED

    async def test_a_row_of_another_org_is_never_read(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _flag(monkeypatch, "*")
        owner = f"owner-{uuid.uuid4().hex[:8]}@sg2.test"
        acc = _seed_account(promoted.admin_engine, org=promoted.org_a, owner=owner)
        _seed_stored(promoted.admin_engine, org=promoted.org_a, account_id=acc,
                     signature=_STORED)
        _seed_settings(promoted.admin_engine, org=promoted.org_a, account_id=acc)
        assert await _resolve(promoted, promoted.org_a, acc, "new") == (
            signature_render.render("classic", _FIELDS)[0])
        assert await _resolve(promoted, promoted.org_b, acc, "new") == ""

    @pytest.mark.parametrize("stored", [None, "", _STORED, "<p>Jane <b>Doe</b></p>"])
    async def test_rule_7_the_draft_readers_and_the_settings_answers(
        self, promoted, app_engine, monkeypatch, stored,  # noqa: F811
    ):
        """No row and the flag off: each reader gets the stored bytes, and the
        GET and the PUT answer carry them, as on main."""
        _flag(monkeypatch, "")
        owner, acc = _box(promoted)
        _seed_stored(promoted.admin_engine, org=promoted.org_b, account_id=acc,
                     signature=stored)
        main = stored or ""
        user = UserContext(
            email=owner, role=UserRole.EMPLOYEE, organization_id=promoted.org_b,
            access=EffectiveAccess(role_granted=frozenset({"feature:email"})))
        token = bind_tenant(promoted.org_b)
        try:
            async with tenant_engine_scope(_app_dsn(promoted)):
                async with _tenant_session() as db:
                    drafted = await drafting._account_signature(db, acc)
                    _, about_sig = await assistant_mod._load_assistant_about(db, acc)
                got = await assistant_mod.get_assistant_settings(account_id=acc, user=user)
                put = await assistant_mod.put_assistant_settings(
                    AssistantSettingsModel(account_id=acc, signature=main), user=user)
        finally:
            release_tenant(token)
        assert drafted == about_sig == main
        assert got["signature"] == main
        assert got["signature_text"] == put["signature_text"] == sig.signature_text(main)

    async def test_the_settings_answers_seed_the_template_text(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """With the flag on, the editor keeps the stored free HTML, and the
        composer seeds the text of the layout signature."""
        _flag(monkeypatch, "*")
        owner, acc = _box(promoted)
        _seed_stored(promoted.admin_engine, org=promoted.org_b, account_id=acc,
                     signature=_STORED)
        _seed_settings(promoted.admin_engine, org=promoted.org_b, account_id=acc)
        user = UserContext(
            email=owner, role=UserRole.EMPLOYEE, organization_id=promoted.org_b,
            access=EffectiveAccess(role_granted=frozenset({"feature:email"})))
        token = bind_tenant(promoted.org_b)
        try:
            async with tenant_engine_scope(_app_dsn(promoted)):
                got = await assistant_mod.get_assistant_settings(account_id=acc, user=user)
        finally:
            release_tenant(token)
        assert got["signature"] == _STORED
        assert got["signature_text"] == signature_render.render("classic", _FIELDS)[1]
