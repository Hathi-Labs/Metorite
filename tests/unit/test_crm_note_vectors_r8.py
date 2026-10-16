"""R8 — search of CRM note text by meaning, against a real Postgres (WS-53 CRM-Q27).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q27 item 18.

The migration adds ``crm_note_vectors``. ``gateway/crm_sync/note_vectors.py``
reads the new or changed notes with an anti-join on ``body_md5``, sends their
text to a fake embed door, and writes one row for each note.
``GET /crm/notes/search`` reads the nearest notes by cosine distance. Every
step here runs as the NON-privileged role of the phase-4 rehearsal, so RLS
binds each read and each write. A fake agrees with any SQL, so only this
suite proves the table, the anti-join, the caps, the search and the tenant
fence.

1. The table: its columns, its keys, the partial HNSW index, FORCE RLS and
   its policy, the cascade from ``crm_activities``, and a replay.
2. A step through a fake door: one row for each note, the md5 and the vector,
   with no session open across the door.
3. A note that did not change is never sent again. A changed note is.
4. The cap of 640 notes, newest first, and the daily cap of requests.
5. A failed vector, a zero vector, a door that is down, and a Redis failure.
6. An org outside ``CRM_NOTE_SEARCH_ORGS``, and the payer check.
7. Tenant isolation of the step and of the search.
8. The search: nearest first, the shape of a hit, the limit, the degraded
   answers, and no text in a log line.

The start over is in ``test_crm_reset_r8.py``.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import structlog
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import _exec_file, tenant_engine_scope
from tests.unit.test_crm_next_activity_r8 import _call
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"
OWNER = "admin@crm-q27-r8.test"
OWNER_B = "admin-b@crm-q27-r8.test"
SECRET_A = "SECRET-A-pump-at-plant-7-leaks"
SECRET_B = "SECRET-B-the-other-org-pump-note"
QUERY = "QUERY-TEXT-where-does-the-pump-leak"
DIMS = 1536

#: Child tables first, so each DELETE meets no foreign key.
_TABLES = (
    "crm_note_vectors", "crm_activities", "crm_module_records", "crm_deal_contacts",
    "crm_deals", "crm_leads", "crm_contacts", "crm_companies", "crm_pipeline_stages",
    "crm_pipelines", "crm_settings", "integration_connections",
    "crm_deal_statuses", "crm_lead_statuses", "crm_lost_reasons",
)

#: The axis of each topic word. A text gets 1.0 on the axis of each word it
#: holds, and 0.01 on the last axis, so no text is a zero vector.
_TOPICS = {"pump": 0, "invoice": 1, "visit": 2, "quote": 3}


def topic_vector(words: str) -> list[float]:
    vec = [0.0] * DIMS
    vec[-1] = 0.01
    for word, axis in _TOPICS.items():
        if word in words.lower():
            vec[axis] = 1.0
    return vec


class FakeDoor:
    """The embed door. A text with BAD gets ``None``, a text with ZERO a zero
    vector. ``down`` answers ``None`` for each text. ``open`` reads how many
    tenant sessions are open at the time of a call."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.open_at_call: list[int] = []
        self.down = False
        self.open = lambda: 0

    async def __call__(self, texts, *, org=None, timeout=20.0):
        self.calls.append(list(texts))
        self.open_at_call.append(self.open())
        if any(not t for t in texts):
            # The vendor refuses an empty input for the WHOLE request.
            return [None] * len(texts)
        out: list[Any] = []
        for t in texts:
            if self.down or "BAD" in t:
                out.append(None)
            elif "ZERO" in t:
                out.append([0.0] * DIMS)
            else:
                out.append(topic_vector(t))
        return out

    def texts(self) -> list[str]:
        return [t for c in self.calls for t in c]


@pytest.fixture
def nv(promoted, monkeypatch):
    """The promoted catalog, the CRM switch on, both orgs listed, cleaned after."""
    from acb_common import get_settings
    from acb_common import tenant_redis as tr
    from acb_common.tenant_redis import TenantRedis
    from gateway.crm_sync import note_vectors
    from gateway.kb_index import embed as embed_door
    from gateway.routes.crm import note_search

    from tests.unit._crm_fakes import crm_switch_on
    from tests.unit.test_feed_decide import FakeRedis

    crm_switch_on(monkeypatch, "*")
    monkeypatch.setattr(get_settings(), "crm_note_search_orgs",
                        f"{promoted.org_a},{promoted.org_b}", raising=False)
    # The member check resolves through the RLS-exempt identity tables, as
    # production does with the cutover on (my_day_loop.departed).
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    door = FakeDoor()
    monkeypatch.setattr(embed_door, "embed", door)
    promoted.door = door

    # Count the open tenant sessions of the step and of the route, so the
    # door can say whether a session stayed open across a call.
    opened = {"n": 0}

    def _counting(real):
        @contextlib.asynccontextmanager
        async def _session(*args, **kwargs):
            async with real(*args, **kwargs) as db:
                opened["n"] += 1
                try:
                    yield db
                finally:
                    opened["n"] -= 1
        return _session

    monkeypatch.setattr(note_vectors, "tenant_session", _counting(note_vectors.tenant_session))
    monkeypatch.setattr(note_search, "_tenant_session", _counting(note_search._tenant_session))
    door.open = lambda: opened["n"]

    redis = FakeRedis()
    monkeypatch.setattr(tr, "get_tenant_redis", lambda **_: TenantRedis(redis))
    promoted.redis = redis
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
                       "WHERE m.user_id = ui.id AND ui.email LIKE '%@crm-q27-r8.test'"))
        c.execute(text("DELETE FROM user_identity WHERE email LIKE '%@crm-q27-r8.test'"))
        c.execute(text("DELETE FROM app_user WHERE email LIKE '%@crm-q27-r8.test'"))


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
        uid = c.execute(text(
            "INSERT INTO user_identity (email, display_name) VALUES (:e, :e) "
            "ON CONFLICT (lower(email)) DO UPDATE SET updated_at = now() "
            "RETURNING id::text"), {"e": email}).scalar()
        c.execute(text(
            "INSERT INTO org_membership (organization_id, user_id, status, joined_at) "
            "VALUES (CAST(:o AS uuid), CAST(:u AS uuid), :s, now()) "
            "ON CONFLICT (organization_id, user_id) DO UPDATE SET status = EXCLUDED.status"),
            {"o": org, "u": uid, "s": status})


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


def _note(p: Any, org: str, company: str | None, *, subject: str = "Pump",
          body: str = "It leaks.", minutes: int = 0, kind: str = "note",
          module_record: str | None = None) -> str:
    """One note. ``minutes`` moves its ``ext_modified_at``: higher is newer."""
    at = datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=minutes)
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO crm_activities (organization_id, type, subject, body, "
            "company_id, module_record_id, created_by, ext_modified_at, occurred_at) "
            "VALUES (CAST(:o AS uuid), :t, :s, :b, CAST(:c AS uuid), CAST(:m AS uuid), "
            "'seed@crm-q27-r8.test', :at, :at) RETURNING id"),
            {"o": org, "t": kind, "s": subject, "b": body, "c": company,
             "m": module_record, "at": at}).scalar())


def _module_record(p: Any, org: str) -> str:
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO crm_module_records (organization_id, module, ext_source, "
            "ext_id, name, fields) VALUES (CAST(:o AS uuid), 'Site_Visits', 'zoho', "
            "'sv-1', 'Visit 1', '{}'::jsonb) RETURNING id"), {"o": org}).scalar())


def _rows(p: Any, org: str) -> dict[str, Any]:
    rows = _q(p, "SELECT activity_id::text, body_md5, embedding IS NOT NULL, "
                 "embedded_at IS NOT NULL, failures, failed_at IS NOT NULL "
                 "FROM crm_note_vectors WHERE organization_id = CAST(:o AS uuid)", o=org)
    return {r[0]: tuple(r[1:]) for r in rows}


def _md5(p: Any, activity: str) -> str:
    return _q(p, "SELECT md5(coalesce(subject, '') || coalesce(body, '')) FROM "
                 "crm_activities WHERE id = CAST(:i AS uuid)", i=activity)[0][0]


def _requests_today(p: Any, org: str, day: str) -> int:
    from gateway.crm_sync import note_vectors

    return int(p.redis.store.get(f"cc:{org}:{note_vectors.NAMESPACE}:requests:{day}", "0"))


async def _step(p: Any, org: str, now: datetime | None = None) -> Any:
    from gateway.crm_sync import note_vectors

    async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
        return await note_vectors.embed_after_run(org, now=now)


async def _search(p: Any, org: str, q: str = QUERY, limit: int | None = None) -> Any:
    path = f"/crm/notes/search?q={q}" + (f"&limit={limit}" if limit is not None else "")
    return await _call(p, org, "GET", path)


def _migration() -> Path:
    found = [f for f in _MIGRATIONS.glob("*.sql")
             if "CREATE TABLE IF NOT EXISTS crm_note_vectors" in f.read_text(encoding="utf-8")]
    assert len(found) == 1, [f.name for f in found]
    return found[0]


DAY = datetime(2026, 10, 16, 1, 0, tzinfo=UTC)


# ── 1. The table ─────────────────────────────────────────────────────────────


def test_the_columns_the_key_and_the_cascade(nv) -> None:
    cols = dict(_q(nv, "SELECT column_name, is_nullable FROM information_schema.columns "
                       "WHERE table_name = 'crm_note_vectors'"))
    assert set(cols) == {"organization_id", "activity_id", "body_md5", "embedding",
                         "embedded_at", "failures", "failed_at"}
    assert cols["organization_id"] == cols["activity_id"] == cols["body_md5"] == "NO"
    assert cols["embedding"] == "YES"
    kind = _q(nv, "SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE "
                  "attrelid = to_regclass('public.crm_note_vectors') AND attname = 'embedding'")
    assert kind == [("vector(1536)",)]
    pk = _q(nv, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = "
                "to_regclass('public.crm_note_vectors') AND contype = 'p'")
    assert pk == [("PRIMARY KEY (activity_id)",)]
    fks = dict(_q(nv, "SELECT confrelid::regclass::text, confdeltype FROM pg_constraint "
                      "WHERE conrelid = to_regclass('public.crm_note_vectors') AND contype = 'f'"))
    assert fks == {"organization": "c", "crm_activities": "c"}


def test_a_partial_hnsw_index_covers_the_vectors(nv) -> None:
    defs = [r[0] for r in _q(nv, "SELECT indexdef FROM pg_indexes "
                                 "WHERE tablename = 'crm_note_vectors'")]
    hnsw = [d for d in defs if "hnsw" in d]
    assert len(hnsw) == 1
    assert "vector_cosine_ops" in hnsw[0] and "WHERE (embedding IS NOT NULL)" in hnsw[0]


def test_force_rls_and_one_policy_with_using_and_with_check(nv) -> None:
    assert _q(nv, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                  "WHERE oid = to_regclass('public.crm_note_vectors')") == [(True, True)]
    assert _q(nv, "SELECT qual IS NOT NULL, with_check IS NOT NULL FROM pg_policies "
                  "WHERE tablename = 'crm_note_vectors'") == [(True, True)]


def test_the_migration_alone_sets_force_rls_and_its_policy(nv) -> None:
    """The generated phase 4 also forces RLS on the table, so the catalog
    above would read the same without the migration's own block. Strip the
    RLS, replay the migration, and read it back."""
    with nv.admin_engine.begin() as c:
        c.execute(text("DROP POLICY IF EXISTS crm_note_vectors_tenant_isolation "
                       "ON crm_note_vectors"))
        c.execute(text("ALTER TABLE crm_note_vectors NO FORCE ROW LEVEL SECURITY"))
        c.execute(text("ALTER TABLE crm_note_vectors DISABLE ROW LEVEL SECURITY"))
    with nv.admin_engine.begin() as c:
        _exec_file(c, str(_migration()))
    assert _q(nv, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                  "WHERE oid = to_regclass('public.crm_note_vectors')") == [(True, True)]
    assert _q(nv, "SELECT policyname FROM pg_policies WHERE tablename = 'crm_note_vectors'"
              ) == [("crm_note_vectors_tenant_isolation",)]


def test_a_replay_changes_nothing(nv) -> None:
    def shape() -> tuple:
        return (
            _q(nv, "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE "
                   "conrelid = to_regclass('public.crm_note_vectors') ORDER BY conname"),
            _q(nv, "SELECT indexdef FROM pg_indexes WHERE tablename = 'crm_note_vectors' "
                   "ORDER BY indexdef"),
            _q(nv, "SELECT policyname FROM pg_policies WHERE tablename = 'crm_note_vectors'"),
        )

    before = shape()
    with nv.admin_engine.begin() as c:
        _exec_file(c, str(_migration()))
    assert shape() == before


def test_a_deleted_note_takes_its_vector_with_it(nv) -> None:
    a = nv.org_a
    act = _note(nv, a, _company(nv, a))
    with nv.admin_engine.begin() as c:
        c.execute(text("INSERT INTO crm_note_vectors (organization_id, activity_id, "
                       "body_md5) VALUES (CAST(:o AS uuid), CAST(:a AS uuid), 'x')"),
                  {"o": a, "a": act})
        c.execute(text("DELETE FROM crm_activities WHERE id = CAST(:a AS uuid)"), {"a": act})
    assert _rows(nv, a) == {}


def test_rls_keeps_each_org_to_its_own_rows(nv, app_engine) -> None:
    a, b = nv.org_a, nv.org_b
    act_a = _note(nv, a, _company(nv, a))
    act_b = _note(nv, b, _company(nv, b))
    with nv.admin_engine.begin() as c:
        for org, act in ((a, act_a), (b, act_b)):
            c.execute(text("INSERT INTO crm_note_vectors (organization_id, activity_id, "
                           "body_md5) VALUES (CAST(:o AS uuid), CAST(:a AS uuid), 'x')"),
                      {"o": org, "a": act})
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": a})
        seen = c.execute(text("SELECT activity_id::text FROM crm_note_vectors")).scalars().all()
        assert seen == [act_a]
    with pytest.raises(DBAPIError), app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": a})
        c.execute(text("UPDATE crm_note_vectors SET organization_id = CAST(:b AS uuid)"),
                  {"b": b})


# ── 2. A step through a fake door ────────────────────────────────────────────


async def test_a_step_embeds_each_note_once_with_no_session_open(nv) -> None:
    a = nv.org_a
    _connect(nv, a)
    company = _company(nv, a)
    ids = [_note(nv, a, company, subject=f"Visit {i}", minutes=i) for i in range(70)]
    _note(nv, a, company, kind="call", subject="A call is not a note")
    _note(nv, a, company, subject="", body="")

    got = await _step(nv, a)

    assert got.reason == "done" and got.embedded == 70 and got.requests == 2
    assert [len(c) for c in nv.door.calls] == [64, 6]
    # Item 7: no tenant session stays open across a call of the door.
    assert nv.door.open_at_call == [0, 0]
    rows = _rows(nv, a)
    assert set(rows) == set(ids)
    assert rows[ids[0]] == (_md5(nv, ids[0]), True, True, 0, False)
    # Newest first, and the text is the subject and the body.
    assert nv.door.calls[0][0] == "Visit 69\nIt leaks."


async def test_the_text_is_clipped_to_2000_characters(nv) -> None:
    a = nv.org_a
    _connect(nv, a)
    _note(nv, a, _company(nv, a), subject="S", body="x" * 5000)
    await _step(nv, a)
    assert [len(t) for t in nv.door.texts()] == [2000]


# ── 3. New or changed ────────────────────────────────────────────────────────


async def test_an_unchanged_note_is_never_sent_again(nv) -> None:
    a = nv.org_a
    _connect(nv, a)
    company = _company(nv, a)
    for i in range(3):
        _note(nv, a, company, minutes=i)
    await _step(nv, a)
    calls = len(nv.door.calls)
    again = await _step(nv, a)
    assert again.reason == "none" and len(nv.door.calls) == calls


async def test_a_changed_note_is_sent_again_and_its_md5_moves(nv) -> None:
    a = nv.org_a
    _connect(nv, a)
    company = _company(nv, a)
    keep = _note(nv, a, company, body="same", minutes=1)
    change = _note(nv, a, company, body="before", minutes=2)
    await _step(nv, a)
    before = _rows(nv, a)
    with nv.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_activities SET body = 'after' WHERE id = CAST(:i AS uuid)"),
                  {"i": change})
    nv.door.calls.clear()
    got = await _step(nv, a)
    assert got.embedded == 1 and nv.door.texts() == ["Pump\nafter"]
    after = _rows(nv, a)
    assert after[keep] == before[keep]
    assert after[change][0] == _md5(nv, change) != before[change][0]


# ── 4. The caps ──────────────────────────────────────────────────────────────


async def test_a_step_takes_at_most_640_notes_newest_first(nv) -> None:
    a = nv.org_a
    _connect(nv, a)
    company = _company(nv, a)
    with nv.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_activities (organization_id, type, subject, body, company_id, "
            "created_by, ext_modified_at) SELECT CAST(:o AS uuid), 'note', 'N' || g, 'b', "
            "CAST(:c AS uuid), 'seed@crm-q27-r8.test', "
            "timestamptz '2026-10-01' + make_interval(mins => g) "
            "FROM generate_series(1, 700) AS g"), {"o": a, "c": company})
    got = await _step(nv, a, now=DAY)
    assert got.embedded == 640 and got.requests == 10 and len(nv.door.calls) == 10
    assert _requests_today(nv, a, "2026-10-16") == 10
    waiting = _q(nv, "SELECT a.subject FROM crm_activities a LEFT JOIN crm_note_vectors v "
                     "ON v.activity_id = a.id WHERE a.organization_id = CAST(:o AS uuid) "
                     "AND v.activity_id IS NULL ORDER BY a.ext_modified_at", o=a)
    assert [r[0] for r in waiting] == [f"N{g}" for g in range(1, 61)]


async def test_the_daily_cap_counts_requests_over_the_whole_utc_day(nv, monkeypatch) -> None:
    from gateway.crm_sync import note_vectors

    monkeypatch.setattr(note_vectors, "DAILY_REQUESTS", 2)
    monkeypatch.setattr(note_vectors, "NOTES_PER_REQUEST", 3)
    a = nv.org_a
    _connect(nv, a)
    company = _company(nv, a)
    for i in range(10):
        _note(nv, a, company, minutes=i)
    first = await _step(nv, a, now=DAY)
    assert first.embedded == 6 and first.requests == 2
    assert _requests_today(nv, a, "2026-10-16") == 2
    later = await _step(nv, a, now=DAY + timedelta(hours=4))
    assert later.reason == "daily_cap" and len(nv.door.calls) == 2
    nxt = await _step(nv, a, now=DAY + timedelta(days=1))
    assert nxt.embedded == 4 and _requests_today(nv, a, "2026-10-17") == 2


async def test_the_cap_holds_when_the_count_moves_after_the_read(nv, monkeypatch) -> None:
    """A second worker spends the org's requests after this step read the
    count. The reserve before each request still holds the cap."""
    from gateway.crm_sync import note_vectors

    monkeypatch.setattr(note_vectors, "DAILY_REQUESTS", 2)
    a = nv.org_a
    _connect(nv, a)
    company = _company(nv, a)
    for i in range(5):
        _note(nv, a, company, minutes=i)

    async def _stale(org, utc_date):
        return 0

    monkeypatch.setattr(note_vectors, "requests_used", _stale)
    nv.redis.store[f"cc:{a}:{note_vectors.NAMESPACE}:requests:2026-10-16"] = "2"
    got = await _step(nv, a, now=DAY)
    assert got.reason == "daily_cap" and got.requests == 0 and nv.door.calls == []
    assert _requests_today(nv, a, "2026-10-16") == 2


async def test_a_redis_failure_embeds_nothing(nv) -> None:
    a = nv.org_a
    _connect(nv, a)
    _note(nv, a, _company(nv, a))
    nv.redis.fail = ConnectionError("redis down")
    got = await _step(nv, a, now=DAY)
    assert got.reason == "failed" and nv.door.calls == [] and _rows(nv, a) == {}


# ── 5. Failed vectors ────────────────────────────────────────────────────────


async def test_a_failed_vector_counts_parks_after_three_and_comes_back(nv) -> None:
    a = nv.org_a
    _connect(nv, a)
    company = _company(nv, a)
    bad = _note(nv, a, company, body="BAD", minutes=100)
    good = [_note(nv, a, company, body=f"good {i}", minutes=i) for i in range(3)]

    first = await _step(nv, a, now=DAY)
    assert first.embedded == 3 and first.failed == 1
    rows = _rows(nv, a)
    assert rows[bad] == (_md5(nv, bad), False, False, 1, True)
    assert all(rows[g][1] for g in good)
    for _ in range(2):
        await _step(nv, a, now=DAY)
    assert _rows(nv, a)[bad][3] == 3
    # Parked: the next step sends nothing.
    calls = len(nv.door.calls)
    assert (await _step(nv, a, now=DAY)).reason == "none"
    assert len(nv.door.calls) == calls
    # Seven days later it comes back.
    with nv.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_note_vectors SET failed_at = now() - interval '8 days' "
                       "WHERE activity_id = CAST(:i AS uuid)"), {"i": bad})
    assert (await _step(nv, a, now=DAY)).failed == 1
    # A change of its text brings it back at once, and the count starts again.
    with nv.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_activities SET body = 'fixed' WHERE id = CAST(:i AS uuid)"),
                  {"i": bad})
    assert (await _step(nv, a, now=DAY)).embedded == 1
    assert _rows(nv, a)[bad] == (_md5(nv, bad), True, True, 0, False)


async def test_a_failure_after_a_text_change_keeps_no_old_vector(nv) -> None:
    a = nv.org_a
    _connect(nv, a)
    act = _note(nv, a, _company(nv, a), body="text A")
    assert (await _step(nv, a, now=DAY)).embedded == 1
    with nv.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_activities SET body = 'BAD text B' "
                       "WHERE id = CAST(:i AS uuid)"), {"i": act})
    assert (await _step(nv, a, now=DAY)).failed == 1
    assert _rows(nv, a)[act] == (_md5(nv, act), False, False, 1, True)


async def test_a_zero_vector_is_never_stored(nv) -> None:
    a = nv.org_a
    _connect(nv, a)
    company = _company(nv, a)
    zero = _note(nv, a, company, body="ZERO", minutes=2)
    fine = _note(nv, a, company, body="fine", minutes=1)
    got = await _step(nv, a, now=DAY)
    assert got.embedded == 1 and got.failed == 1
    rows = _rows(nv, a)
    assert rows[zero][1:4] == (False, False, 1) and rows[fine][1] is True
    assert _q(nv, "SELECT count(*) FROM crm_note_vectors WHERE embedding IS NOT NULL AND "
                  "vector_norm(embedding) = 0") == [(0,)]


async def test_a_door_that_is_down_stops_the_step_after_one_request(nv) -> None:
    a = nv.org_a
    _connect(nv, a)
    company = _company(nv, a)
    for i in range(100):
        _note(nv, a, company, minutes=i)
    nv.door.down = True
    got = await _step(nv, a, now=DAY)
    assert got.reason == "no_vectors" and got.requests == 1 and got.failed == 0
    assert len(nv.door.calls) == 1


async def test_a_note_with_no_text_never_blocks_a_chunk(nv) -> None:
    """Fix round 2 of 2026-10-17 (the reviewer's case). A note of ASCII
    whitespace only, and one of Unicode spaces only, sit among 70 good
    notes, newest first, so both fall in the first chunk. The SQL leaves
    out the first. ``note_text`` strips the second to "", and the step
    marks it done with no vector and sends it never. Before the fix the
    door refused the whole chunk, and each run stopped on it."""
    a = nv.org_a
    _connect(nv, a)
    company = _company(nv, a)
    for i in range(70):
        _note(nv, a, company, body=f"good {i}", minutes=i)
    ascii_blank = _note(nv, a, company, subject=" ", body=" \n\t ", minutes=200)
    unicode_blank = _note(nv, a, company, subject=None, body=chr(0xA0) + chr(0x2003), minutes=201)

    embedded = 0
    for _ in range(2):
        embedded += (await _step(nv, a, now=DAY)).embedded
    assert embedded == 70
    assert all(t.strip() for t in nv.door.texts()), "an empty text went out"
    rows = _rows(nv, a)
    assert ascii_blank not in rows
    # Done, with no vector and no failure.
    assert rows[unicode_blank] == (_md5(nv, unicode_blank), False, True, 0, False)
    calls = len(nv.door.calls)
    assert (await _step(nv, a, now=DAY)).reason == "none"
    assert len(nv.door.calls) == calls
    # A real text brings it back.
    with nv.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_activities SET body = 'pump fixed' "
                       "WHERE id = CAST(:i AS uuid)"), {"i": unicode_blank})
    assert (await _step(nv, a, now=DAY)).embedded == 1


async def test_an_outage_parks_no_note(nv) -> None:
    """Fix round of 2026-10-17. A door that is down for more runs than
    MAX_FAILURES writes no failure row. So when it comes back, every note
    embeds at once, and none waits PARK_DAYS."""
    from gateway.crm_sync.note_labels import MAX_FAILURES

    a = nv.org_a
    _connect(nv, a)
    company = _company(nv, a)
    for i in range(100):
        _note(nv, a, company, minutes=i)
    nv.door.down = True
    for _ in range(MAX_FAILURES + 1):
        assert (await _step(nv, a, now=DAY)).reason == "no_vectors"
    assert _rows(nv, a) == {}
    nv.door.down = False
    got = await _step(nv, a, now=DAY)
    assert got.reason == "done" and got.embedded == 100 and got.failed == 0


# ── 6. The org list and the payer ────────────────────────────────────────────


async def test_an_org_outside_the_list_embeds_nothing(nv, monkeypatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "crm_note_search_orgs", nv.org_b, raising=False)
    a = nv.org_a
    _connect(nv, a)
    _note(nv, a, _company(nv, a))
    assert (await _step(nv, a, now=DAY)).reason == "off"
    assert nv.door.calls == [] and _rows(nv, a) == {}
    assert _requests_today(nv, a, "2026-10-16") == 0


async def test_no_connection_member_skips(nv) -> None:
    a = nv.org_a
    _connect(nv, a, member="no-address")
    _note(nv, a, _company(nv, a))
    got = await _step(nv, a, now=DAY)
    assert got.reason == "no_member" and got.notes == 1
    assert nv.door.calls == [] and _requests_today(nv, a, "2026-10-16") == 0


async def test_a_member_who_left_embeds_nothing(nv) -> None:
    a = nv.org_a
    _connect(nv, a)
    _note(nv, a, _company(nv, a))
    with nv.admin_engine.begin() as c:
        c.execute(text("UPDATE app_user SET status = 'removed' WHERE lower(email) = :e"),
                  {"e": OWNER})
    got = await _step(nv, a, now=DAY)
    assert got.reason == "member_left" and nv.door.calls == []
    assert _requests_today(nv, a, "2026-10-16") == 0


# ── 7. Tenant isolation of the step ──────────────────────────────────────────


async def test_a_step_reads_and_writes_its_own_org_only(nv) -> None:
    a, b = nv.org_a, nv.org_b
    for org in (a, b):
        _connect(nv, org)
    mine = _note(nv, a, _company(nv, a), body="mine")
    theirs = _note(nv, b, _company(nv, b), body=SECRET_B)
    assert (await _step(nv, a)).embedded == 1
    assert nv.door.texts() == ["Pump\nmine"]
    assert set(_rows(nv, a)) == {mine} and _rows(nv, b) == {}
    await _step(nv, b)
    assert set(_rows(nv, b)) == {theirs} and set(_rows(nv, a)) == {mine}


# ── 8. The search ────────────────────────────────────────────────────────────


async def _seeded(p: Any) -> dict[str, str]:
    """Notes of org A on three topics, embedded, and one of org B."""
    a, b = p.org_a, p.org_b
    for org in (a, b):
        _connect(p, org)
    company = _company(p, a)
    record = _module_record(p, a)
    out = {
        "pump": _note(p, a, company, subject="Site", body=f"The pump {SECRET_A}", minutes=1),
        "invoice": _note(p, a, company, subject="Money", body="The invoice is late", minutes=2),
        "visit": _note(p, a, None, subject="Visit", body="A visit next week",
                       minutes=3, module_record=record),
        "long": _note(p, a, company, subject="Pump", body="y" * 900, minutes=4),
        "theirs": _note(p, b, _company(p, b), subject="Pump", body=SECRET_B, minutes=5),
        "record": record, "company": company,
    }
    await _step(p, a)
    await _step(p, b)
    p.door.calls.clear()
    p.door.open_at_call.clear()
    return out


async def test_a_search_answers_the_nearest_notes_first(nv) -> None:
    seeded = await _seeded(nv)
    res = await _search(nv, nv.org_a, q="pump", limit=3)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["degraded"] is False
    ids = [h["activity_id"] for h in body["hits"]]
    assert set(ids[:2]) == {seeded["pump"], seeded["long"]} and len(ids) == 3
    # The vector of the query is made with no session open (item 12).
    assert nv.door.calls == [["pump"]] and nv.door.open_at_call == [0]


async def test_a_hit_holds_the_id_the_parent_the_time_and_a_snippet_only(nv) -> None:
    seeded = await _seeded(nv)
    body = (await _search(nv, nv.org_a, q="pump", limit=20)).json()
    hits = {h["activity_id"]: h for h in body["hits"]}
    for hit in hits.values():
        assert set(hit) == {"activity_id", "parent", "at", "snippet"}
        assert len(hit["snippet"]) <= 200
    assert hits[seeded["pump"]]["parent"] == {
        "entity": "organizations", "record_id": seeded["company"],
        "module": None, "module_record_id": None}
    assert hits[seeded["visit"]]["parent"] == {
        "entity": None, "record_id": None,
        "module": "Site_Visits", "module_record_id": seeded["record"]}
    assert hits[seeded["pump"]]["snippet"].startswith("Site — The pump")
    assert hits[seeded["pump"]]["at"].startswith("2026-10-01T00:01")
    assert hits[seeded["long"]]["snippet"].endswith("…")
    for word in ("embedding", "distance", "body_md5", "0.01"):
        assert word not in (await _search(nv, nv.org_a, q="pump")).text


async def test_the_limit_is_20_at_most(nv) -> None:
    await _seeded(nv)
    assert (await _search(nv, nv.org_a, q="pump", limit=20)).status_code == 200
    assert (await _search(nv, nv.org_a, q="pump", limit=21)).status_code == 422
    assert (await _search(nv, nv.org_a, q="pump", limit=0)).status_code == 422


async def test_an_org_outside_the_list_gets_degraded_and_no_embed_call(nv, monkeypatch) -> None:
    from acb_common import get_settings

    await _seeded(nv)
    monkeypatch.setattr(get_settings(), "crm_note_search_orgs", nv.org_b, raising=False)
    res = await _search(nv, nv.org_a, q="pump")
    assert res.status_code == 200
    assert res.json() == {"hits": [], "degraded": True}
    assert nv.door.calls == []


async def test_no_vector_for_the_query_is_degraded_with_no_hits(nv) -> None:
    await _seeded(nv)
    nv.door.down = True
    res = await _search(nv, nv.org_a, q="pump")
    assert res.json() == {"hits": [], "degraded": True}
    assert len(nv.door.calls) == 1


async def test_a_search_never_crosses_orgs(nv) -> None:
    seeded = await _seeded(nv)
    res = await _search(nv, nv.org_a, q="pump", limit=20)
    ids = {h["activity_id"] for h in res.json()["hits"]}
    assert seeded["theirs"] not in ids and SECRET_B not in res.text
    res_b = await _search(nv, nv.org_b, q="pump", limit=20)
    assert [h["activity_id"] for h in res_b.json()["hits"]] == [seeded["theirs"]]
    assert SECRET_A not in res_b.text


async def test_no_log_line_holds_a_query_or_note_text(nv) -> None:
    a = nv.org_a
    _connect(nv, a)
    _note(nv, a, _company(nv, a), body=SECRET_A)
    with structlog.testing.capture_logs() as caps:
        await _step(nv, a)
        res = await _search(nv, a, q=QUERY)
    assert res.status_code == 200
    lines = repr(caps)
    assert SECRET_A not in lines and QUERY not in lines
    events = {c.get("event") for c in caps}
    assert {"crm.note_vectors.step", "crm.note_search"} <= events
