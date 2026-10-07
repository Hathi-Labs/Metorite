"""WS-17 EM-T14a — the Insights fact table, the migration and the write path.

Spec: ``project-docs/specs/email_app_master_plan.md`` §13.3, §13.5 items 6 and
9, and §13.9.1. Decisions D-EM-39, D-EM-41, D-EM-42 and D-EM-44.

R7 fences named here:

* ``email-insights-tenant`` (R8): org A writes a fact, and org B reads 0 rows.
  An insert with no tenant bound fails. The table OWNER, bound to org B, also
  reads 0 rows on the catalog that the migration alone leaves, so FORCE is a
  property of the migration and not only of ``generated/04_policies.sql``.
* ``email-insights-dedupe`` (R8): two writes with one dedupe key keep one row.
  The row keeps ``message_id``, ``quote``, ``counterpart_email`` and ``state``
  of the first write.
* ``email-insights-mailbox-check`` (R8): a write that names a message of
  another mailbox, or a file of another message, writes nothing. A mailbox
  that is not opted in writes nothing.
* ``email-insights-flag``: ``insights_enabled()`` is false with the flag off,
  with an empty organization list, with no tenant, and for an organization
  that the list does not name. It is the ONE reader of the two settings.
* ``email-insights-version`` (R8): a write with a newer extractor version
  deletes the older facts of that message and source, and nothing else.
* ``email-insights-cascade`` (R8): a delete of the message deletes its facts.
  A delete of the mailbox deletes its facts.
* ``email-insights-opt-in`` (R8): the column is NOT NULL with a default of
  false, and a mailbox with no settings row reads ``insights_enabled`` false
  through the real GET handler.

**R8.** The real SQL against the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``, as the role ``acb_app_h3rls``
(NOSUPERUSER, NOBYPASSRLS). The admin engine seeds and reads the rows.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_insights_store.py -v -rs
"""
from __future__ import annotations

import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from acb_common.db import TenantUnbound, bind_tenant, release_tenant
from gateway.db import tenant_session
from gateway.routes.email.automation import assistant as assistant_mod
from gateway.routes.email.automation import insights_store as store
from gateway.routes.email.automation.insights_store import Fact
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are fixtures, used by name, so the import is
# load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]
_LADDER = _ROOT / "infra" / "postgres"
_NUMBERED = re.compile(r"^\d+_.*\.sql$")
_MIGRATION_MARK = "CREATE TABLE IF NOT EXISTS email_insights"


def _migration() -> Path:
    """The one numbered migration that creates the table, found by content."""
    hits = [p for p in sorted(_LADDER.iterdir())
            if _NUMBERED.match(p.name)
            and _MIGRATION_MARK in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, f"want one migration that creates the table: {hits}"
    return hits[0]


def _body(path: Path) -> str:
    """The SQL of ``path`` with its comment lines removed."""
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("--"))


def _invoice(*, ref: str | None = "INV-1", amount: Decimal | None = Decimal("100"),
             quote: str = "Invoice INV-1 for INR 100", **over: Any) -> Fact:
    fields: dict[str, Any] = {
        "fact_type": "invoice", "title": f"Invoice {ref}", "quote": quote,
        "confidence": 0.9, "direction": "payable", "counterpart": "Acme",
        "ref": ref, "amount": amount, "currency": "INR",
        "due_on": date(2026, 11, 1),
    }
    fields.update(over)
    return Fact(**fields)


# ── hermetic ─────────────────────────────────────────────────────────────────


class TestTheMigrationText:
    def test_it_forces_rls_with_a_check(self):
        body = " ".join(_body(_migration()).split())
        assert "ALTER TABLE email_insights ENABLE ROW LEVEL SECURITY" in body
        assert "ALTER TABLE email_insights FORCE ROW LEVEL SECURITY" in body
        assert "WITH CHECK (organization_id =" in body
        assert "current_setting(''app.tenant_id'', true)" in body

    def test_the_cascading_file_key_has_an_index(self):
        """Review round 1, P2: a referential action bypasses RLS, so with no
        index each delete of an attachment scans every organization."""
        body = " ".join(_body(_migration()).split())
        assert ("CREATE INDEX IF NOT EXISTS idx_email_insights_attachment ON "
                "email_insights (attachment_id) WHERE attachment_id IS NOT NULL"
                ) in body

    def test_it_is_expand_only(self):
        body = _body(_migration()).upper()
        # R6: no rename, no drop of a table or a column, and no rewrite of a
        # row. The DROP POLICY IF EXISTS of the RLS block is not a drop of data.
        for word in ("DROP TABLE", "DROP COLUMN", "RENAME", "UPDATE EMAIL",
                     "DELETE FROM"):
            assert word not in body, word

    def test_the_new_columns_are_nullable_or_have_a_constant_default(self):
        body = " ".join(_body(_migration()).split())
        assert "ADD COLUMN IF NOT EXISTS insights_at TIMESTAMPTZ;" in body
        assert ("ADD COLUMN IF NOT EXISTS insights_tries SMALLINT NOT NULL "
                "DEFAULT 0") in body
        assert ("ADD COLUMN IF NOT EXISTS insights_enabled BOOLEAN NOT NULL "
                "DEFAULT false") in body


class TestTheFlag:
    """``email-insights-flag``."""

    ORG = "11111111-1111-1111-1111-111111111111"

    def _enabled(self, monkeypatch, *, on: bool, orgs: str, org: str | None) -> bool:
        settings = get_settings()
        monkeypatch.setattr(settings, "email_insights", on, raising=False)
        monkeypatch.setattr(settings, "email_insights_orgs", orgs, raising=False)
        token = bind_tenant(org) if org else None
        try:
            return store.insights_enabled()
        finally:
            release_tenant(token)

    def test_the_defaults_are_off(self):
        fresh = type(get_settings())()
        assert fresh.email_insights is False
        assert fresh.email_insights_orgs == ""

    @pytest.mark.parametrize(("on", "orgs", "org", "expected"), [
        (False, "*", ORG, False),                 # the flag is off
        (True, "", ORG, False),                   # an empty list
        (True, " , ", ORG, False),                # only commas
        (True, "22222222-2222-2222-2222-222222222222", ORG, False),  # not named
        (True, "*", None, False),                 # no tenant, also with *
        (True, f" {ORG} ", ORG, True),            # named
        (True, f"x,{ORG}", ORG, True),            # named in a list
        (True, "*", ORG, True),                   # every organization
    ], ids=["off", "empty", "commas", "other-org", "no-tenant", "named",
            "in-list", "star"])
    def test_the_answer(self, monkeypatch, on, orgs, org, expected):
        assert self._enabled(monkeypatch, on=on, orgs=orgs, org=org) is expected

    def test_insights_enabled_is_the_one_reader(self):
        """No other module under ``apps/`` or ``packages/`` names the two
        settings, so a second reader cannot drift from this one."""
        allowed = {
            _ROOT / "packages/acb_common/acb_common/settings.py",
            _ROOT / "apps/services/gateway/gateway/routes/email/automation/"
                    "insights_store.py",
        }
        # A read of either setting: the attribute, or its name as a string.
        # The table name inside SQL text matches neither.
        reads = re.compile(
            r"email_insights_orgs|[\"']EMAIL_INSIGHTS(_ORGS)?[\"']"
            r"|\.email_insights\b|[\"']email_insights[\"']")
        readers = set()
        for root in (_ROOT / "apps", _ROOT / "packages"):
            for path in root.rglob("*.py"):
                if ".venv" in path.parts or "node_modules" in path.parts:
                    continue
                if reads.search(path.read_text(encoding="utf-8", errors="replace")):
                    readers.add(path)
        assert readers <= allowed, sorted(str(p) for p in readers - allowed)


class TestTheWritePathTrustsNoField:
    """The write path refuses an unknown type, drops a field outside the
    type, and cuts each text to its cap (§13.3, §13.4)."""

    def test_an_unknown_type_is_refused(self):
        assert store._clean(_invoice(fact_type="bonus")) is None

    def test_every_type_names_a_known_domain(self):
        assert set(store.DOMAIN_OF.values()) == set(store.DOMAINS)

    def test_a_field_outside_the_type_is_dropped(self):
        fact = Fact(fact_type="deadline", title="Ship it", quote="by 1 Nov 2026",
                    confidence=0.9, direction="payable", amount=Decimal("5"),
                    currency="INR", due_on=date(2026, 11, 1))
        row = store._clean(fact)
        assert row is not None and row["domain"] == "projects"
        assert (row["direction"], row["amount"], row["currency"]) == (None, None, None)
        assert row["due_on"] == date(2026, 11, 1)

    def test_a_confirmation_has_no_due_date(self):
        row = store._clean(_invoice(fact_type="payment_confirmation"))
        assert row is not None and row["due_on"] is None

    @pytest.mark.parametrize("over", [
        {"confidence": 1.5}, {"confidence": -0.1}, {"confidence": True},
        {"confidence": "0.9"}, {"title": "  "}, {"quote": ""}, {"quote": None},
    ], ids=["conf-high", "conf-low", "conf-bool", "conf-str", "no-title",
            "no-quote", "none-quote"])
    def test_a_fact_with_a_bad_required_field_is_refused(self, over):
        assert store._clean(_invoice(**over)) is None

    @pytest.mark.parametrize(("over", "field"), [
        ({"amount": 100.5}, "amount"),          # a float is a model number
        ({"amount": True}, "amount"),
        ({"amount": Decimal("1e17")}, "amount"),
        ({"amount": Decimal("NaN")}, "amount"),
        ({"currency": "inr"}, "currency"),
        ({"currency": "RUPEES"}, "currency"),
        ({"direction": "owed"}, "direction"),
        ({"due_on": "2026-11-01"}, "due_on"),
    ], ids=["float", "bool", "too-big", "nan", "lower", "long", "direction",
            "date-str"])
    def test_a_bad_optional_field_is_dropped(self, over, field):
        row = store._clean(_invoice(**over))
        assert row is not None and row[field] is None

    def test_each_text_is_cut_and_cleaned(self):
        row = store._clean(_invoice(
            title="T" * 300, counterpart="C" * 300, ref="R" * 100,
            quote="Q​\x07" + "q" * 300))
        assert row is not None
        assert len(row["title"]) == 120 and len(row["counterpart"]) == 120
        assert len(row["ref"]) == 64 and len(row["quote"]) == 200
        assert row["quote"].startswith("Qq")
        assert row["amount"] == Decimal("100.00")

    @pytest.mark.parametrize(("raw", "want"), [
        ("Invoice \ud800", "Invoice"),            # a lone high surrogate (Cs)
        ("\udfffInvoice", "Invoice"),             # a lone low surrogate (Cs)
        ("Invoice", "Invoice"),             # private use (Co)
        ("Inv͸oice", "Invoice"),             # unassigned (Cn)
        ("Inv\x00oice", "Invoice"),               # NUL, which Postgres refuses
        ("\ud800", None),                         # nothing is left
    ], ids=["high", "low", "private", "unassigned", "nul", "empty"])
    def test_clean_text_keeps_only_text_that_encodes(self, raw, want):
        """Review round 1, P2: a lone surrogate raised in the driver or in the
        hash, after the DELETE had run."""
        got = store.clean_text(raw, 200)
        assert got == want
        if got is not None:
            got.encode("utf-8")

    def test_every_text_field_of_a_clean_row_encodes(self):
        row = store._clean(_invoice(
            title="T\ud800", counterpart="C\udc00", ref="R\ud83d", quote="Q\udfff"))
        assert row is not None
        for field in ("title", "counterpart", "ref", "quote"):
            row[field].encode("utf-8")
        store.dedupe_key(row, message_id="m", counterpart_email="a@b.test")


class TestTheDedupeKey:
    def test_the_key_names_type_ref_amount_currency_and_sender_domain(self):
        row = store._clean(_invoice(ref="INV-9", amount=Decimal("1250.5")))
        assert store.dedupe_key(
            row, message_id="m-1", counterpart_email="Billing@Acme.TEST",
        ) == "invoice|inv-9|1250.50|inr|acme.test"

    def test_no_ref_uses_the_full_sender_address(self):
        """Review round 1, P2 (orchestrator decision, 2026-10-07). Two people
        at one free mail domain stay two keys. With a ref, one vendor that
        bills from two addresses stays one key."""
        row = store._clean(_invoice(ref=None, amount=Decimal("5000")))
        ravi = store.dedupe_key(row, message_id="m", counterpart_email="Ravi@Gmail.com")
        priya = store.dedupe_key(row, message_id="m", counterpart_email="priya@gmail.com")
        assert ravi == "invoice||5000.00|inr|ravi@gmail.com"
        assert priya == "invoice||5000.00|inr|priya@gmail.com"
        with_ref = store._clean(_invoice(ref="INV-1"))
        assert store.dedupe_key(
            with_ref, message_id="m", counterpart_email="billing@vendor.test",
        ) == store.dedupe_key(
            with_ref, message_id="n", counterpart_email="ar@vendor.test")

    def test_no_ref_and_no_amount_uses_the_message_and_the_quote(self):
        row = store._clean(_invoice(ref=None, amount=None, quote="Pay  us\nsoon"))
        key = store.dedupe_key(row, message_id="M-1", counterpart_email=None)
        same = store.dedupe_key(
            store._clean(_invoice(ref=None, amount=None, quote="pay us soon")),
            message_id="M-1", counterpart_email=None)
        assert key.startswith("invoice|m-1|") and key == same


class TestTheGuardsBeforeTheDatabase:
    async def test_no_tenant_raises_before_any_sql(self):
        with pytest.raises(TenantUnbound):
            await store.write_facts(None, "a", "m", None, "fin-1", [])

    async def test_every_fact_is_cleaned_before_any_sql(self, monkeypatch):
        """Review round 1, P2: no value may raise after the DELETE. So the
        clean runs first, and a failure there leaves the session untouched."""
        calls: list[str] = []

        class _Db:
            async def execute(self, sql, params=None):
                calls.append(str(sql))
                raise AssertionError("SQL ran before the facts were cleaned")

        def _boom(fact):
            raise RuntimeError("clean failed")

        monkeypatch.setattr(store, "_clean", _boom)
        token = bind_tenant(str(uuid.uuid4()))
        try:
            with pytest.raises(RuntimeError, match="clean failed"):
                await store.write_facts(
                    _Db(), str(uuid.uuid4()), str(uuid.uuid4()), None, "fin-1",
                    [_invoice()])
        finally:
            release_tenant(token)
        assert calls == []

    @pytest.mark.parametrize("version", ["fin", "FIN-1", "fin-x", "fin-1234567", ""])
    async def test_a_bad_version_raises(self, version):
        token = bind_tenant(str(uuid.uuid4()))
        try:
            with pytest.raises(ValueError):
                await store.write_facts(None, "a", "m", None, version, [])
        finally:
            release_tenant(token)


# ── R8 helpers ───────────────────────────────────────────────────────────────


def _assert_non_priv(engine) -> None:
    with engine.connect() as c:
        row = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).one()
    assert (row.rolsuper, row.rolbypassrls) == (False, False)


def _account(admin, *, org: str, owner: str) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, organization_id) "
            "VALUES (:u, 'microsoft', :m, 'x', CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "m": owner, "o": org}).scalar_one())


def _message(admin, *, org: str, account_id: str,
             sender: str = "billing@acme.test") -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "folder, from_address, to_addresses, subject, body_text, "
            "received_at, organization_id) VALUES (CAST(:a AS uuid), :p, "
            "'inbox', CAST(:frm AS jsonb), '[]'::jsonb, 's', 'b', :r, "
            "CAST(:o AS uuid)) RETURNING id"),
            {"a": account_id, "p": f"pm-{uuid.uuid4().hex[:10]}",
             "frm": json.dumps({"name": "N", "email": sender}),
             "r": datetime.now(UTC) - timedelta(minutes=5),
             "o": org}).scalar_one())


def _attachment(admin, *, org: str, message_id: str) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_attachments (message_id, filename, "
            "organization_id) VALUES (CAST(:m AS uuid), 'inv.pdf', "
            "CAST(:o AS uuid)) RETURNING id"),
            {"m": message_id, "o": org}).scalar_one())


def _opt_in(admin, *, org: str, account_id: str, on: bool = True) -> None:
    with admin.begin() as c:
        c.execute(text(
            "INSERT INTO email_assistant_settings (account_id, insights_enabled, "
            "organization_id) VALUES (CAST(:a AS uuid), :on, CAST(:o AS uuid))"),
            {"a": account_id, "on": on, "o": org})


def _facts(admin, *, account_id: str) -> list:
    with admin.connect() as c:
        return list(c.execute(text(
            "SELECT * FROM email_insights WHERE account_id = CAST(:a AS uuid) "
            "ORDER BY created_at, id"), {"a": account_id}).mappings())


def _insights_at(admin, message_id: str):
    with admin.connect() as c:
        return c.execute(text(
            "SELECT insights_at FROM email_messages WHERE id = CAST(:m AS uuid)"),
            {"m": message_id}).scalar_one()


def _raw_insert(engine, *, org: str, account_id: str, message_id: str,
                key: str) -> None:
    """One fact, by SQL, with NO tenant bound on the connection."""
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO email_insights (organization_id, account_id, "
            "message_id, domain, fact_type, title, quote, confidence, "
            "extractor_version, dedupe_key) VALUES (CAST(:o AS uuid), "
            "CAST(:a AS uuid), CAST(:m AS uuid), 'finance', 'invoice', 't', "
            "'q', 0.9, 'fin-1', :k)"),
            {"o": org, "a": account_id, "m": message_id, "k": key})


def _purge(admin, owner_pattern: str) -> None:
    with admin.begin() as c:
        c.execute(text("DELETE FROM email_accounts WHERE user_id LIKE :u"),
                  {"u": owner_pattern})


def _count_as(url, org: str | None, account_id: str) -> int:
    """How many facts of ``account_id`` the role of ``url`` sees, bound to
    ``org`` or unbound when ``org`` is None."""
    eng = create_engine(url, future=True)
    try:
        with eng.connect() as c, c.begin():
            if org is not None:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": org})
            return c.execute(text(
                "SELECT count(*) FROM email_insights "
                "WHERE account_id = CAST(:a AS uuid)"),
                {"a": account_id}).scalar_one()
    finally:
        eng.dispose()


@asynccontextmanager
async def _as_member(p, org: str):
    """Bind ``org`` and point the shared engine at the app role."""
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            yield
    finally:
        release_tenant(token)


async def _write(p, org: str, account_id: str, message_id: str,
                 attachment_id: str | None, version: str,
                 facts: list[Fact]) -> store.WriteResult:
    async with _as_member(p, org), tenant_session() as db:
        return await store.write_facts(
            db, account_id, message_id, attachment_id, version, facts)


def _mailbox(p, org: str, tag: str, *, opted_in: bool = True) -> SimpleNamespace:
    owner = f"member-{uuid.uuid4().hex[:8]}-{tag}@t14a.test"
    aid = _account(p.admin_engine, org=org, owner=owner)
    if opted_in:
        _opt_in(p.admin_engine, org=org, account_id=aid)
    return SimpleNamespace(owner=owner, aid=aid)


# ── R8 ───────────────────────────────────────────────────────────────────────


@_DB_GATE
class TestTheTenantBoundary:
    """``email-insights-tenant``."""

    async def test_org_a_writes_and_org_b_reads_nothing(
        self, promoted, app_engine,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p, tag = promoted, uuid.uuid4().hex[:8]
        box = _mailbox(p, p.org_a, tag)
        mid = _message(p.admin_engine, org=p.org_a, account_id=box.aid)
        try:
            res = await _write(p, p.org_a, box.aid, mid, None, "fin-1", [_invoice()])
            assert (res.written, res.refused) == (1, None)
            (row,) = _facts(p.admin_engine, account_id=box.aid)
            assert str(row["organization_id"]) == p.org_a
            assert row["counterpart_email"] == "billing@acme.test"
            assert row["state"] == "open" and row["domain"] == "finance"
            assert _insights_at(p.admin_engine, mid) is not None
            assert _count_as(p.app_url, p.org_a, box.aid) == 1
            assert _count_as(p.app_url, p.org_b, box.aid) == 0
            assert _count_as(p.app_url, None, box.aid) == 0
        finally:
            _purge(p.admin_engine, f"%-{tag}@t14a.test")

    async def test_org_b_cannot_write_into_a_mailbox_of_org_a(
        self, promoted, app_engine,  # noqa: F811
    ):
        """Bound to org B, the message check sees no row of org A."""
        p, tag = promoted, uuid.uuid4().hex[:8]
        box = _mailbox(p, p.org_a, tag)
        mid = _message(p.admin_engine, org=p.org_a, account_id=box.aid)
        try:
            res = await _write(p, p.org_b, box.aid, mid, None, "fin-1", [_invoice()])
            assert res.refused == "message_not_in_mailbox"
            assert _facts(p.admin_engine, account_id=box.aid) == []
        finally:
            _purge(p.admin_engine, f"%-{tag}@t14a.test")

    def test_an_insert_with_no_tenant_bound_fails(
        self, promoted, app_engine,  # noqa: F811
    ):
        p, tag = promoted, uuid.uuid4().hex[:8]
        box = _mailbox(p, p.org_a, tag)
        mid = _message(p.admin_engine, org=p.org_a, account_id=box.aid)
        try:
            with pytest.raises(DBAPIError, match="row-level security"):
                _raw_insert(app_engine, org=p.org_a, account_id=box.aid,
                            message_id=mid, key="k")
            assert _facts(p.admin_engine, account_id=box.aid) == []
        finally:
            _purge(p.admin_engine, f"%-{tag}@t14a.test")

    def test_the_migration_alone_binds_the_table_owner(
        self, promoted, app_engine,  # noqa: F811
    ):
        """FORCE is what binds the OWNER of a table. The promoted catalog gets
        FORCE from ``generated/04_policies.sql`` too, so this test takes it
        away, runs the migration again, and makes the app role the owner. A
        migration without FORCE then lets the owner, bound to org B, read the
        fact of org A, and lets it insert with no tenant bound."""
        p, tag = promoted, uuid.uuid4().hex[:8]
        box = _mailbox(p, p.org_a, tag)
        mid = _message(p.admin_engine, org=p.org_a, account_id=box.aid)
        body = "\n".join(
            line for line in _migration().read_text(encoding="utf-8").splitlines()
            if line.strip().upper() not in ("BEGIN;", "COMMIT;"))
        role = p.app_url.username
        _raw_insert(p.admin_engine, org=p.org_a, account_id=box.aid,
                    message_id=mid, key="k")
        try:
            with p.admin_engine.begin() as c:
                c.execute(text(
                    "ALTER TABLE email_insights NO FORCE ROW LEVEL SECURITY"))
                with c.connection.dbapi_connection.cursor() as cur:
                    cur.execute(body)
                c.execute(text(f"ALTER TABLE email_insights OWNER TO {role}"))
            assert _count_as(p.app_url, p.org_b, box.aid) == 0
            assert _count_as(p.app_url, p.org_a, box.aid) == 1
            with p.admin_engine.connect() as c:
                forced = c.execute(text(
                    "SELECT relforcerowsecurity FROM pg_class "
                    "WHERE oid = 'email_insights'::regclass")).scalar_one()
            assert forced is True
            with pytest.raises(DBAPIError, match="row-level security"):
                _raw_insert(app_engine, org=p.org_a, account_id=box.aid,
                            message_id=mid, key="k2")
        finally:
            # A move of the owner moves the privileges of the old owner too, so
            # the app role loses its grant when the table moves back.
            with p.admin_engine.begin() as c:
                c.execute(text("ALTER TABLE email_insights OWNER TO CURRENT_USER"))
                c.execute(text(
                    "ALTER TABLE email_insights FORCE ROW LEVEL SECURITY"))
                c.execute(text(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON email_insights "
                    f"TO {role}"))
            _purge(p.admin_engine, f"%-{tag}@t14a.test")


@_DB_GATE
class TestTheDedupe:
    """``email-insights-dedupe``."""

    async def test_two_writes_with_one_key_keep_the_first_row(
        self, promoted, app_engine,  # noqa: F811
    ):
        p, tag = promoted, uuid.uuid4().hex[:8]
        box = _mailbox(p, p.org_b, tag)
        first = _message(p.admin_engine, org=p.org_b, account_id=box.aid,
                         sender="Billing@Acme.test")
        again = _message(p.admin_engine, org=p.org_b, account_id=box.aid,
                         sender="ar@acme.test")
        try:
            await _write(p, p.org_b, box.aid, first, None, "fin-1",
                         [_invoice(quote="Invoice INV-1 for INR 100")])
            with p.admin_engine.begin() as c:
                c.execute(text(
                    "UPDATE email_insights SET state = 'done' "
                    "WHERE account_id = CAST(:a AS uuid)"), {"a": box.aid})
            (before,) = _facts(p.admin_engine, account_id=box.aid)
            # Another message with the same key sends another due date and
            # direction (review round 1, P1). Two facts in one call, too.
            res = await _write(p, p.org_b, box.aid, again, None, "fin-1", [
                _invoice(quote="Re: INV-1 is INR 100", confidence=0.6,
                         title="Invoice INV-1 again", direction="receivable",
                         due_on=date(2027, 1, 1), counterpart="Mallory"),
                _invoice(quote="the same key twice in one call", confidence=0.3,
                         title="Pay now", due_on=date(2027, 2, 2)),
            ])
            assert (res.written, res.kept) == (0, 2)
            (row,) = _facts(p.admin_engine, account_id=box.aid)
            # NO field of the first row changed: the source, the quote, the
            # mark of the member, and each field that the quote holds.
            assert dict(row) == dict(before)
            assert str(row["message_id"]) == first
            assert row["quote"] == "Invoice INV-1 for INR 100"
            assert row["counterpart_email"] == "billing@acme.test"
            assert row["state"] == "done"
            assert row["direction"] == "payable"
            assert row["due_on"] == date(2026, 11, 1)
            assert row["dedupe_key"] == "invoice|inv-1|100.00|inr|acme.test"
            # The job still read the other message.
            assert _insights_at(p.admin_engine, again) is not None
        finally:
            _purge(p.admin_engine, f"%-{tag}@t14a.test")

    async def test_the_same_source_refines_its_own_fact(
        self, promoted, app_engine,  # noqa: F811
    ):
        """A new extraction of the SAME message and file refines the fields.
        The quote, the source and the mark of the member stay."""
        p, tag = promoted, uuid.uuid4().hex[:8]
        box = _mailbox(p, p.org_b, tag)
        mail = _message(p.admin_engine, org=p.org_b, account_id=box.aid)
        try:
            await _write(p, p.org_b, box.aid, mail, None, "fin-1",
                         [_invoice(quote="Invoice INV-1 for INR 100")])
            with p.admin_engine.begin() as c:
                c.execute(text(
                    "UPDATE email_insights SET state = 'done' "
                    "WHERE account_id = CAST(:a AS uuid)"), {"a": box.aid})
            res = await _write(p, p.org_b, box.aid, mail, None, "fin-2", [
                _invoice(quote="INV-1, INR 100, due 1 Dec 2026", confidence=0.6,
                         title="Invoice INV-1 refined", due_on=date(2026, 12, 1))])
            assert (res.written, res.kept) == (1, 0)
            (row,) = _facts(p.admin_engine, account_id=box.aid)
            assert row["title"] == "Invoice INV-1 refined"
            assert row["due_on"] == date(2026, 12, 1)
            assert row["confidence"] == pytest.approx(0.6)
            assert row["extractor_version"] == "fin-2"
            assert row["quote"] == "Invoice INV-1 for INR 100"
            assert (str(row["message_id"]), row["state"]) == (mail, "done")
        finally:
            _purge(p.admin_engine, f"%-{tag}@t14a.test")

    async def test_two_freemail_senders_with_no_ref_stay_two_cards(
        self, promoted, app_engine,  # noqa: F811
    ):
        """Review round 1, P2: with no ``ref`` the key holds the FULL sender."""
        p, tag = promoted, uuid.uuid4().hex[:8]
        box = _mailbox(p, p.org_b, tag)
        ravi = _message(p.admin_engine, org=p.org_b, account_id=box.aid,
                        sender="ravi@gmail.com")
        priya = _message(p.admin_engine, org=p.org_b, account_id=box.aid,
                         sender="priya@gmail.com")
        try:
            for mid in (ravi, priya):
                res = await _write(p, p.org_b, box.aid, mid, None, "fin-1", [
                    _invoice(ref=None, amount=Decimal("5000"),
                             quote="Invoice for INR 5000")])
                assert res.written == 1
            keys = sorted(r["dedupe_key"]
                          for r in _facts(p.admin_engine, account_id=box.aid))
            assert keys == ["invoice||5000.00|inr|priya@gmail.com",
                            "invoice||5000.00|inr|ravi@gmail.com"]
        finally:
            _purge(p.admin_engine, f"%-{tag}@t14a.test")

    async def test_one_vendor_with_two_addresses_and_one_ref_is_one_card(
        self, promoted, app_engine,  # noqa: F811
    ):
        p, tag = promoted, uuid.uuid4().hex[:8]
        box = _mailbox(p, p.org_b, tag)
        billing = _message(p.admin_engine, org=p.org_b, account_id=box.aid,
                           sender="billing@vendor.test")
        ar = _message(p.admin_engine, org=p.org_b, account_id=box.aid,
                      sender="ar@vendor.test")
        try:
            for mid in (billing, ar):
                await _write(p, p.org_b, box.aid, mid, None, "fin-1", [
                    _invoice(ref="INV-77", quote="Invoice INV-77 for INR 100")])
            (row,) = _facts(p.admin_engine, account_id=box.aid)
            assert row["dedupe_key"] == "invoice|inv-77|100.00|inr|vendor.test"
            assert str(row["message_id"]) == billing
        finally:
            _purge(p.admin_engine, f"%-{tag}@t14a.test")

    async def test_a_lone_surrogate_from_a_model_writes_clean_text(
        self, promoted, app_engine,  # noqa: F811
    ):
        """Review round 1, P2: a lone surrogate never reaches the driver."""
        p, tag = promoted, uuid.uuid4().hex[:8]
        box = _mailbox(p, p.org_b, tag)
        mail = _message(p.admin_engine, org=p.org_b, account_id=box.aid)
        try:
            res = await _write(p, p.org_b, box.aid, mail, None, "fin-1", [
                _invoice(title="Invoice \ud800", counterpart="Acme\udfff",
                         ref="INV-\ud83d1", quote="Invoice \ud800INV-1")])
            assert res.written == 1
            (row,) = _facts(p.admin_engine, account_id=box.aid)
            assert (row["title"], row["counterpart"], row["ref"], row["quote"]) \
                == ("Invoice", "Acme", "INV-1", "Invoice INV-1")
        finally:
            _purge(p.admin_engine, f"%-{tag}@t14a.test")


@_DB_GATE
class TestTheMailboxCheck:
    """``email-insights-mailbox-check``."""

    async def test_a_message_of_another_mailbox_writes_nothing(
        self, promoted, app_engine,  # noqa: F811
    ):
        p, tag = promoted, uuid.uuid4().hex[:8]
        mine = _mailbox(p, p.org_a, tag)
        theirs = _mailbox(p, p.org_a, tag)
        their_mail = _message(p.admin_engine, org=p.org_a, account_id=theirs.aid)
        try:
            res = await _write(p, p.org_a, mine.aid, their_mail, None, "fin-1",
                               [_invoice()])
            assert res == store.WriteResult(refused="message_not_in_mailbox")
            assert _facts(p.admin_engine, account_id=mine.aid) == []
            assert _facts(p.admin_engine, account_id=theirs.aid) == []
            assert _insights_at(p.admin_engine, their_mail) is None
        finally:
            _purge(p.admin_engine, f"%-{tag}@t14a.test")

    async def test_a_file_of_another_message_writes_nothing(
        self, promoted, app_engine,  # noqa: F811
    ):
        p, tag = promoted, uuid.uuid4().hex[:8]
        box = _mailbox(p, p.org_a, tag)
        mail = _message(p.admin_engine, org=p.org_a, account_id=box.aid)
        other = _message(p.admin_engine, org=p.org_a, account_id=box.aid)
        file_of_other = _attachment(p.admin_engine, org=p.org_a, message_id=other)
        try:
            res = await _write(p, p.org_a, box.aid, mail, file_of_other, "fin-1",
                               [_invoice()])
            assert res == store.WriteResult(refused="file_not_in_message")
            assert _facts(p.admin_engine, account_id=box.aid) == []
            assert _insights_at(p.admin_engine, mail) is None
            # Its own file writes.
            own = _attachment(p.admin_engine, org=p.org_a, message_id=mail)
            res = await _write(p, p.org_a, box.aid, mail, own, "fin-1", [_invoice()])
            assert res.written == 1
            (row,) = _facts(p.admin_engine, account_id=box.aid)
            assert str(row["attachment_id"]) == own
        finally:
            _purge(p.admin_engine, f"%-{tag}@t14a.test")

    async def test_a_mailbox_that_is_not_opted_in_writes_nothing(
        self, promoted, app_engine,  # noqa: F811
    ):
        p, tag = promoted, uuid.uuid4().hex[:8]
        no_row = _mailbox(p, p.org_a, tag, opted_in=False)
        off = _mailbox(p, p.org_a, tag, opted_in=False)
        _opt_in(p.admin_engine, org=p.org_a, account_id=off.aid, on=False)
        try:
            for box in (no_row, off):
                mail = _message(p.admin_engine, org=p.org_a, account_id=box.aid)
                res = await _write(p, p.org_a, box.aid, mail, None, "fin-1",
                                   [_invoice()])
                assert res.refused == "not_opted_in"
                assert _facts(p.admin_engine, account_id=box.aid) == []
        finally:
            _purge(p.admin_engine, f"%-{tag}@t14a.test")


@_DB_GATE
class TestTheVersion:
    """``email-insights-version``."""

    async def test_a_newer_version_deletes_the_older_facts_of_its_source(
        self, promoted, app_engine,  # noqa: F811
    ):
        p, tag = promoted, uuid.uuid4().hex[:8]
        box = _mailbox(p, p.org_a, tag)
        mail = _message(p.admin_engine, org=p.org_a, account_id=box.aid)
        pdf = _attachment(p.admin_engine, org=p.org_a, message_id=mail)
        try:
            await _write(p, p.org_a, box.aid, mail, None, "fin-1", [
                _invoice(ref="INV-1"), _invoice(ref="INV-2")])
            await _write(p, p.org_a, box.aid, mail, pdf, "fin-1", [
                _invoice(ref="INV-3")])
            await _write(p, p.org_a, box.aid, mail, None, "prj-1", [
                Fact(fact_type="deadline", title="Ship", quote="by 1 Nov 2026",
                     confidence=0.9, ref="PO-7")])
            with p.admin_engine.begin() as c:
                c.execute(text(
                    "UPDATE email_insights SET state = 'dismissed' "
                    "WHERE account_id = CAST(:a AS uuid) AND ref = 'INV-1'"),
                    {"a": box.aid})

            res = await _write(p, p.org_a, box.aid, mail, None, "fin-2", [
                _invoice(ref="INV-1")])
            assert (res.written, res.deleted) == (1, 1)
            rows = {r["ref"]: r for r in _facts(p.admin_engine, account_id=box.aid)}
            # INV-2 was an older fact of the body. INV-1 came again, so it
            # stays with the mark of the member and the new version.
            assert set(rows) == {"INV-1", "INV-3", "PO-7"}
            assert rows["INV-1"]["state"] == "dismissed"
            assert rows["INV-1"]["extractor_version"] == "fin-2"
            # The file is another source, and prj is another extractor.
            assert rows["INV-3"]["extractor_version"] == "fin-1"
            assert rows["PO-7"]["extractor_version"] == "prj-1"

            # An OLDER version deletes nothing of a newer one.
            res = await _write(p, p.org_a, box.aid, mail, None, "fin-1", [])
            assert res.deleted == 0
            assert len(_facts(p.admin_engine, account_id=box.aid)) == 3
        finally:
            _purge(p.admin_engine, f"%-{tag}@t14a.test")


@_DB_GATE
class TestTheCascade:
    """``email-insights-cascade``."""

    async def test_a_delete_of_the_message_deletes_its_facts(
        self, promoted, app_engine,  # noqa: F811
    ):
        p, tag = promoted, uuid.uuid4().hex[:8]
        box = _mailbox(p, p.org_a, tag)
        gone = _message(p.admin_engine, org=p.org_a, account_id=box.aid)
        kept = _message(p.admin_engine, org=p.org_a, account_id=box.aid)
        try:
            await _write(p, p.org_a, box.aid, gone, None, "fin-1",
                         [_invoice(ref="INV-1")])
            await _write(p, p.org_a, box.aid, kept, None, "fin-1",
                         [_invoice(ref="INV-2")])
            async with _as_member(p, p.org_a), tenant_session() as db:
                await db.execute(text(
                    "DELETE FROM email_messages WHERE id = CAST(:m AS uuid)"),
                    {"m": gone})
            assert [r["ref"] for r in _facts(p.admin_engine, account_id=box.aid)] \
                == ["INV-2"]
        finally:
            _purge(p.admin_engine, f"%-{tag}@t14a.test")

    async def test_a_delete_of_the_mailbox_deletes_its_facts(
        self, promoted, app_engine,  # noqa: F811
    ):
        p, tag = promoted, uuid.uuid4().hex[:8]
        box = _mailbox(p, p.org_a, tag)
        mail = _message(p.admin_engine, org=p.org_a, account_id=box.aid)
        try:
            await _write(p, p.org_a, box.aid, mail, None, "fin-1", [_invoice()])
            assert len(_facts(p.admin_engine, account_id=box.aid)) == 1
            async with _as_member(p, p.org_a), tenant_session() as db:
                await db.execute(text(
                    "DELETE FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                    {"a": box.aid})
            assert _facts(p.admin_engine, account_id=box.aid) == []
        finally:
            _purge(p.admin_engine, f"%-{tag}@t14a.test")

    async def test_a_delete_of_a_file_deletes_its_facts_through_an_index(
        self, promoted, app_engine,  # noqa: F811
    ):
        """Review round 1, P2: the cascade on ``attachment_id`` has an index."""
        p, tag = promoted, uuid.uuid4().hex[:8]
        box = _mailbox(p, p.org_a, tag)
        mail = _message(p.admin_engine, org=p.org_a, account_id=box.aid)
        pdf = _attachment(p.admin_engine, org=p.org_a, message_id=mail)
        try:
            await _write(p, p.org_a, box.aid, mail, pdf, "fin-1",
                         [_invoice(ref="INV-1")])
            await _write(p, p.org_a, box.aid, mail, None, "fin-1",
                         [_invoice(ref="INV-2")])
            async with _as_member(p, p.org_a), tenant_session() as db:
                await db.execute(text(
                    "DELETE FROM email_attachments WHERE id = CAST(:f AS uuid)"),
                    {"f": pdf})
            assert [r["ref"] for r in _facts(p.admin_engine, account_id=box.aid)] \
                == ["INV-2"]
            with p.admin_engine.connect() as c:
                index = c.execute(text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname = 'idx_email_insights_attachment'")).scalar_one()
            assert "(attachment_id) WHERE (attachment_id IS NOT NULL)" in index
        finally:
            _purge(p.admin_engine, f"%-{tag}@t14a.test")


@_DB_GATE
class TestTheOptIn:
    """``email-insights-opt-in``."""

    def test_the_columns(self, promoted):  # noqa: F811
        with promoted.admin_engine.connect() as c:
            cols = {r.key: r for r in c.execute(text(
                "SELECT table_name || '.' || column_name AS key, is_nullable, "
                "column_default, data_type FROM information_schema.columns "
                "WHERE (table_name, column_name) IN "
                "(('email_assistant_settings', 'insights_enabled'), "
                " ('email_messages', 'insights_at'), "
                " ('email_messages', 'insights_tries'))"))}
            index = c.execute(text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'idx_email_messages_insights_pending'")).scalar_one()
        opt_in = cols["email_assistant_settings.insights_enabled"]
        assert (opt_in.is_nullable, opt_in.column_default) == ("NO", "false")
        assert cols["email_messages.insights_at"].is_nullable == "YES"
        tries = cols["email_messages.insights_tries"]
        assert (tries.is_nullable, tries.column_default) == ("NO", "0")
        assert "WHERE (insights_at IS NULL)" in index

    async def test_a_missing_settings_row_reads_false(
        self, promoted, app_engine,  # noqa: F811
    ):
        p, tag = promoted, uuid.uuid4().hex[:8]
        box = _mailbox(p, p.org_b, tag, opted_in=False)
        user = SimpleNamespace(email=box.owner)
        try:
            async with _as_member(p, p.org_b):
                body = await assistant_mod.get_assistant_settings(
                    account_id=box.aid, user=user)
                assert body["insights_enabled"] is False
                req = assistant_mod.AssistantSettingsModel.model_validate(
                    {**body, "insights_enabled": True})
                put = await assistant_mod.put_assistant_settings(req=req, user=user)
                assert put["insights_enabled"] is True
                again = await assistant_mod.get_assistant_settings(
                    account_id=box.aid, user=user)
                assert again["insights_enabled"] is True
        finally:
            _purge(p.admin_engine, f"%-{tag}@t14a.test")
