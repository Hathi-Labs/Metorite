"""R8 — labels on CRM notes, against a real Postgres (WS-53 CRM-Q26).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q26 item 12.

The migration adds ``crm_note_labels``. ``gateway/crm_sync/note_labels.py``
reads the new or changed notes with an anti-join on ``body_md5``, asks
``decide`` through a fake facade, and writes one row for each note. Every
step here runs as the NON-privileged role of the phase-4 rehearsal, so RLS
binds each read and each write. A fake agrees with any SQL, so only this
suite proves the table, the anti-join, the caps and the tenant fence.

1. The table: its columns, its keys, FORCE RLS and its policy, the CHECKs,
   the cascade from ``crm_activities``, and a replay that changes nothing.
2. A step through a fake decide: one row for each note, the labels, the
   md5, the mode and the request id.
3. A note that did not change is never asked again. A changed note is.
4. The 200 cap, newest first, and the daily cap on ``labeled_at``.
5. The ``off`` path, and an org outside ``CRM_DECIDE_ORGS``.
6. Tenant isolation: a step reads and writes its own org only.
7. No route returns a label: the timeline of a company reads the same with
   and without its labels.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

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
OWNER = "admin@crm-q26-r8.test"
#: The connecting admin of org B. `app_user` is unique on the address, so
#: each org needs its own.
OWNER_B = "admin-b@crm-q26-r8.test"
SECRET_B = "SECRET-B-the-other-org-note"

#: Child tables first, so each DELETE meets no foreign key.
_TABLES = (
    "crm_note_labels", "crm_activities", "crm_deal_contacts", "crm_deals",
    "crm_leads", "crm_contacts", "crm_companies", "crm_pipeline_stages",
    "crm_pipelines", "crm_settings", "integration_connections",
    "crm_deal_statuses", "crm_lead_statuses", "crm_lost_reasons",
)


class FakeDecide:
    """Records each call. Every note is a payment that needs action, high."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, state, questions, **kwargs):
        import acb_llm

        self.calls.append({"state": state, "questions": dict(questions), **kwargs})
        answers: dict[str, Any] = {}
        for qid, q in questions.items():
            if q.type == "boolean":
                answers[qid] = acb_llm.BooleanAnswer(probability=0.875)
            elif q.type == "score":
                answers[qid] = acb_llm.ScoreAnswer(
                    score=2, probabilities=MappingProxyType({"high": 0.75, "low": 0.25}),
                    confidence=0.5, level="high")
            else:
                answers[qid] = acb_llm.ChoiceAnswer(
                    choice="payment",
                    probabilities=MappingProxyType({"payment": 0.75, "other": 0.25}),
                    confidence=0.625)
        return acb_llm.Decision(answers=MappingProxyType(answers),
                                request_id=f"req-{len(self.calls)}")

    def notes_asked(self) -> list[dict[str, Any]]:
        return [n for c in self.calls for n in c["state"]["notes"]]


@pytest.fixture
def lb(promoted, monkeypatch):
    """The promoted catalog, the CRM switch on, the mode set, cleaned after."""
    import acb_llm
    from acb_common import get_settings
    from gateway import decide_features as df

    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    settings = get_settings()
    monkeypatch.setattr(settings, "decide_feature_modes", "crm.note_labels=shadow",
                        raising=False)
    monkeypatch.setattr(settings, "crm_decide_orgs", f"{promoted.org_a},{promoted.org_b}",
                        raising=False)
    # The member check resolves through the RLS-exempt identity tables, as
    # production does with the cutover on (my_day_loop.departed).
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    df._cooldown_until.clear()
    fake = FakeDecide()
    monkeypatch.setattr(acb_llm, "decide", fake)
    promoted.fake = fake
    from acb_common import tenant_redis
    from acb_common.tenant_redis import TenantRedis

    from tests.unit.test_feed_decide import FakeRedis

    redis = FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(redis))
    promoted.redis = redis
    _member_of(promoted, OWNER, promoted.org_a)
    _member_of(promoted, OWNER_B, promoted.org_b)
    yield promoted
    df._cooldown_until.clear()
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})
        c.execute(text("DELETE FROM org_membership m USING user_identity ui "
                       "WHERE m.user_id = ui.id AND ui.email LIKE '%@crm-q26-r8.test'"))
        c.execute(text("DELETE FROM user_identity WHERE email LIKE '%@crm-q26-r8.test'"))
        c.execute(text("DELETE FROM app_user WHERE email LIKE '%@crm-q26-r8.test'"))


# ── Seeds, as the superuser ──────────────────────────────────────────────────


def _q(p: Any, sql: str, **params: Any) -> list[Any]:
    with p.admin_engine.connect() as c:
        return list(c.execute(text(sql), params).all())


def _member_of(p: Any, email: str, org: str, *, status: str = "active",
               app_user: bool = True) -> None:
    """A member of ``org``: the ``app_user`` row and the identity membership."""
    with p.admin_engine.begin() as c:
        if app_user:
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', :s, CAST(:o AS uuid)) "
                "ON CONFLICT DO NOTHING"), {"e": email, "s": status, "o": org})
        uid = c.execute(text(
            "INSERT INTO user_identity (email, display_name) VALUES (:e, :e) "
            "ON CONFLICT (lower(email)) DO UPDATE SET updated_at = now() "
            "RETURNING id::text"), {"e": email}).scalar()
        c.execute(text(
            "INSERT INTO org_membership (organization_id, user_id, status, joined_at) "
            "VALUES (CAST(:o AS uuid), CAST(:u AS uuid), :s, now()) "
            "ON CONFLICT (organization_id, user_id) DO UPDATE SET status = EXCLUDED.status"),
            {"o": org, "u": uid, "s": status})


def _set_status(p: Any, email: str, org: str, status: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE app_user SET status = :s WHERE lower(email) = lower(:e)"),
                  {"s": status, "e": email})
        c.execute(text(
            "UPDATE org_membership m SET status = :s FROM user_identity ui "
            "WHERE m.user_id = ui.id AND lower(ui.email) = lower(:e) "
            "AND m.organization_id = CAST(:o AS uuid)"), {"s": status, "e": email, "o": org})


def _requests_today(p: Any, org: str, day: str) -> int:
    from gateway.crm_sync import note_labels

    return int(p.redis.store.get(f"cc:{org}:{note_labels.NAMESPACE}:requests:{day}", "0"))


def _connect(p: Any, org: str, member: str | None = None) -> None:
    if member is None:
        member = OWNER if org == p.org_a else OWNER_B
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, "
            "owning_app, connected_by) VALUES (CAST(:o AS uuid), 'zoho', 'crm', :m)"),
            {"o": org, "m": member})


def _company(p: Any, org: str, name: str = "Acme") -> str:
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO crm_companies (organization_id, name) "
            "VALUES (CAST(:o AS uuid), :n) RETURNING id"), {"o": org, "n": name}).scalar())


def _note(p: Any, org: str, company: str, *, subject: str = "Pump", body: str = "It leaks.",
          minutes: int = 0, kind: str = "note") -> str:
    """One note. ``minutes`` moves its ``ext_modified_at``: higher is newer."""
    at = datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=minutes)
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO crm_activities (organization_id, type, subject, body, "
            "company_id, created_by, ext_modified_at) VALUES (CAST(:o AS uuid), :t, "
            ":s, :b, CAST(:c AS uuid), 'seed@crm-q26-r8.test', :at) RETURNING id"),
            {"o": org, "t": kind, "s": subject, "b": body, "c": company, "at": at}).scalar())


def _labels(p: Any, org: str) -> dict[str, Any]:
    rows = _q(p, "SELECT activity_id::text, kind, kind_confidence, kind_probabilities, "
                 "needs_action, urgency, urgency_confidence, urgency_probabilities, "
                 "body_md5, mode, request_id, labeled_at FROM crm_note_labels "
                 "WHERE organization_id = CAST(:o AS uuid)", o=org)
    return {r[0]: r for r in rows}


async def _step(p: Any, org: str, now: datetime | None = None) -> Any:
    from gateway.crm_sync import note_labels

    async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
        return await note_labels.label_after_run(org, now=now)


def _migration() -> Path:
    found = [f for f in _MIGRATIONS.glob("*.sql")
             if "CREATE TABLE IF NOT EXISTS crm_note_labels" in f.read_text(encoding="utf-8")]
    assert len(found) == 1, [f.name for f in found]
    return found[0]


# ── 1. The table ─────────────────────────────────────────────────────────────


def test_the_columns_the_key_and_the_cascade(lb) -> None:
    cols = dict(_q(lb, "SELECT column_name, is_nullable FROM information_schema.columns "
                       "WHERE table_name = 'crm_note_labels'"))
    assert set(cols) == {
        "organization_id", "activity_id", "kind", "kind_confidence", "kind_probabilities",
        "needs_action", "urgency", "urgency_confidence", "urgency_probabilities",
        "body_md5", "mode", "labeled_at", "request_id", "failures", "failed_at"}
    assert cols["organization_id"] == cols["activity_id"] == cols["body_md5"] == "NO"
    pk = _q(lb, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = "
                "to_regclass('public.crm_note_labels') AND contype = 'p'")
    assert pk == [("PRIMARY KEY (activity_id)",)]
    fks = dict(_q(lb, "SELECT confrelid::regclass::text, confdeltype FROM pg_constraint "
                      "WHERE conrelid = to_regclass('public.crm_note_labels') AND contype = 'f'"))
    assert fks == {"organization": "c", "crm_activities": "c"}


def test_force_rls_and_one_policy_with_using_and_with_check(lb) -> None:
    row = _q(lb, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                 "WHERE oid = to_regclass('public.crm_note_labels')")
    assert row == [(True, True)]
    pol = _q(lb, "SELECT qual IS NOT NULL, with_check IS NOT NULL FROM pg_policies "
                 "WHERE tablename = 'crm_note_labels'")
    assert pol == [(True, True)]


def test_the_migration_alone_sets_force_rls_and_its_policy(lb) -> None:
    """The generated phase 4 also forces RLS on the table, so the catalog
    above would read the same without the migration's own block. Strip the
    RLS, replay the migration, and read it back."""
    with lb.admin_engine.begin() as c:
        c.execute(text("DROP POLICY IF EXISTS crm_note_labels_tenant_isolation "
                       "ON crm_note_labels"))
        c.execute(text("ALTER TABLE crm_note_labels NO FORCE ROW LEVEL SECURITY"))
        c.execute(text("ALTER TABLE crm_note_labels DISABLE ROW LEVEL SECURITY"))
    with lb.admin_engine.begin() as c:
        _exec_file(c, str(_migration()))
    assert _q(lb, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                  "WHERE oid = to_regclass('public.crm_note_labels')") == [(True, True)]
    assert _q(lb, "SELECT policyname FROM pg_policies WHERE tablename = 'crm_note_labels'"
              ) == [("crm_note_labels_tenant_isolation",)]


def test_the_checks_refuse_a_string_that_is_not_ours(lb) -> None:
    a = lb.org_a
    act = _note(lb, a, _company(lb, a))
    for column, value in (("kind", "vendor-text"), ("urgency", "urgentest"),
                          ("mode", "off"), ("needs_action", 1.5)):
        values = {"kind": None, "urgency": None, "mode": "shadow", "needs_action": None,
                  column: value}
        with pytest.raises(IntegrityError), lb.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO crm_note_labels (organization_id, activity_id, kind, urgency, "
                "mode, needs_action, body_md5) VALUES (CAST(:o AS uuid), CAST(:a AS uuid), "
                ":kind, :urgency, :mode, :needs_action, 'x')"), {"o": a, "a": act, **values})


def test_a_deleted_note_takes_its_label_with_it(lb) -> None:
    a = lb.org_a
    act = _note(lb, a, _company(lb, a))
    with lb.admin_engine.begin() as c:
        c.execute(text("INSERT INTO crm_note_labels (organization_id, activity_id, mode, "
                       "body_md5) VALUES (CAST(:o AS uuid), CAST(:a AS uuid), 'shadow', 'x')"),
                  {"o": a, "a": act})
        c.execute(text("DELETE FROM crm_activities WHERE id = CAST(:a AS uuid)"), {"a": act})
    assert _labels(lb, a) == {}


def test_a_replay_changes_nothing(lb) -> None:
    def shape() -> tuple:
        return (
            _q(lb, "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE "
                   "conrelid = to_regclass('public.crm_note_labels') ORDER BY conname"),
            _q(lb, "SELECT indexdef FROM pg_indexes WHERE tablename = 'crm_note_labels' "
                   "ORDER BY indexdef"),
            _q(lb, "SELECT policyname FROM pg_policies WHERE tablename = 'crm_note_labels'"),
        )

    before = shape()
    with lb.admin_engine.begin() as c:
        _exec_file(c, str(_migration()))
    assert shape() == before


def test_rls_keeps_each_org_to_its_own_rows(lb, app_engine) -> None:
    a, b = lb.org_a, lb.org_b
    act_a = _note(lb, a, _company(lb, a))
    act_b = _note(lb, b, _company(lb, b))
    with lb.admin_engine.begin() as c:
        for org, act in ((a, act_a), (b, act_b)):
            c.execute(text("INSERT INTO crm_note_labels (organization_id, activity_id, mode, "
                           "body_md5) VALUES (CAST(:o AS uuid), CAST(:a AS uuid), 'shadow', "
                           "'x')"), {"o": org, "a": act})
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": a})
        seen = c.execute(text("SELECT activity_id::text FROM crm_note_labels")).scalars().all()
        assert seen == [act_a]
    with pytest.raises(DBAPIError), app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": a})
        c.execute(text("UPDATE crm_note_labels SET organization_id = CAST(:b AS uuid)"),
                  {"b": b})


# ── 2. A step through a fake decide ──────────────────────────────────────────


async def test_a_step_labels_each_note_once(lb) -> None:
    a = lb.org_a
    _connect(lb, a)
    company = _company(lb, a)
    ids = [_note(lb, a, company, subject=f"Visit {i}", minutes=i) for i in range(7)]
    _note(lb, a, company, kind="call", subject="A call is not a note")
    _note(lb, a, company, subject="", body="")

    got = await _step(lb, a)

    assert got.reason == "done" and got.labeled == 7 and got.requests == 2
    assert len(lb.fake.calls) == 2
    assert [len(c["state"]["notes"]) for c in lb.fake.calls] == [5, 2]
    assert all(c["member"] == OWNER and c["member_proven"] is True
               and c["module_slug"] == "crm" for c in lb.fake.calls)
    labels = _labels(lb, a)
    assert set(labels) == set(ids)
    row = labels[ids[-1]]
    assert row[1] == "payment" and row[2] == pytest.approx(0.625)
    assert row[3] == {"payment": 0.75, "other": 0.25}
    assert row[4] == pytest.approx(0.875) and row[5] == "high"
    assert row[7] == {"high": 0.75, "low": 0.25}
    assert row[9] == "shadow" and row[10] == "req-1"
    md5 = _q(lb, "SELECT md5(coalesce(subject, '') || coalesce(body, '')) FROM "
                 "crm_activities WHERE id = CAST(:i AS uuid)", i=ids[-1])[0][0]
    assert row[8] == md5
    # Newest first: the first request holds the five newest notes.
    assert [n["subject"] for n in lb.fake.calls[0]["state"]["notes"]] == [
        f"Visit {i}" for i in (6, 5, 4, 3, 2)]


async def test_on_writes_the_same_rows_with_its_mode(lb, monkeypatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "decide_feature_modes", "crm.note_labels=on",
                        raising=False)
    a = lb.org_a
    _connect(lb, a)
    act = _note(lb, a, _company(lb, a))
    assert (await _step(lb, a)).labeled == 1
    assert _labels(lb, a)[act][9] == "on"


# ── 3. New or changed ────────────────────────────────────────────────────────


async def test_an_unchanged_note_is_never_asked_again(lb) -> None:
    a = lb.org_a
    _connect(lb, a)
    company = _company(lb, a)
    for i in range(3):
        _note(lb, a, company, minutes=i)
    await _step(lb, a)
    calls = len(lb.fake.calls)
    again = await _step(lb, a)
    assert again.reason == "none" and len(lb.fake.calls) == calls


async def test_a_changed_note_is_asked_again_and_its_md5_moves(lb) -> None:
    a = lb.org_a
    _connect(lb, a)
    company = _company(lb, a)
    keep = _note(lb, a, company, body="same", minutes=1)
    change = _note(lb, a, company, body="before", minutes=2)
    await _step(lb, a)
    before = _labels(lb, a)
    with lb.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_activities SET body = 'after' WHERE id = CAST(:i AS uuid)"),
                  {"i": change})
    lb.fake.calls.clear()
    got = await _step(lb, a)
    assert got.labeled == 1
    assert [n["body"] for n in lb.fake.notes_asked()] == ["after"]
    after = _labels(lb, a)
    assert after[keep][8] == before[keep][8]
    assert after[change][8] != before[change][8]


# ── 4. The caps ──────────────────────────────────────────────────────────────


async def test_a_step_takes_at_most_200_notes_newest_first(lb) -> None:
    a = lb.org_a
    _connect(lb, a)
    company = _company(lb, a)
    with lb.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_activities (organization_id, type, subject, body, company_id, "
            "created_by, ext_modified_at) SELECT CAST(:o AS uuid), 'note', 'N' || g, 'b', "
            "CAST(:c AS uuid), 'seed@crm-q26-r8.test', "
            "timestamptz '2026-10-01' + make_interval(mins => g) "
            "FROM generate_series(1, 230) AS g"), {"o": a, "c": company})
    got = await _step(lb, a)
    assert got.labeled == 200 and len(lb.fake.calls) == 40
    unlabeled = _q(lb, "SELECT a.subject FROM crm_activities a LEFT JOIN crm_note_labels l "
                       "ON l.activity_id = a.id WHERE a.organization_id = CAST(:o AS uuid) "
                       "AND l.activity_id IS NULL ORDER BY a.ext_modified_at", o=a)
    assert [r[0] for r in unlabeled] == [f"N{g}" for g in range(1, 31)]


DAY = datetime(2026, 10, 16, 1, 0, tzinfo=UTC)


async def test_the_daily_cap_counts_requests_over_the_whole_utc_day(lb, monkeypatch) -> None:
    """Fix round items 2 and 5: the cap counts REQUESTS, in Redis, for one
    UTC day. A request four hours old still counts, and the next day starts
    again."""
    from gateway.crm_sync import note_labels

    monkeypatch.setattr(note_labels, "DAILY_NOTES", 10)  # 2 requests a day
    a = lb.org_a
    _connect(lb, a)
    company = _company(lb, a)
    for i in range(18):
        _note(lb, a, company, minutes=i)
    assert (await _step(lb, a, now=DAY)).labeled == 10
    assert _requests_today(lb, a, "2026-10-16") == 2
    later = await _step(lb, a, now=DAY + timedelta(hours=4))
    assert later.reason == "daily_cap" and len(lb.fake.calls) == 2
    nxt = await _step(lb, a, now=DAY + timedelta(days=1))
    assert nxt.labeled == 8 and _requests_today(lb, a, "2026-10-17") == 2


async def test_a_failed_request_counts_against_the_cap(lb, monkeypatch) -> None:
    import acb_llm
    from gateway.crm_sync import note_labels

    monkeypatch.setattr(note_labels, "DAILY_NOTES", 10)  # 2 requests a day

    async def _invalid(state, questions, **kw):
        raise acb_llm.DecideRequestInvalid(400, {"reason": "too_long"})

    monkeypatch.setattr(acb_llm, "decide", _invalid)
    a = lb.org_a
    _connect(lb, a)
    company = _company(lb, a)
    for i in range(12):
        _note(lb, a, company, minutes=i)
    for _ in range(2):
        assert (await _step(lb, a, now=DAY)).reason == "batch_failed"
    assert _requests_today(lb, a, "2026-10-16") == 2
    assert (await _step(lb, a, now=DAY)).reason == "daily_cap"


async def test_a_backlog_lifts_the_daily_allowance(lb, monkeypatch) -> None:
    from gateway.crm_sync import note_labels

    monkeypatch.setattr(note_labels, "DAILY_NOTES", 10)
    monkeypatch.setattr(note_labels, "BACKLOG_DAILY_NOTES", 30)
    monkeypatch.setattr(note_labels, "BACKLOG_THRESHOLD", 20)
    a = lb.org_a
    _connect(lb, a)
    company = _company(lb, a)
    for i in range(25):
        _note(lb, a, company, minutes=i)
    # 25 notes wait, more than 20: the backlog allowance, 30 notes.
    assert (await _step(lb, a, now=DAY)).labeled == 25
    assert _requests_today(lb, a, "2026-10-16") == 5
    for i in range(25, 40):
        _note(lb, a, company, minutes=i)
    # 15 wait, not more than 20: the normal 10 notes, and 5 are spent.
    got = await _step(lb, a, now=DAY)
    assert got.reason == "daily_cap" and got.labeled == 0


async def test_a_failing_batch_goes_to_the_back_and_parks(lb, monkeypatch) -> None:
    """Fix round item 2: one bad note cannot block the queue."""
    import acb_llm

    real = lb.fake

    async def _picky(state, questions, **kw):
        if any(n["body"] == "BAD" for n in state["notes"]):
            raise acb_llm.DecideRequestInvalid(400, {"reason": "bad"})
        return await real(state, questions, **kw)

    monkeypatch.setattr(acb_llm, "decide", _picky)
    a = lb.org_a
    _connect(lb, a)
    company = _company(lb, a)
    bad = _note(lb, a, company, body="BAD", minutes=100)
    good = [_note(lb, a, company, body=f"good {i}", minutes=i) for i in range(6)]

    first = await _step(lb, a, now=DAY)
    assert first.reason == "batch_failed" and first.labeled == 0
    rows = _q(lb, "SELECT activity_id::text, failures, labeled_at FROM crm_note_labels "
                  "WHERE organization_id = CAST(:o AS uuid)", o=a)
    assert {r[0]: (r[1], r[2]) for r in rows}[bad] == (1, None)
    # The failed batch is at the back now, so the others are labeled.
    for _ in range(4):
        await _step(lb, a, now=DAY)
    labeled = {k for k, v in _labels(lb, a).items() if v[11] is not None}
    assert set(good) <= labeled and bad not in labeled
    failures = _q(lb, "SELECT failures FROM crm_note_labels WHERE activity_id = "
                      "CAST(:i AS uuid)", i=bad)[0][0]
    assert failures == 3
    # Parked: the next step asks nothing about it.
    assert (await _step(lb, a, now=DAY)).reason == "none"
    # A change of its text brings it back, and the count starts again.
    with lb.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_activities SET body = 'fixed' WHERE id = CAST(:i AS uuid)"),
                  {"i": bad})
    assert (await _step(lb, a, now=DAY)).labeled == 1
    row = _q(lb, "SELECT failures, labeled_at IS NOT NULL FROM crm_note_labels WHERE "
                 "activity_id = CAST(:i AS uuid)", i=bad)[0]
    assert tuple(row) == (0, True)


async def test_a_poison_note_parks_alone_with_no_fresh_notes(lb, monkeypatch) -> None:
    """Round 2, P2-1: the steady state with no fresh note. A poison note and
    its four batch-mates fail once together. Then each goes out alone, so the
    four land and only the poison note parks."""
    import acb_llm

    real = lb.fake

    async def _picky(state, questions, **kw):
        if any(n["body"] == "POISON" for n in state["notes"]):
            raise acb_llm.DecideRequestInvalid(400, {"reason": "bad"})
        return await real(state, questions, **kw)

    monkeypatch.setattr(acb_llm, "decide", _picky)
    a = lb.org_a
    _connect(lb, a)
    company = _company(lb, a)
    poison = _note(lb, a, company, body="POISON", minutes=100)
    mates = [_note(lb, a, company, body=f"mate {i}", minutes=i) for i in range(4)]
    for _ in range(6):
        await _step(lb, a, now=DAY)
    labels = _labels(lb, a)
    assert {m for m in mates if labels[m][11] is not None} == set(mates)
    assert labels[poison][11] is None
    state = _q(lb, "SELECT failures FROM crm_note_labels WHERE activity_id = "
                   "CAST(:i AS uuid)", i=poison)[0][0]
    assert state == 3
    # A retried note goes out alone: no request after the first holds two.
    assert [len(c["state"]["notes"]) for c in lb.fake.calls] == [1, 1, 1, 1]
    assert (await _step(lb, a, now=DAY)).reason == "none"


async def test_a_failure_after_a_text_change_keeps_no_old_label(lb, monkeypatch) -> None:
    """Round 2, P2-2: the failure row pairs the NEW md5 with no label, never
    with the label of the old text."""
    import acb_llm

    a = lb.org_a
    _connect(lb, a)
    act = _note(lb, a, _company(lb, a), body="text A")
    assert (await _step(lb, a, now=DAY)).labeled == 1
    before = _labels(lb, a)[act]
    assert before[1] == "payment" and before[10] == "req-1"

    async def _invalid(state, questions, **kw):
        raise acb_llm.DecideRequestInvalid(400, {"reason": "bad"})

    monkeypatch.setattr(acb_llm, "decide", _invalid)
    with lb.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_activities SET body = 'text B' WHERE id = CAST(:i AS uuid)"),
                  {"i": act})
    assert (await _step(lb, a, now=DAY)).reason == "batch_failed"
    row = _labels(lb, a)[act]
    md5_b = _q(lb, "SELECT md5(coalesce(subject, '') || coalesce(body, '')) FROM "
                   "crm_activities WHERE id = CAST(:i AS uuid)", i=act)[0][0]
    assert row[8] == md5_b and row[11] is None
    # kind, its confidence and probabilities, needs_action, urgency, its
    # confidence and probabilities, and the request id: all cleared.
    assert [row[i] for i in (1, 2, 3, 4, 5, 6, 7, 10)] == [None] * 8
    failures = _q(lb, "SELECT failures FROM crm_note_labels WHERE activity_id = "
                      "CAST(:i AS uuid)", i=act)[0][0]
    assert failures == 1


async def test_a_parked_note_waits_seven_days(lb, monkeypatch) -> None:
    a = lb.org_a
    _connect(lb, a)
    act = _note(lb, a, _company(lb, a), body="x")
    md5 = _q(lb, "SELECT md5(coalesce(subject, '') || coalesce(body, '')) FROM "
                 "crm_activities WHERE id = CAST(:i AS uuid)", i=act)[0][0]
    with lb.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_note_labels (organization_id, activity_id, body_md5, mode, "
            "failures, failed_at) VALUES (CAST(:o AS uuid), CAST(:a AS uuid), :m, "
            "'shadow', 3, now() - interval '6 days')"), {"o": a, "a": act, "m": md5})
    assert (await _step(lb, a, now=DAY)).reason == "none"
    with lb.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_note_labels SET failed_at = now() - interval '8 days'"))
    assert (await _step(lb, a, now=DAY)).labeled == 1


# ── 5. Off ───────────────────────────────────────────────────────────────────


async def test_off_writes_nothing_and_asks_nothing(lb, monkeypatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "decide_feature_modes", "", raising=False)
    a = lb.org_a
    _connect(lb, a)
    _note(lb, a, _company(lb, a))
    assert (await _step(lb, a)).reason == "off"
    assert lb.fake.calls == [] and _labels(lb, a) == {}


async def test_an_org_outside_the_crm_list_is_off(lb, monkeypatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "crm_decide_orgs", lb.org_b, raising=False)
    monkeypatch.setattr(get_settings(), "decide_feature_orgs", "*", raising=False)
    a = lb.org_a
    _connect(lb, a)
    _note(lb, a, _company(lb, a))
    assert (await _step(lb, a)).reason == "off"
    assert lb.fake.calls == [] and _labels(lb, a) == {}


async def test_no_connection_member_skips(lb) -> None:
    a = lb.org_a
    _connect(lb, a, member="no-address")
    _note(lb, a, _company(lb, a))
    got = await _step(lb, a)
    assert got.reason == "no_member" and got.notes == 1
    assert lb.fake.calls == [] and _labels(lb, a) == {}


# ── 5b. The member must still belong to THIS org (money) ─────────────────────


async def test_a_member_who_left_with_no_other_org_spends_nothing(lb) -> None:
    a = lb.org_a
    _connect(lb, a)
    _note(lb, a, _company(lb, a))
    _set_status(lb, OWNER, a, "removed")
    got = await _step(lb, a, now=DAY)
    assert got.reason == "member_left" and lb.fake.calls == []
    assert _labels(lb, a) == {} and _requests_today(lb, a, "2026-10-16") == 0


async def test_a_member_in_two_orgs_spends_nothing(lb) -> None:
    a, b = lb.org_a, lb.org_b
    _connect(lb, a)
    _note(lb, a, _company(lb, a))
    _member_of(lb, OWNER, b, app_user=False)  # an active membership in B too
    got = await _step(lb, a, now=DAY)
    assert got.reason == "member_left" and lb.fake.calls == []
    assert _requests_today(lb, a, "2026-10-16") == 0


async def test_a_member_who_moved_to_another_org_spends_nothing(lb) -> None:
    a, b = lb.org_a, lb.org_b
    _connect(lb, a)
    _note(lb, a, _company(lb, a))
    _set_status(lb, OWNER, a, "removed")
    _member_of(lb, OWNER, b, app_user=False)  # B is the one org it resolves to
    got = await _step(lb, a, now=DAY)
    assert got.reason == "member_left" and lb.fake.calls == []
    assert _requests_today(lb, b, "2026-10-16") == 0


async def test_a_removed_member_with_a_stale_membership_spends_nothing(lb) -> None:
    """``org_membership`` can stay ``active`` after a removal. The bound
    ``app_user`` status still refuses."""
    a = lb.org_a
    _connect(lb, a)
    _note(lb, a, _company(lb, a))
    with lb.admin_engine.begin() as c:
        c.execute(text("UPDATE app_user SET status = 'removed' WHERE lower(email) = :e"),
                  {"e": OWNER})
    got = await _step(lb, a, now=DAY)
    assert got.reason == "member_left" and lb.fake.calls == []


# ── 6. Tenant isolation ──────────────────────────────────────────────────────


async def test_a_step_reads_and_writes_its_own_org_only(lb) -> None:
    a, b = lb.org_a, lb.org_b
    for org in (a, b):
        _connect(lb, org)
    mine = _note(lb, a, _company(lb, a), body="mine")
    theirs = _note(lb, b, _company(lb, b), body=SECRET_B)
    got = await _step(lb, a)
    assert got.labeled == 1
    assert [n["body"] for n in lb.fake.notes_asked()] == ["mine"]
    assert set(_labels(lb, a)) == {mine} and _labels(lb, b) == {}
    await _step(lb, b)
    assert set(_labels(lb, b)) == {theirs} and set(_labels(lb, a)) == {mine}


# ── 7. No route returns a label ──────────────────────────────────────────────


async def test_the_timeline_reads_the_same_with_and_without_labels(lb) -> None:
    a = lb.org_a
    await _native(lb, a)
    _connect(lb, a)
    company = _company(lb, a)
    _note(lb, a, company, subject="Visit", body="It leaks.")
    path = f"/crm/organizations/{company}/timeline"
    before = await _call(lb, a, "GET", path)
    assert before.status_code == 200, before.text
    assert (await _step(lb, a)).labeled == 1
    after = await _call(lb, a, "GET", path)
    assert after.status_code == 200
    assert after.json() == before.json()
    body = after.text
    for word in ("payment", "needs_action", "urgency", "body_md5", "req-1"):
        assert word not in body

