"""WS-58 MD-3f, R8: people and chat in My Day on a REAL Postgres
(``my_day_feed.md`` §5.9 item 14, the MD-3f ticket).

The new People read (``absences.away_on``), the lens read of today's
blocks, the chat store (``pending_ask.waiting_asks``) and the room check
run their real SQL on the phase-4-promoted catalog of
``test_h3_rls_promotion_rehearsal.py``. The gateway reads connect as the
NON-privileged role with RLS forced (``_as_member``), and so do the
``acb_graph`` reads of chat. The hermetic half is
``test_feed_people_chat_kinds.py``.

The members of org B:

* **ME** has one task with a block today. Its co-assignees are COLL (away
  today), PART (a ``partial`` absence today), TOMO (away tomorrow only),
  TWIN and an agent. ME is away today too.
* **TWIN** has a People row in BOTH orgs, with the same address. Only the
  row of org A is away today. So a read that loses its org predicate names
  TWIN in org B. A check on a session that bypasses RLS (the superuser)
  proves that the SQL itself holds it. Production connects as ``acb_app``,
  which FORCE RLS holds, so the predicate is defence in depth (R5).
* **OTHER** owns a private chat with a question for ME, so ME cannot send
  there. OTHER is also the one asked in a chat of ME.

Each source must read ``ok``, because a failed provider also gives no card.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import UTC, datetime, time, timedelta

import pytest

pytest.importorskip("sqlalchemy")
pending_ask = pytest.importorskip("orchestrator.pending_ask")

import acb_graph  # noqa: E402
import acb_graph.db as graph_db  # noqa: E402
from acb_auth.permissions import EffectiveAccess  # noqa: E402
from acb_auth.roles import UserContext, UserRole  # noqa: E402
from acb_common.db import bind_tenant, release_tenant, tenant_session  # noqa: E402
from gateway.routes.shell import feed  # noqa: E402
from gateway.routes.shell import needs as shell  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from tests.unit._tenant_ladder import tenant_engine_scope  # noqa: E402

# The catalog, the app role and the binding, shared rather than copied.
# Fixtures are used by name, so the import is load-bearing.
from tests.unit.test_email_keep_separate import (  # noqa: E402, F401
    _DB_GATE,
    _as_member,
    app_engine,
    promoted,
)
from tests.unit.test_h3_rls_promotion_rehearsal import _APP_ROLE  # noqa: E402
from tests.unit.test_shell_needs_r8 import NOW, _Frozen, _project, _task  # noqa: E402

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ME = f"pc-me-{TAG}@md3f.test"
COLL = f"pc-coll-{TAG}@md3f.test"
PART = f"pc-part-{TAG}@md3f.test"
TOMO = f"pc-tomo-{TAG}@md3f.test"
TWIN = f"pc-twin-{TAG}@md3f.test"
OTHER = f"pc-other-{TAG}@md3f.test"
AGENT = "agent:projects-assistant"
QUESTION = "Archive the zebra project?"
#: The member's zone is UTC, so "today" is the UTC date of NOW.
DAY_START = datetime.combine(NOW.astimezone(UTC).date(), time.min, tzinfo=UTC)
TODAY = DAY_START.date()
FEATURES = ("people", "tasks", "projects", "chat", "email")


@pytest.fixture(autouse=True)
def _one_instant(monkeypatch):
    """The feed's clock and the lens read's "today" read ``NOW``."""
    from gateway.routes.projects import personal

    monkeypatch.setattr(shell, "_now", lambda: NOW)
    monkeypatch.setattr(personal, "datetime", _Frozen)


def _user(email: str, org: str, *, hr: bool = True) -> UserContext:
    grants = {f"feature:{f}" for f in FEATURES}
    if hr:
        grants.add("admin:members:read")
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=EffectiveAccess(role_granted=frozenset(grants)))


def _name(email: str) -> str:
    return f"Name {email.split('@')[0]}"


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811
    """The rows, seeded as the superuser, so the route's reads are the first
    the app role makes."""
    p = promoted
    org = p.org_b
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for email in (ME, COLL, PART, TOMO, TWIN, OTHER):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :n, 'employee', 'active', "
                "CAST(:o AS uuid))"), {"e": email, "n": _name(email), "o": org})
        c.execute(text(
            "INSERT INTO user_settings (user_id, timezone, day_start_hour, "
            "day_end_hour, organization_id) VALUES (:e, 'UTC', 0, 23, "
            "CAST(:o AS uuid))"), {"e": ME, "o": org})
        # TWIN's second People row, in org A, with the same address.
        c.execute(text(
            "INSERT INTO people (id, name, email, status, skills, source, "
            "source_key, organization_id, updated_by, updated_at) VALUES "
            "(gen_random_uuid(), 'Twin of A', :e, 'active', ARRAY[]::text[], "
            "'manual', :k, CAST(:o AS uuid), 'test', now())"),
            {"e": TWIN, "k": f"manual:{TWIN}", "o": p.org_a})

        def absence(email: str, at_org: str, day, kind: str) -> None:
            made = c.execute(text(
                "INSERT INTO people_absences (person_id, starts_on, ends_on, kind, "
                "hours_per_day, created_by, organization_id) SELECT id, :d, :d, :k, :h, "
                ":e, CAST(:o AS uuid) FROM people WHERE lower(email) = :e "
                "AND organization_id = CAST(:o AS uuid)"),
                {"d": day, "k": kind, "e": email, "o": at_org,
                 "h": 4 if kind == "partial" else None})
            assert made.rowcount == 1, (email, at_org)

        absence(COLL, org, TODAY, "away")
        absence(PART, org, TODAY, "partial")
        absence(TOMO, org, TODAY + timedelta(days=1), "holiday")
        absence(ME, org, TODAY, "away")
        absence(TWIN, p.org_a, TODAY, "away")

        project, lane = _project(c, org=org, name=f"People {TAG}", grant_org=True)
        ids["task"] = _task(c, org=org, project=project, lane=lane, title="Ship the extruder",
                            due=None, assignee=ME, number=1)
        for who in (COLL, PART, TOMO, TWIN, AGENT):
            c.execute(text(
                "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by, "
                "organization_id) VALUES (CAST(:t AS uuid), :a, :by, CAST(:o AS uuid))"),
                {"t": ids["task"], "a": who, "by": ME, "o": org})
        start = DAY_START + timedelta(hours=10)
        c.execute(text(
            "INSERT INTO pm_task_personal (task_id, member_email, scheduled_start, "
            "scheduled_end, organization_id) VALUES (CAST(:t AS uuid), :e, :s, :end, "
            "CAST(:o AS uuid))"),
            {"t": ids["task"], "e": ME, "s": start, "end": start + timedelta(hours=1),
             "o": org})

        # ── The chats and their questions ───────────────────────────────────
        ids["t_mine"] = f"pc-mine-{TAG}"
        ids["t_viewer"] = f"pc-viewer-{TAG}"
        ids["t_theirs"] = f"pc-theirs-{TAG}"
        ids["t_a"] = f"pc-a-{TAG}"
        for tid, owner, at_org in ((ids["t_mine"], ME, org), (ids["t_viewer"], OTHER, org),
                                   (ids["t_theirs"], ME, org), (ids["t_a"], ME, p.org_a)):
            c.execute(text(
                "INSERT INTO chat_session (id, user_id, agent_name, title, "
                "organization_id) VALUES (:id, :u, 'orchestrator', 'a chat', :o)"),
                {"id": tid, "u": owner, "o": at_org})

        def ask(tid: str, actor: str, at_org: str, minutes_ago: int) -> str:
            rid = uuid.uuid4().hex
            c.execute(text(
                "INSERT INTO chat_pending_ask (organization_id, request_id, thread_id, "
                "actor_email, agent_name, kind, question, payload, asked_at) VALUES "
                "(CAST(:o AS uuid), :r, :t, :a, 'projects-assistant', 'questions', :q, "
                "CAST(:p AS jsonb), :at)"),
                {"o": at_org, "r": rid, "t": tid, "a": actor, "q": QUESTION,
                 "p": json.dumps({"request_id": rid, "title": QUESTION}),
                 "at": NOW - timedelta(minutes=minutes_ago)})
            return rid

        ids["r_mine"] = ask(ids["t_mine"], ME, org, 30)
        ids["r_viewer"] = ask(ids["t_viewer"], ME, org, 20)
        ids["r_theirs"] = ask(ids["t_theirs"], OTHER, org, 10)
        ids["r_a"] = ask(ids["t_a"], ME, p.org_a, 5)
        # Migration 222 grants this to `acb_app` only. The rehearsal role has
        # a suite-private name, so it gets the same grant here.
        c.execute(text(
            f"GRANT EXECUTE ON FUNCTION public.chat_session_exists(text) TO {_APP_ROLE}"))
    return ids


@pytest.fixture()
def as_app(promoted, monkeypatch):  # noqa: F811
    """``acb_graph`` sessions open as the app role, one fresh backend each,
    and ``CHAT_DURABLE_ASKS`` is on."""
    from acb_common import get_settings

    eng = create_engine(promoted.app_url, poolclass=NullPool, future=True)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    monkeypatch.setattr(get_settings(), "chat_durable_asks", True)
    try:
        yield promoted
    finally:
        eng.dispose()


def _list(monkeypatch, value: str) -> None:
    from acb_common.settings import get_settings

    monkeypatch.setattr(get_settings(), "my_day_providers", value)


async def _feed(p, monkeypatch, user: UserContext, listed: str = "people,chat") -> dict:
    # Room for a cold database. The `sources` check proves each provider ran.
    monkeypatch.setattr(shell, "PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(shell, "TOTAL_BUDGET_S", 90.0)
    monkeypatch.setattr(shell, "MAILBOX_TIMEOUT_S", 30.0)
    monkeypatch.setattr(feed, "FEED_PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(feed, "FEED_BUDGET_S", 90.0)
    monkeypatch.setattr(feed, "FEED_TOTAL_S", 90.0)
    _list(monkeypatch, listed)
    async with _as_member(p, p.org_b):
        return await feed.shell_feed(limit=80, user=user)


@contextlib.asynccontextmanager
async def _as_owner(p, org: str):
    """Bind ``org`` and point the shared engine at the SUPERUSER, which RLS
    does not hold. So only the SQL's own org predicate can filter."""
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(
                p.admin_engine.url.render_as_string(hide_password=False)):
            yield
    finally:
        release_tenant(token)


def _by_kind(answer: dict, kind: str) -> dict[str, dict]:
    return {c["signal_id"]: c for c in answer["cards"]
            if c["kind"] == kind or kind in c["folded"]}


class TestTheKindsOnARealDatabase:
    async def test_each_kind_gives_its_card_and_no_other(
        self, as_app, seeded, monkeypatch,
    ):
        answer = await _feed(as_app, monkeypatch, _user(ME, as_app.org_b))
        assert answer["sources"]["people"] == "ok"
        assert answer["sources"]["chat"] == "ok"
        out = _by_kind(answer, "colleague_out")
        assert set(out) == {f"calendar:{TODAY.isoformat()}"}
        c = out[f"calendar:{TODAY.isoformat()}"]
        # Done-when 5 and 6: not PART (partial), not ME, not TOMO (another
        # day), not TWIN (away only in org A), not the agent.
        assert c["facts"]["out"] == [_name(COLL)]
        assert c["facts"]["task"] == "Ship the extruder"
        if c["kind"] == "colleague_out":
            assert c["why"] == (f"{_name(COLL)} is away today. "
                                "You share Ship the extruder with them.")
        asks = _by_kind(answer, "agent_asks")
        # Done-when 8 and 9, and the tenant: only my question in my room.
        assert set(asks) == {f"chat:{seeded['t_mine']}"}
        a = asks[f"chat:{seeded['t_mine']}"]
        assert (a["title"], a["group"], a["why_key"]) == (
            "projects-assistant asked you a question", "needs_you", "asks")
        assert a["facts"]["request_ids"] == [seeded["r_mine"]]
        assert a["href"] == f"/chat?do=open-chat&fill.session={seeded['t_mine']}"
        # Done-when 11: no question text, anywhere.
        assert "zebra" not in json.dumps(answer, default=str)

    async def test_a_member_without_the_hr_grant_gets_people_absent(
        self, as_app, seeded, monkeypatch,
    ):
        answer = await _feed(as_app, monkeypatch, _user(ME, as_app.org_b, hr=False))
        assert answer["sources"]["people"] == "absent"
        assert not _by_kind(answer, "colleague_out")

    async def test_with_the_list_empty_nothing_runs(self, as_app, seeded, monkeypatch):
        """Done-when 1, on the real database."""
        answer = await _feed(as_app, monkeypatch, _user(ME, as_app.org_b), listed="")
        assert not {"people", "chat"} & set(answer["sources"])
        assert not {c["kind"] for c in answer["cards"]} & {"colleague_out", "agent_asks"}


class TestAwayOn:
    """Done-when 4 and 7: the HR gate, the day it gets, and one tenant."""

    async def test_it_reads_full_absences_of_the_day_it_gets(self, as_app, seeded):
        from gateway.routes.people.absences import away_on

        user = _user(ME, as_app.org_b)
        everyone = [COLL, PART, TOMO, TWIN, ME.upper()]
        async with _as_member(as_app, as_app.org_b):
            today = await away_on(user, everyone, TODAY)
            tomorrow = await away_on(user, everyone, TODAY + timedelta(days=1))
        assert today == {COLL: {"name": _name(COLL), "kind": "away"},
                         ME: {"name": _name(ME), "kind": "away"}}
        assert tomorrow == {TOMO: {"name": _name(TOMO), "kind": "holiday"}}

    async def test_each_org_reads_its_own_rows(self, as_app, seeded):
        from gateway.routes.people.absences import away_on

        async with _as_member(as_app, as_app.org_a):
            in_a = await away_on(_user(ME, as_app.org_a), [TWIN, COLL], TODAY)
        assert in_a == {TWIN: {"name": "Twin of A", "kind": "away"}}

    async def test_the_org_predicate_holds_on_a_session_that_bypasses_rls(
        self, as_app, seeded,
    ):
        """Only the SQL's own org predicate can filter on this session. It is
        defence in depth (R5): production connects as ``acb_app``, which FORCE
        RLS holds."""
        from gateway.routes.people.absences import away_on

        async with _as_owner(as_app, as_app.org_b):
            async with tenant_session() as db:
                both = (await db.execute(text(
                    "SELECT count(DISTINCT p.organization_id) FROM people p "
                    "JOIN people_absences a ON a.person_id = p.id "
                    "WHERE lower(p.email) = :e"), {"e": TWIN})).scalar_one()
                twins = (await db.execute(text(
                    "SELECT count(*) FROM people WHERE lower(email) = :e"),
                    {"e": TWIN})).scalar_one()
            got = await away_on(_user(ME, as_app.org_b), [TWIN, COLL], TODAY)
        assert twins == 2, "the owner session must see both orgs, or this proves nothing"
        assert both == 1  # only org A's TWIN row has an absence
        assert got == {COLL: {"name": _name(COLL), "kind": "away"}}

    async def test_it_raises_403_without_the_grant(self, as_app, seeded):
        from fastapi import HTTPException
        from gateway.routes.people.absences import away_on

        async with _as_member(as_app, as_app.org_b):
            with pytest.raises(HTTPException) as err:
                await away_on(_user(ME, as_app.org_b, hr=False), [COLL], TODAY)
        assert err.value.status_code == 403


def _counting(name: str, real, opened: list[str]):
    @contextlib.asynccontextmanager
    async def _session(*args, **kwargs):
        opened.append(name)
        async with real(*args, **kwargs) as db:
            yield db
    return _session


class TestTheChatOrgPredicate:
    """``pending_ask.waiting_asks`` names the org beside RLS (fix round 1).
    The ``acb_graph`` reads run as the SUPERUSER here, which RLS does not
    hold. So only the SQL's own org predicate keeps org A's question ``r_a``
    out of org B. It is defence in depth (R5): production connects as
    ``acb_app``, which FORCE RLS holds.

    ⚠️ ``rooms._load_room`` reads ``chat_session`` with no org predicate, so
    on this session the room check of ``t_a`` passes. The store's predicate
    is the only thing that holds here."""

    @pytest.fixture()
    def as_owner_graph(self, promoted, monkeypatch):  # noqa: F811
        from acb_common import get_settings

        eng = create_engine(promoted.admin_engine.url, poolclass=NullPool, future=True)
        factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
        monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
        monkeypatch.setattr(get_settings(), "chat_durable_asks", True)
        try:
            yield promoted
        finally:
            eng.dispose()

    async def test_another_orgs_question_never_reaches_the_card(
        self, as_owner_graph, seeded, monkeypatch,
    ):
        p = as_owner_graph
        with acb_graph.tenant_session(p.org_b) as s:
            seen = s.execute(text(
                "SELECT count(*) FROM chat_pending_ask WHERE request_id = :r"),
                {"r": seeded["r_a"]}).scalar_one()
        assert seen == 1, "the owner session must see org A's row, or this proves nothing"
        async with _as_owner(p, p.org_b):
            found = await feed._chat(_user(ME, p.org_b))
        ids = [s.signal_id for s in found.signals()]
        assert f"chat:{seeded['t_a']}" not in ids
        assert ids == [f"chat:{seeded['t_mine']}"]


class TestTheSessions:
    """Done-when 13: a counting wrapper of each seam."""

    async def test_people_opens_three_sessions_at_most(self, as_app, seeded, monkeypatch):
        from gateway.routes.people import absences
        from gateway.routes.projects import personal
        from gateway.routes.tasks import settings

        opened: list[str] = []
        monkeypatch.setattr(personal, "_tenant_session",
                            _counting("personal", personal._tenant_session, opened))
        monkeypatch.setattr(settings, "_tenant_session",
                            _counting("settings", settings._tenant_session, opened))
        monkeypatch.setattr(absences, "_tenant_session",
                            _counting("absences", absences._tenant_session, opened))
        async with _as_member(as_app, as_app.org_b):
            found = await feed._people(_user(ME, as_app.org_b))
        assert opened == ["settings", "personal", "absences"]
        assert [s.kind for s in found.signals()] == ["colleague_out"]

    async def test_chat_opens_one_plus_one_for_each_thread(self, as_app, seeded, monkeypatch):
        opened: list[str] = []
        real = acb_graph.tenant_session

        @contextlib.contextmanager
        def _graph(*args, **kwargs):
            opened.append("graph")
            with real(*args, **kwargs) as s:
                yield s

        monkeypatch.setattr(acb_graph, "tenant_session", _graph)
        async with _as_member(as_app, as_app.org_b):
            found = await feed._chat(_user(ME, as_app.org_b))
        # The list, then the room of each of my two threads with a question.
        assert len(opened) == 3
        assert [s.signal_id for s in found.signals()] == [f"chat:{seeded['t_mine']}"]

    async def test_with_durable_asks_off_chat_reads_nothing(self, as_app, seeded, monkeypatch):
        from acb_common import get_settings

        opened: list[str] = []
        real = acb_graph.tenant_session

        @contextlib.contextmanager
        def _graph(*args, **kwargs):
            opened.append("graph")
            with real(*args, **kwargs) as s:
                yield s

        monkeypatch.setattr(acb_graph, "tenant_session", _graph)
        monkeypatch.setattr(get_settings(), "chat_durable_asks", False)
        answer = await _feed(as_app, monkeypatch, _user(ME, as_app.org_b), listed="chat")
        assert answer["sources"]["chat"] == "absent"
        assert opened == []
