"""R8 — the weekly CRM digest, against a real Postgres (WS-53 CRM-Q28).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q28 item 17.

The migration adds ``crm_digests``. ``gateway/crm_sync/digest.py`` reads the
numbers of the week through ``aggregate.compute``, sends them to a SCRIPTED
model (``acb_llm.context.acompletion_with_fallback`` is replaced, so no real
model call goes out), checks each number of the text, and writes one row for
each org and week. ``GET /crm/digests`` and the My Day read serve it. Every
step runs as the NON-privileged role of the phase-4 rehearsal, so RLS binds
each read and each write. A fake agrees with any SQL, so only this suite
proves the table, one digest each week, the Monday rule in the CRM zone, the
cap of 3 tries, the route, the tenant fence and the numbers themselves.

The start over is in ``test_crm_reset_r8.py``.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import contextlib
import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import _exec_file, tenant_engine_scope
from tests.unit.test_crm_next_activity_r8 import _call, _native
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"
DOMAIN = "crm-q28-r8.test"
OWNER = f"owner@{DOMAIN}"
OWNER_B = f"owner-b@{DOMAIN}"

#: A Monday, 09:00 UTC. The counted week is 5 to 11 October 2026.
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
IN_WEEK = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)
BEFORE_WEEK = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)

#: Child tables first, so each DELETE meets no foreign key.
_TABLES = (
    "crm_digests", "crm_note_labels", "crm_activities", "crm_deal_contacts",
    "crm_deals", "crm_leads", "crm_contacts", "crm_companies",
    "crm_source_profiles", "crm_pipeline_stages", "crm_pipelines", "crm_settings",
    "integration_connections", "crm_deal_statuses", "crm_lead_statuses",
    "crm_lost_reasons",
)


class ScriptedModel:
    """The model, scripted. It records each call's keyword arguments. Its
    reply is a function of the prompt, so a test can write a text that
    quotes a real number of the input, or one that makes a number up."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.open_at_call: list[int] = []
        self.open = lambda: 0
        self.reply: Any = quote_first_number

    async def __call__(self, **kw: Any) -> tuple[Any, str]:
        self.calls.append(kw)
        self.open_at_call.append(self.open())
        content = self.reply(kw["messages"])
        return (SimpleNamespace(id=f"req-{len(self.calls)}", choices=[SimpleNamespace(
            message=SimpleNamespace(content=content))]), "scripted")

    def prompts(self) -> list[str]:
        return [json.dumps(c["messages"]) for c in self.calls]


def _input(messages: list[dict[str, str]]) -> dict[str, Any]:
    return json.loads(messages[1]["content"].split("\n", 1)[1])


def quote_first_number(messages: list[dict[str, str]]) -> str:
    """A text that uses one number of the input, as the input writes it."""
    for part in _input(messages)["parts"]:
        for row in part["rows"]:
            if row["value"][:1].isdigit():
                return f"{part['title']}: {row['value']} {part['unit']}."
        if part.get("total", "")[:1].isdigit():
            return f"{part['title']}: {part['total']} {part['unit']}."
    return "The week was quiet."


def make_up_a_number(_messages: list[dict[str, str]]) -> str:
    return "The pipeline grew by 98765 deals this week."


@pytest.fixture
def dg8(promoted, monkeypatch):
    """The promoted catalog, the CRM switch on, both orgs listed, a scripted
    model, cleaned after."""
    from acb_common import get_settings
    from acb_llm import context
    from gateway.crm_sync import digest

    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    monkeypatch.setattr(get_settings(), "crm_digest_orgs",
                        f"{promoted.org_a},{promoted.org_b}", raising=False)
    # The member check resolves through the RLS-exempt identity tables, as
    # production does with the cutover on (my_day_loop.departed).
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    model = ScriptedModel()
    monkeypatch.setattr(context, "acompletion_with_fallback", model)
    promoted.model = model

    opened = {"n": 0}
    real = digest.tenant_session

    @contextlib.asynccontextmanager
    async def _counting(*args, **kwargs):
        async with real(*args, **kwargs) as db:
            opened["n"] += 1
            try:
                yield db
            finally:
                opened["n"] -= 1

    monkeypatch.setattr(digest, "tenant_session", _counting)
    model.open = lambda: opened["n"]
    _member_of(promoted, OWNER, promoted.org_a)
    _member_of(promoted, OWNER_B, promoted.org_b)
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})
        c.execute(text("DELETE FROM org_membership m USING user_identity ui "
                       f"WHERE m.user_id = ui.id AND ui.email LIKE '%@{DOMAIN}'"))
        c.execute(text(f"DELETE FROM user_identity WHERE email LIKE '%@{DOMAIN}'"))
        c.execute(text(f"DELETE FROM app_user WHERE email LIKE '%@{DOMAIN}'"))


# ── Seeds, as the superuser ──────────────────────────────────────────────────


def _q(p: Any, sql: str, **params: Any) -> list[Any]:
    with p.admin_engine.connect() as c:
        return list(c.execute(text(sql), params).all())


def _member_of(p: Any, email: str, org: str, *, status: str = "active") -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', :s, CAST(:o AS uuid)) "
            "ON CONFLICT DO NOTHING"), {"e": email, "s": status, "o": org})
        c.execute(text("UPDATE app_user SET status = :s WHERE email = :e"),
                  {"e": email, "s": status})
        uid = c.execute(text(
            "INSERT INTO user_identity (email, display_name) VALUES (:e, :e) "
            "ON CONFLICT (lower(email)) DO UPDATE SET updated_at = now() "
            "RETURNING id::text"), {"e": email}).scalar()
        c.execute(text(
            "INSERT INTO org_membership (organization_id, user_id, status, joined_at) "
            "VALUES (CAST(:o AS uuid), CAST(:u AS uuid), :s, now()) "
            "ON CONFLICT (organization_id, user_id) DO UPDATE SET status = EXCLUDED.status"),
            {"o": org, "u": uid, "s": status})


def _connect(p: Any, org: str, member: str | None = None) -> str:
    if member is None:
        member = OWNER if org == p.org_a else OWNER_B
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, "
            "owning_app, connected_by) VALUES (CAST(:o AS uuid), 'zoho', 'crm', :m) "
            "RETURNING id"), {"o": org, "m": member}).scalar())


async def _native_org(p: Any, org: str) -> dict[str, tuple[str, str]]:
    """Native mode, the seeded stages and a connection. Stage name → (id, type)."""
    await _native(p, org)
    _connect(p, org)
    rows = _q(p, "SELECT name, id::text, type FROM crm_deal_statuses "
                 "WHERE organization_id = CAST(:o AS uuid)", o=org)
    return {r[0]: (r[1], r[2]) for r in rows}


def _stage(stages: dict[str, tuple[str, str]], kind: str) -> str:
    return next(i for i, t in stages.values() if t == kind)


def _deal(p: Any, org: str, status: str, *, name: str = "Deal", amount: float | None = 100,
          close: date | None = None, at: datetime = BEFORE_WEEK) -> str:
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, status_id, amount, "
            "expected_close_date, created_at, updated_at) VALUES (CAST(:o AS uuid), "
            ":n, CAST(:s AS uuid), :a, :c, :at, :at) RETURNING id"),
            {"o": org, "n": name, "s": status, "a": amount, "c": close, "at": at}).scalar())


def _lead(p: Any, org: str, *, at: datetime, name: str = "Lead") -> None:
    with p.admin_engine.begin() as c:
        status = c.execute(text(
            "SELECT id FROM crm_lead_statuses WHERE organization_id = CAST(:o AS uuid) "
            "ORDER BY position LIMIT 1"), {"o": org}).scalar()
        c.execute(text(
            "INSERT INTO crm_leads (organization_id, lead_name, status_id, created_at, "
            "updated_at) VALUES (CAST(:o AS uuid), :n, :s, :at, :at)"),
            {"o": org, "n": name, "s": status, "at": at})


def _row(p: Any, org: str) -> Any:
    rows = _q(p, "SELECT week_start, body, tries, request_id, numbers, stored_at "
                 "FROM crm_digests WHERE organization_id = CAST(:o AS uuid) "
                 "ORDER BY week_start", o=org)
    return rows


async def _step(p: Any, org: str, now: datetime = NOW) -> Any:
    from gateway.crm_sync import digest

    async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
        return await digest.digest_after_run(org, now=now)


def _migration() -> Path:
    found = [f for f in _MIGRATIONS.glob("*.sql")
             if "CREATE TABLE IF NOT EXISTS crm_digests" in f.read_text(encoding="utf-8")]
    assert len(found) == 1, [f.name for f in found]
    return found[0]


def _part(numbers: dict[str, Any], key: str) -> dict[str, Any] | None:
    return next((p for p in numbers["parts"] if p["key"] == key), None)


# ── Item 1 and 2: the table ──────────────────────────────────────────────────


def test_the_columns_the_keys_and_the_cap(dg8) -> None:
    cols = dict(_q(dg8, "SELECT column_name, is_nullable FROM information_schema.columns "
                        "WHERE table_name = 'crm_digests'"))
    assert set(cols) == {"organization_id", "id", "week_start", "numbers", "body",
                         "request_id", "tries", "created_at", "stored_at"}
    assert cols["organization_id"] == cols["week_start"] == cols["numbers"] == "NO"
    assert cols["body"] == cols["request_id"] == cols["stored_at"] == "YES"
    unique = [r[0] for r in _q(dg8, "SELECT indexdef FROM pg_indexes WHERE "
                                    "tablename = 'crm_digests' AND indexdef LIKE '%UNIQUE%'")]
    assert any("(organization_id, week_start)" in d for d in unique)
    fks = dict(_q(dg8, "SELECT confrelid::regclass::text, confdeltype FROM pg_constraint "
                       "WHERE conrelid = to_regclass('public.crm_digests') AND contype = 'f'"))
    assert fks == {"organization": "c"}
    with pytest.raises(DBAPIError), dg8.admin_engine.begin() as c:
        c.execute(text("INSERT INTO crm_digests (organization_id, week_start, tries) "
                       "VALUES (CAST(:o AS uuid), DATE '2026-10-05', 4)"), {"o": dg8.org_a})


def test_one_row_for_each_org_and_week(dg8) -> None:
    with dg8.admin_engine.begin() as c:
        c.execute(text("INSERT INTO crm_digests (organization_id, week_start) VALUES "
                       "(CAST(:o AS uuid), DATE '2026-10-05')"), {"o": dg8.org_a})
        c.execute(text("INSERT INTO crm_digests (organization_id, week_start) VALUES "
                       "(CAST(:o AS uuid), DATE '2026-10-05')"), {"o": dg8.org_b})
    with pytest.raises(DBAPIError), dg8.admin_engine.begin() as c:
        c.execute(text("INSERT INTO crm_digests (organization_id, week_start) VALUES "
                       "(CAST(:o AS uuid), DATE '2026-10-05')"), {"o": dg8.org_a})


def test_force_rls_and_one_policy(dg8) -> None:
    assert _q(dg8, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                   "WHERE oid = to_regclass('public.crm_digests')") == [(True, True)]
    assert _q(dg8, "SELECT qual IS NOT NULL, with_check IS NOT NULL FROM pg_policies "
                   "WHERE tablename = 'crm_digests'") == [(True, True)]


def test_the_migration_alone_sets_force_rls_and_a_replay_changes_nothing(dg8) -> None:
    with dg8.admin_engine.begin() as c:
        c.execute(text("DROP POLICY IF EXISTS crm_digests_tenant_isolation ON crm_digests"))
        c.execute(text("ALTER TABLE crm_digests NO FORCE ROW LEVEL SECURITY"))
        c.execute(text("ALTER TABLE crm_digests DISABLE ROW LEVEL SECURITY"))
    with dg8.admin_engine.begin() as c:
        _exec_file(c, str(_migration()))

    def shape() -> tuple:
        return (
            _q(dg8, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = to_regclass('public.crm_digests')"),
            _q(dg8, "SELECT conname FROM pg_constraint WHERE "
                    "conrelid = to_regclass('public.crm_digests') ORDER BY conname"),
            _q(dg8, "SELECT indexdef FROM pg_indexes WHERE tablename = 'crm_digests' "
                    "ORDER BY indexdef"),
            _q(dg8, "SELECT policyname FROM pg_policies WHERE tablename = 'crm_digests'"),
        )

    before = shape()
    assert before[0] == [(True, True)]
    assert before[3] == [("crm_digests_tenant_isolation",)]
    with dg8.admin_engine.begin() as c:
        _exec_file(c, str(_migration()))
    assert shape() == before


def test_rls_keeps_each_org_to_its_own_rows(dg8, app_engine) -> None:
    a, b = dg8.org_a, dg8.org_b
    with dg8.admin_engine.begin() as c:
        for org in (a, b):
            c.execute(text("INSERT INTO crm_digests (organization_id, week_start, body) "
                           "VALUES (CAST(:o AS uuid), DATE '2026-10-05', :b)"),
                      {"o": org, "b": f"text of {org}"})
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": a})
        assert c.execute(text("SELECT body FROM crm_digests")).scalars().all() == [
            f"text of {a}"]
    with pytest.raises(DBAPIError), app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": a})
        c.execute(text("UPDATE crm_digests SET organization_id = CAST(:b AS uuid)"),
                  {"b": b})


# ── Item 5 and 9: the numbers are the route's numbers ────────────────────────


async def test_the_digest_holds_the_numbers_of_the_aggregate_route(dg8) -> None:
    a = dg8.org_a
    stages = await _native_org(dg8, a)
    open_id, won_id = _stage(stages, "open"), _stage(stages, "won")
    for i in range(3):
        _deal(dg8, a, open_id, amount=1000 + i, close=date(2026, 11, 3))
    _deal(dg8, a, open_id, amount=2500.5, close=date(2026, 12, 20))
    _deal(dg8, a, won_id, amount=40, at=IN_WEEK)
    _lead(dg8, a, at=IN_WEEK)
    _lead(dg8, a, at=IN_WEEK)
    _lead(dg8, a, at=BEFORE_WEEK)

    got = await _step(dg8, a)

    assert got.reason == "stored", got
    [row] = _row(dg8, a)
    numbers = row.numbers
    assert row.week_start == date(2026, 10, 5) and row.tries == 1 and row.body
    assert numbers["week_start"] == "2026-10-05" and numbers["zone"] == "UTC"
    # The route gives the same numbers for the same call.
    route = (await _call(dg8, a, "GET",
                         "/crm/aggregate?entity=deals&measure=count&group_by=status")).json()
    by_label = {g["label"]: g["value"] for g in route["groups"]}
    stage = _part(numbers, "open_deals_by_stage")
    assert stage["rows"] and all(
        float(r["value"]) == by_label[r["label"]] for r in stage["rows"])
    assert {"label": next(n for n, (i, _t) in stages.items() if i == open_id),
            "value": "4"} in stage["rows"]
    months = _part(numbers, "amount_by_close_month")
    assert months["rows"] == [{"label": "November 2026", "value": "3003"},
                              {"label": "December 2026", "value": "2500.5"}]
    assert months["total"] == "5503.5"
    leads = (await _call(dg8, a, "GET", "/crm/aggregate?entity=leads&measure=count&"
                         "group_by=created_month&created_from=2026-10-05&"
                         "created_to=2026-10-11&include_converted=true")).json()
    assert _part(numbers, "new_leads")["total"] == "2" and leads["total"] == 2
    won = _part(numbers, "won_lost")
    assert won["rows"] == [{"label": next(n for n, (i, _t) in stages.items() if i == won_id),
                            "value": "1"}]
    # The text quotes a number of the input, and no session was open at the call.
    assert row.body.endswith("deals.") or row.body.endswith("INR.")
    assert dg8.model.open_at_call == [0]


async def test_the_numbers_of_one_org_never_hold_the_other(dg8) -> None:
    a, b = dg8.org_a, dg8.org_b
    stages_a = await _native_org(dg8, a)
    await _native_org(dg8, b)
    for _ in range(5):
        _deal(dg8, a, _stage(stages_a, "open"), amount=777)
    await _step(dg8, b)
    [row] = _row(dg8, b)
    assert "777" not in json.dumps(row.numbers)
    assert all(r["value"] == "0" for r in _part(row.numbers, "open_deals_by_stage")["rows"]
               ) or _part(row.numbers, "open_deals_by_stage")["rows"] == []


async def test_an_empty_amount_reads_as_its_route_does(dg8) -> None:
    """``sum_amount`` is a ``COALESCE`` of the deals: the route answers 0 for
    deals with no amount, and the digest writes what the route gives."""
    a = dg8.org_a
    stages = await _native_org(dg8, a)
    _deal(dg8, a, _stage(stages, "open"), amount=None, close=date(2026, 10, 20))
    await _step(dg8, a)
    [row] = _row(dg8, a)
    assert _part(row.numbers, "amount_by_close_month")["rows"] == [
        {"label": "October 2026", "value": "0"}]


# ── Item 8: the prompt holds no name, no email and no note text ─────────────


async def test_the_prompt_holds_no_record_name_no_email_and_no_note(dg8) -> None:
    a = dg8.org_a
    stages = await _native_org(dg8, a)
    _deal(dg8, a, _stage(stages, "open"), name="SECRET-DEAL-NAME", close=date(2026, 10, 30))
    with dg8.admin_engine.begin() as c:
        c.execute(text("INSERT INTO crm_contacts (organization_id, first_name, email, "
                       "created_at) VALUES (CAST(:o AS uuid), 'SECRET-CONTACT', "
                       "'secret.person@client.test', :at)"), {"o": a, "at": IN_WEEK})
        company = c.execute(text("INSERT INTO crm_companies (organization_id, name) "
                                 "VALUES (CAST(:o AS uuid), 'SECRET-COMPANY') RETURNING id"),
                            {"o": a}).scalar()
        c.execute(text("INSERT INTO crm_activities (organization_id, type, subject, body, "
                       "created_by, occurred_at, company_id) VALUES (CAST(:o AS uuid), "
                       "'note', 'S', 'SECRET-NOTE-BODY', 'x@y.test', :at, :c)"),
                  {"o": a, "at": IN_WEEK, "c": company})
    await _step(dg8, a)
    [prompt] = dg8.model.prompts()
    for secret in ("SECRET-DEAL-NAME", "SECRET-CONTACT", "secret.person@client.test",
                   "SECRET-NOTE-BODY", "SECRET-COMPANY", OWNER):
        assert secret not in prompt
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", prompt)
    assert _part(_row(dg8, a)[0].numbers, "new_contacts")["total"] == "1"


# ── Item 4: one digest each week, and the Monday rule in the CRM zone ───────


async def test_one_digest_each_week(dg8) -> None:
    a = dg8.org_a
    await _native_org(dg8, a)
    assert (await _step(dg8, a)).reason == "stored"
    again = await _step(dg8, a, NOW + timedelta(hours=5))
    assert again.reason == "done_already" and len(dg8.model.calls) == 1
    later = await _step(dg8, a, NOW + timedelta(days=7))
    assert later.reason == "stored" and len(dg8.model.calls) == 2
    assert [r.week_start for r in _row(dg8, a)] == [date(2026, 10, 5), date(2026, 10, 12)]


def _mirror_in(p: Any, org: str, zone: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, mode, access) "
            "VALUES (CAST(:o AS uuid), 'mirror', 'everyone') "
            "ON CONFLICT (organization_id) DO UPDATE SET mode = 'mirror'"), {"o": org})
    conn = _connect(p, org)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_source_profiles (organization_id, connection_id, version, "
            "profile, profile_hash) VALUES (CAST(:o AS uuid), :c, 1, CAST(:p AS jsonb), :h)"),
            {"o": org, "c": conn, "h": uuid.uuid4().hex,
             "p": json.dumps({"modules": [], "org": {"time_zone": zone}})})


async def test_the_monday_rule_reads_the_crm_zone(dg8) -> None:
    a, b = dg8.org_a, dg8.org_b
    _mirror_in(dg8, a, "Asia/Kolkata")
    await _native_org(dg8, b)
    # 00:00 UTC on Monday is 05:30 in Kolkata: not yet for either.
    early = datetime(2026, 10, 12, 0, 0, tzinfo=UTC)
    assert (await _step(dg8, a, early)).reason == "not_yet"
    # 01:00 UTC is 06:30 in Kolkata: due for A, and not yet for B in UTC.
    one = datetime(2026, 10, 12, 1, 0, tzinfo=UTC)
    assert (await _step(dg8, b, one)).reason == "not_yet"
    got = await _step(dg8, a, one)
    assert got.reason == "stored", got
    [row] = _row(dg8, a)
    assert row.week_start == date(2026, 10, 5) and row.numbers["zone"] == "Asia/Kolkata"
    # Mirror mode keeps no created date (CRM-Q8): those parts are named missing.
    assert "New leads in the week" in row.numbers["missing"]
    assert "New leads in the week" in dg8.model.prompts()[0]
    assert _row(dg8, b) == []


# ── Items 9, 11 and 12: the cap of 3 tries, with a fake model ───────────────


async def test_a_made_up_number_is_never_stored_and_three_tries_cap_the_week(dg8) -> None:
    a = dg8.org_a
    await _native_org(dg8, a)
    dg8.model.reply = make_up_a_number

    first = await _step(dg8, a)
    assert first.reason == "tries_cap" and first.requests == 3 and first.rejected == 3
    for hours in (1, 30, 100):
        again = await _step(dg8, a, NOW + timedelta(hours=hours))
        assert again.requests == 0
    # Item 11: 3 requests in the week, each ONE request of bounded size.
    assert len(dg8.model.calls) == 3
    for call in dg8.model.calls:
        assert call["model"] == "tier-powerful" and call["max_tokens"] == 800
        assert sum(len(m["content"]) for m in call["messages"]) <= 8000
    [row] = _row(dg8, a)
    assert row.tries == 3 and row.body is None and row.stored_at is None
    assert row.request_id == "req-3"
    # Item 13: a try that failed is never served.
    res = await _call(dg8, a, "GET", "/crm/digests")
    assert res.status_code == 200 and res.json() == {"digests": []}


async def test_a_second_try_that_passes_is_stored(dg8) -> None:
    a = dg8.org_a
    await _native_org(dg8, a)
    replies = iter([make_up_a_number, quote_first_number])
    dg8.model.reply = lambda m: next(replies)(m)
    got = await _step(dg8, a)
    assert got.reason == "stored" and got.requests == 2 and got.rejected == 1
    [row] = _row(dg8, a)
    assert row.tries == 2 and "98765" not in row.body


# ── Item 7: the labels, in ``on`` only ──────────────────────────────────────


async def _labeled_note(p: Any, org: str, kind: str, needs: float, at: datetime) -> None:
    with p.admin_engine.begin() as c:
        company = c.execute(text("INSERT INTO crm_companies (organization_id, name) "
                                 "VALUES (CAST(:o AS uuid), 'Acme') RETURNING id"),
                            {"o": org}).scalar()
        act = c.execute(text(
            "INSERT INTO crm_activities (organization_id, type, subject, body, created_by, "
            "occurred_at, company_id) VALUES (CAST(:o AS uuid), 'note', 'S', 'B', "
            "'x@y.test', :at, :c) RETURNING id"), {"o": org, "at": at, "c": company}).scalar()
        c.execute(text(
            "INSERT INTO crm_note_labels (organization_id, activity_id, kind, needs_action, "
            "mode, body_md5, labeled_at) VALUES (CAST(:o AS uuid), :a, :k, :n, 'on', 'h', "
            "now())"), {"o": org, "a": act, "k": kind, "n": needs})


@pytest.mark.parametrize("mode", ["on", "shadow"])
async def test_the_labels_count_in_on_only(dg8, monkeypatch, mode) -> None:
    from gateway.crm_sync import note_labels

    a = dg8.org_a
    await _native_org(dg8, a)
    await _labeled_note(dg8, a, "payment", 0.9, IN_WEEK)
    await _labeled_note(dg8, a, "payment", 0.1, IN_WEEK)
    await _labeled_note(dg8, a, "site_issue", 0.8, IN_WEEK)
    await _labeled_note(dg8, a, "payment", 0.9, BEFORE_WEEK)
    monkeypatch.setattr(note_labels, "mode", lambda _org: mode)
    await _step(dg8, a)
    [row] = _row(dg8, a)
    part = _part(row.numbers, "note_labels")
    if mode == "shadow":
        assert part is None and "note_labels" not in dg8.model.prompts()[0]
        return
    assert part["rows"] == [
        {"label": "Payment notes", "value": "2"},
        {"label": "Payment notes that need action", "value": "1"},
        {"label": "Site issue notes", "value": "1"},
        {"label": "Site issue notes that need action", "value": "1"},
    ]


# ── Item 10: the payer ──────────────────────────────────────────────────────


async def test_a_payer_who_left_spends_nothing(dg8) -> None:
    a = dg8.org_a
    await _native_org(dg8, a)
    _member_of(dg8, OWNER, a, status="suspended")
    got = await _step(dg8, a)
    assert got.reason == "member_left" and dg8.model.calls == [] and _row(dg8, a) == []


async def test_no_connection_spends_nothing(dg8) -> None:
    a = dg8.org_a
    await _native(dg8, a)
    got = await _step(dg8, a)
    assert got.reason == "no_member" and dg8.model.calls == [] and _row(dg8, a) == []


# ── Item 13: the route ──────────────────────────────────────────────────────


async def test_the_route_serves_the_stored_digest_of_the_bound_org(dg8) -> None:
    a, b = dg8.org_a, dg8.org_b
    await _native_org(dg8, a)
    await _native_org(dg8, b)
    await _step(dg8, a)
    res = await _call(dg8, a, "GET", "/crm/digests")
    assert res.status_code == 200
    [only] = res.json()["digests"]
    assert only["week_start"] == "2026-10-05" and only["week_end"] == "2026-10-11"
    assert only["text"] == _row(dg8, a)[0].body
    assert only["numbers"]["parts"]
    # Tenant fence: org B reads none of A's.
    assert (await _call(dg8, b, "GET", "/crm/digests")).json() == {"digests": []}
    assert (await _call(dg8, a, "GET", "/crm/digests?limit=13")).status_code == 422


async def test_the_route_answers_403_to_a_member_the_access_rule_refuses(dg8,
                                                                         monkeypatch) -> None:
    from acb_auth import UserContext, UserRole, build_access

    from tests.unit import test_crm_next_activity_r8 as helper

    a = dg8.org_a
    await _native_org(dg8, a)
    await _step(dg8, a)

    def _plain(org: str) -> Any:
        return UserContext(email=f"plain@{DOMAIN}", role=UserRole.EMPLOYEE,
                           access=build_access(["feature:crm"]), organization_id=org)

    # Native mode seeds the access rule `admins`: a plain member is refused.
    with dg8.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET access = 'admins' "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": a})
    monkeypatch.setattr(helper, "_admin", _plain)
    assert (await _call(dg8, a, "GET", "/crm/digests")).status_code == 403


# ── Item 14: the My Day read ────────────────────────────────────────────────


async def test_my_day_reads_a_digest_for_one_day(dg8, monkeypatch) -> None:
    from acb_auth import UserContext, UserRole, build_access
    from acb_common import get_settings
    from acb_common.db import bind_tenant, release_tenant
    from gateway.routes.crm import aggregate

    a = dg8.org_a
    await _native_org(dg8, a)
    await _step(dg8, a)
    stored_at = _row(dg8, a)[0].stored_at
    user = UserContext(email=OWNER, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:crm"]), organization_id=a)

    async def rows(now: datetime) -> Any:
        token = bind_tenant(a)
        try:
            async with tenant_engine_scope(dg8.app_url.render_as_string(hide_password=False)):
                return await aggregate.my_day_rows(user, now=now, limit=5)
        finally:
            release_tenant(token)

    got = await rows(stored_at + timedelta(hours=2))
    assert got["digest"] == {"week_start": date(2026, 10, 5), "stored_at": stored_at}
    assert (await rows(stored_at + timedelta(days=1, minutes=1)))["digest"] is None
    monkeypatch.setattr(get_settings(), "crm_digest_orgs", "", raising=False)
    assert (await rows(stored_at + timedelta(hours=2)))["digest"] is None
