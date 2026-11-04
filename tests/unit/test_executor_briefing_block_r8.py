"""R8: the briefing at the start of a chat on a real database (WS-63 PZ-34).

Spec: ``project-docs/specs/personalization.md`` §15.4 and §15.9, the PZ-34
row. The unit half is ``tests/unit/test_executor_briefing_block.py``.

Each test runs the executor's own prime (``_prime_member_briefing``) in a run
frame as ``run_agent_stream`` binds it: the run's org in ``_RUN_ORG`` and the
bound tenant, the verified member in the run context, a solo room, and the
artifact context of the run. The reads are the real ones, as the NON-privileged
role of the phase-4 rehearsal (no superuser, no BYPASSRLS):
``assistant_summaries.recent``, ``personal.stored_zone``,
``acb_auth.deps.member_context`` (with the identity cutover on) and
``personal.my_due_tasks``. Only Redis is a fake, which records each key.

What this proves:

* item 7: the tasks come from ``my_due_tasks``, the oldest deadline first.
  A private task of another member, a task of another member, a task due
  later, a task in a project that the member cannot see and a task of
  another org never show. The chats are the member's own: a chat of
  another member, of another org, an archived chat, a chat with ``channel``
  set, a chat with another agent, a chat that is no longer solo, a chat that
  reads ``off`` and the current chat never show. At most 3 chats show, the
  newest first;
* item 8: the member's switch ``apps.chat.summaries`` off hides the chats
  and keeps the tasks. A member without ``feature:projects`` gets no task
  part;
* item 11: ``recent`` reads nothing with the flag of PZ-33 off. With no
  summary, the block holds the tasks only;
* item 12: the key is ``cc:<org>:chat-briefing:<session>:<member hash>``
  with a TTL of 24 hours. A stored block outlives a Delete in the same chat
  (D-PZ-38), and a new chat reads the store again;
* the trap of ``member_context``: it binds the member's org, and the run's
  bound tenant does not move;
* item 13: the stored text reaches the block as it is.

Mutations, each red here: drop ``ELIGIBLE_SQL`` from ``RECENT_SQL`` (the
archived, channel and agent chats show); drop ``_MINE`` (another member's
chat shows); drop the ``exclude`` bind (the current chat shows); drop the
switch read of ``recent``; drop the ``feature:projects`` check; read the
tasks with ``my_inbox`` in place of ``my_due_tasks`` (a later task shows).

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from acb_common.db import bind_tenant, current_tenant, release_tenant, tenant_session
from acb_common.settings import install_settings
from orchestrator import executor as ex
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_executor_briefing_block import FakeRedis
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ME = f"brief-me-{TAG}@pz34.test"
OTHER = f"brief-other-{TAG}@pz34.test"
NOPROJ = f"brief-noproj-{TAG}@pz34.test"
NOW = datetime.now(UTC)

#: The chat of the turn. It has a summary, and it must never show.
CURRENT = f"pz34-current-{TAG}"


def _summary(text_: str, follow_ups: tuple[str, ...] = ()) -> str:
    return json.dumps({"v": 1, "text": text_, "follow_ups": list(follow_ups),
                       "through": "m1", "through_ms": 1, "at": "2026-10-30T10:00:00Z"})


def _chat(c, *, org: str, sid: str, member: str, title: str, at: datetime,
          summary: str | None, agent: str = "orchestrator", channel: str | None = None,
          archived: bool = False, others: tuple[str, ...] = ()) -> None:
    c.execute(text(
        "INSERT INTO chat_session (id, user_id, agent_name, title, last_preview, "
        "message_count, created_at, updated_at, visibility, organization_id, channel, "
        "archived_at, member_summary) VALUES (:id, :u, :a, :t, 'Done.', 4, :at, :at, "
        "'private', CAST(:o AS uuid), :ch, :arc, CAST(:sm AS jsonb))"),
        {"id": sid, "u": member, "a": agent, "t": title, "at": at, "o": org,
         "ch": channel, "arc": at if archived else None, "sm": summary})
    for i, subject in enumerate((member, *others)):
        c.execute(text(
            "INSERT INTO chat_session_participant (session_id, subject, role, "
            "organization_id) VALUES (:s, :subject, :r, CAST(:o AS uuid))"),
            {"s": sid, "subject": subject, "r": "owner" if i == 0 else "member", "o": org})
    c.execute(text(
        "INSERT INTO chat_session_agent (session_id, agent_name, role, organization_id) "
        "VALUES (:s, :a, 'primary', CAST(:o AS uuid))"), {"s": sid, "a": agent, "o": org})


def _project(c, *, org: str, name: str, owner: str | None = None,
             grant: str | None = None) -> tuple[str, str]:
    project, lane = str(uuid.uuid4()), str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
        "owns_statuses, personal_owner) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), "
        ":n, 'manual', :by, true, :own)"),
        {"p": project, "o": org, "n": name, "by": owner or OTHER, "own": owner})
    c.execute(text(
        "INSERT INTO pm_task_statuses (id, project_id, name, position, category, "
        "is_default) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), 'To do', 1, "
        "'todo', true)"), {"s": lane, "p": project})
    if grant:
        c.execute(text(
            "INSERT INTO pm_project_grants (project_id, subject, created_by, "
            "organization_id) VALUES (CAST(:p AS uuid), :g, :by, CAST(:o AS uuid))"),
            {"p": project, "g": grant, "by": OTHER, "o": org})
    return project, lane


_NUMBER = iter(range(1, 10_000))


def _task(c, *, org: str, project: str, lane: str, title: str, due: datetime,
          assignee: str | None = None, waiting: str | None = None) -> str:
    task = str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
        "status_id, title, source, created_by, task_number, due_at) VALUES "
        "(CAST(:t AS uuid), CAST(:o AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), "
        "CAST(:s AS uuid), :ti, 'manual', :by, :n, :due)"),
        {"t": task, "o": org, "p": project, "s": lane, "ti": title, "by": OTHER,
         "n": next(_NUMBER), "due": due})
    if assignee:
        c.execute(text(
            "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by, "
            "organization_id) VALUES (CAST(:t AS uuid), :a, :by, CAST(:o AS uuid))"),
            {"t": task, "a": assignee, "by": OTHER, "o": org})
    if waiting:
        c.execute(text(
            "INSERT INTO pm_task_personal (task_id, member_email, disposition, "
            "organization_id) VALUES (CAST(:t AS uuid), :e, 'WAITING', CAST(:o AS uuid))"),
            {"t": task, "e": waiting, "o": org})
    return task


def _member(c, *, org: str, email: str, role: str | None) -> None:
    """A member with the identity rows that ``member_context`` reads with
    the cutover on, and the role that grants ``feature:projects``."""
    user = c.execute(text(
        "INSERT INTO app_user (email, display_name, role, status, organization_id) "
        "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) RETURNING id"),
        {"e": email, "o": org}).scalar_one()
    identity = c.execute(text(
        "INSERT INTO user_identity (email, display_name) VALUES (:e, :e) "
        "RETURNING id::text"), {"e": email}).scalar_one()
    c.execute(text(
        "INSERT INTO org_membership (organization_id, user_id, status, joined_at) "
        "VALUES (CAST(:o AS uuid), CAST(:u AS uuid), 'active', now())"),
        {"o": org, "u": identity})
    if role:
        c.execute(text(
            "INSERT INTO user_role (user_id, role_id, organization_id) "
            "VALUES (:u, CAST(:r AS uuid), CAST(:o AS uuid))"),
            {"u": user, "r": role, "o": org})


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    """Three members of org A, their chats and their tasks, seeded as the
    superuser, so the reads of the run are the first that the app role
    makes."""
    p = promoted
    org, elsewhere = p.org_a, p.org_b
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        role = str(c.execute(text(
            "INSERT INTO org_role (organization_id, slug, display_name, is_system, rank) "
            "VALUES (CAST(:o AS uuid), :s, 'Projects', false, 50) RETURNING id"),
            {"o": org, "s": f"pz34-{TAG}"}).scalar_one())
        c.execute(text(
            "INSERT INTO org_role_permission (role_id, permission, organization_id) "
            "VALUES (CAST(:r AS uuid), 'feature:projects', CAST(:o AS uuid))"),
            {"r": role, "o": org})
        _member(c, org=org, email=ME, role=role)
        _member(c, org=org, email=OTHER, role=role)
        _member(c, org=org, email=NOPROJ, role=None)

        def chat(name: str, **kw: Any) -> str:
            sid = f"pz34-{name}-{TAG}"
            ids[name] = sid
            _chat(c, sid=sid, **kw)
            return sid

        hour = timedelta(hours=1)
        mine = {"org": org, "member": ME}
        assert chat("current", **mine, title="This chat", at=NOW,
                    summary=_summary("Now.")) == CURRENT
        chat("a", **mine, title="Q3 pricing", at=NOW - 1 * hour,
             summary=_summary("Planned the Q3 deck with three tiers.",
                              ("Send the deck to Priya",)))
        chat("b", **mine, title="Hiring plan", at=NOW - 2 * hour,
             summary=_summary("Listed two open roles."))
        chat("c", **mine, title="Van booking", at=NOW - 3 * hour,
             summary=_summary("Booked the van for Tuesday."))
        chat("d", **mine, title="Fourth chat", at=NOW - 4 * hour,
             summary=_summary("The oldest of four."))
        # Each one below is NEWER than the four above, so a read that kept
        # one would put it first.
        minute = timedelta(minutes=1)
        chat("archived", **mine, title="Archived", at=NOW - 5 * minute,
             summary=_summary("Archived text."), archived=True)
        chat("whatsapp", **mine, title="WhatsApp", at=NOW - 6 * minute,
             summary=_summary("Channel text."), channel="whatsapp")
        chat("agent", **mine, title="Email chat", at=NOW - 7 * minute,
             summary=_summary("Agent text."), agent="email-assistant")
        chat("shared", **mine, title="Shared", at=NOW - 8 * minute,
             summary=_summary("Shared text."), others=(OTHER,))
        chat("off", **mine, title="Deleted", at=NOW - 9 * minute,
             summary=json.dumps({"v": 1, "off": True}))
        chat("bare", **mine, title="No summary", at=NOW - 10 * minute, summary=None)
        chat("theirs", org=org, member=OTHER, title="Their chat", at=NOW - 11 * minute,
             summary=_summary("Their text."))
        chat("elsewhere", org=elsewhere, member=ME, title="Other org", at=NOW - 12 * minute,
             summary=_summary("Other org text."))
        chat("noproj", org=org, member=NOPROJ, title="Without projects",
             at=NOW - 1 * hour, summary=_summary("A chat of a member with no Projects."))

        shared, lane = _project(c, org=org, name=f"Launch {TAG}", grant="org")
        private, plane = _project(c, org=org, name=f"Theirs {TAG}", owner=OTHER,
                                  grant=OTHER)
        hidden, hlane = _project(c, org=org, name=f"Hidden {TAG}", grant=OTHER)
        far, flane = _project(c, org=elsewhere, name=f"Elsewhere {TAG}", grant="org")
        day = timedelta(days=1)
        _task(c, org=org, project=shared, lane=lane, title="Ship the extruder",
              due=NOW - 2 * day, assignee=ME)
        _task(c, org=org, project=shared, lane=lane, title="Chase the supplier",
              due=NOW - 3 * day, waiting=ME)
        _task(c, org=org, project=shared, lane=lane, title="Plan the launch",
              due=NOW + 5 * day, assignee=ME)
        _task(c, org=org, project=private, lane=plane, title="Their private task",
              due=NOW - 1 * day)
        _task(c, org=org, project=shared, lane=lane, title="Their assigned task",
              due=NOW - 1 * day, assignee=OTHER)
        _task(c, org=org, project=hidden, lane=hlane, title="Hidden waiting task",
              due=NOW - 4 * day, assignee=OTHER, waiting=ME)
        _task(c, org=elsewhere, project=far, lane=flane, title="Other org task",
              due=NOW - 1 * day, assignee=ME)
        _task(c, org=org, project=shared, lane=lane, title="No projects task",
              due=NOW - 1 * day, assignee=NOPROJ)
    p.ids = ids
    return p


@pytest.fixture
def env(world, monkeypatch):
    """The flags on for org A, the identity cutover on, a fresh access cache,
    a fake Redis, and a budget that a cold Windows engine cannot miss. The
    budget itself is a fence of the unit half."""
    from acb_auth import access as access_module
    from acb_auth.access import invalidate

    original = get_settings()

    def flags(*, summaries: str, briefing: str) -> None:
        install_settings(original.model_copy(update={
            "assistant_profile_enabled": True, "assistant_profile_orgs": "",
            "assistant_chat_summary_orgs": summaries,
            "assistant_briefing_orgs": briefing}))

    flags(summaries=world.org_a, briefing=world.org_a)
    world.flags = flags
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setattr(access_module, "_tables_missing", False)
    invalidate()
    world.redis = FakeRedis()
    monkeypatch.setattr(ex, "_briefing_redis", lambda: world.redis)
    monkeypatch.setattr(ex, "_BRIEFING_TIMEOUT_S", 60.0)
    try:
        yield world
    finally:
        install_settings(original)
        invalidate()


def _app_dsn(ns) -> str:
    return ns.app_url.render_as_string(hide_password=False)


@contextlib.contextmanager
def _run_frame(org: str, member: str, session: str, run_id: str):
    """The binding of a streamed run of the orchestrator in the member's own
    web chat, as ``run_agent_stream`` makes it."""
    from acb_common import bind_run_context, run_context_scope
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    with run_context_scope(), artifact_context_scope():
        bind_run_context(run_id=run_id, thread_id=session, agent="orchestrator",
                         user=member, source="chat", member_verified=True)
        bind_artifact_context(session_id=session, run_id=run_id, member=member,
                              batch_thread=False)
        thread = ex._stream_relay_thread_id.set(session)
        ex._RUN_ORG[session] = org
        solo = ex._RUN_SOLO_ROOM.set(True)
        try:
            yield
        finally:
            ex._RUN_SOLO_ROOM.reset(solo)
            ex._RUN_ORG.pop(session, None)
            ex._stream_relay_thread_id.reset(thread)


async def _turn(ns, *, member: str = ME, session: str = CURRENT,
                run_id: str | None = None) -> str:
    """One turn: the prime and then the block, as the app role, with the
    run's tenant bound. The run's tenant must not move."""
    async with tenant_engine_scope(_app_dsn(ns)):
        token = bind_tenant(ns.org_a)
        try:
            with _run_frame(ns.org_a, member, session, run_id or uuid.uuid4().hex):
                assert ex._current_run_org() == ns.org_a
                await ex._prime_member_briefing(
                    "orchestrator", organization_id=ns.org_a, thread_id=session,
                    delegated=False, shows_cards=True, batch_thread=False)
                assert current_tenant() == ns.org_a, "member_context moved the tenant"
                return ex._member_briefing_block("orchestrator")
        finally:
            release_tenant(token)


def _section(block: str, head: str) -> list[str]:
    """The item lines under the heading *head*, in order."""
    lines = block.split("\n")
    if head not in lines:
        return []
    out = []
    for line in lines[lines.index(head) + 1:]:
        if not line.startswith("- "):
            break
        out.append(line)
    return out


def _chat_titles(block: str) -> list[str]:
    from acb_common import assistant_profile as ap

    return [ln.split('"')[1] for ln in _section(block, ap.BRIEFING_CHATS_HEADER)]


def _task_titles(block: str) -> list[str]:
    from acb_common import assistant_profile as ap

    return [ln[2:].rsplit(" (due ", 1)[0] for ln in _section(block, ap.BRIEFING_TASKS_HEADER)]


def _key(org: str, session: str, member: str) -> str:
    part = hashlib.sha256(member.encode("utf-8")).hexdigest()[:24]
    return f"cc:{org}:chat-briefing:{session}:{part}"


# ── Item 7: only the member's own data ──────────────────────────────────────


async def test_7_the_tasks_come_from_my_due_tasks(env) -> None:
    from acb_auth.deps import member_context
    from gateway.routes.projects.personal import my_due_tasks

    block = await _turn(env)
    assert _task_titles(block) == ["Chase the supplier", "Ship the extruder"]
    async with tenant_engine_scope(_app_dsn(env)):
        token = bind_tenant(env.org_a)
        try:
            user = await member_context(ME)
            answer = await my_due_tasks(user, limit=3)
        finally:
            release_tenant(token)
    assert [r["title"] for r in answer["rows"]] == _task_titles(block)
    for never in ("Plan the launch", "Their private task", "Their assigned task",
                  "Hidden waiting task", "Other org task", "No projects task"):
        assert never not in block, never


async def test_7_only_the_members_own_chats_show(env) -> None:
    from gateway import assistant_summaries

    block = await _turn(env)
    assert _chat_titles(block) == ["Q3 pricing", "Hiring plan", "Van booking"]
    for never in ("This chat", "Fourth chat", "Archived", "WhatsApp", "Email chat",
                  "Shared", "Deleted", "No summary", "Their chat", "Other org"):
        assert f'"{never}"' not in block, never
    async with tenant_engine_scope(_app_dsn(env)), tenant_session(env.org_a) as db:
        found = await assistant_summaries.recent(db, ME, exclude=CURRENT)
        everything = await assistant_summaries.recent(db, ME, exclude="")
    assert [r.session_id for r in found] == [env.ids["a"], env.ids["b"], env.ids["c"]]
    # Without the exclude, the current chat is the newest one.
    assert everything[0].session_id == CURRENT


async def test_13_the_stored_text_reaches_the_block_as_it_is(env) -> None:
    block = await _turn(env, session=f"pz34-text-{TAG}")
    assert ('"Q3 pricing": Planned the Q3 deck with three tiers. '
            "Follow-ups: Send the deck to Priya.") in block


# ── Item 8: the switch and the feature ──────────────────────────────────────


async def test_8_the_switch_off_hides_the_chats_and_keeps_the_tasks(env) -> None:
    with env.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO user_settings (organization_id, user_id, assistant_profile) "
            "VALUES (CAST(:o AS uuid), :u, CAST(:p AS jsonb))"),
            {"o": env.org_a, "u": ME,
             "p": json.dumps({"v": 1, "apps": {"chat": {"summaries": False}}})})
    try:
        block = await _turn(env, session=f"pz34-switch-{TAG}")
    finally:
        with env.admin_engine.begin() as c:
            c.execute(text("DELETE FROM user_settings WHERE user_id = :u"), {"u": ME})
    assert _chat_titles(block) == []
    assert _task_titles(block) == ["Chase the supplier", "Ship the extruder"]


async def test_8_a_member_without_projects_gets_no_task_part(env) -> None:
    from acb_common import assistant_profile as ap

    block = await _turn(env, member=NOPROJ, session=f"pz34-noproj-turn-{TAG}")
    assert _chat_titles(block) == ["Without projects"]
    assert ap.BRIEFING_TASKS_HEADER not in block
    assert "No projects task" not in block


# ── Item 11: the flag of PZ-33 ──────────────────────────────────────────────


async def test_11_recent_reads_nothing_with_the_summary_flag_off(env) -> None:
    from acb_common import assistant_profile as ap
    from gateway import assistant_summaries

    env.flags(summaries="", briefing=env.org_a)
    async with tenant_engine_scope(_app_dsn(env)), tenant_session(env.org_a) as db:
        assert await assistant_summaries.recent(db, ME, exclude=CURRENT) == []
    # Production today: no summary reaches the block, and the tasks still do.
    block = await _turn(env, session=f"pz34-noflag-{TAG}")
    assert ap.BRIEFING_CHATS_HEADER not in block
    assert _task_titles(block) == ["Chase the supplier", "Ship the extruder"]


async def test_11_the_briefing_flag_off_reads_nothing(env) -> None:
    env.flags(summaries=env.org_a, briefing="")
    assert await _turn(env, session=f"pz34-off-{TAG}") == ""
    assert env.redis.gets == [] and env.redis.sets == []


# ── Item 12: the store ──────────────────────────────────────────────────────


async def test_12_the_key_is_tenant_scoped_and_lives_24_hours(env) -> None:
    session = f"pz34-key-{TAG}"
    block = await _turn(env, session=session)
    ((key, ttl, value),) = env.redis.sets
    assert key == _key(env.org_a, session, ME)
    assert ME not in key
    assert ttl == 24 * 3600
    assert json.loads(value) == {"v": 1, "text": block}


async def test_12_a_stored_block_outlives_a_delete_in_the_same_chat(env) -> None:
    """Known and documented (D-PZ-38): a Delete clears the summary, and a
    block already stored keeps it in that one chat for up to 24 hours. A new
    chat reads the store again, and the deleted summary is gone there."""
    from gateway import assistant_summaries

    first = await _turn(env, session=CURRENT, run_id="run-1")
    assert _chat_titles(first) == ["Q3 pricing", "Hiring plan", "Van booking"]
    assert "Booked the van for Tuesday." in first
    try:
        async with tenant_engine_scope(_app_dsn(env)), tenant_session(env.org_a) as db:
            assert await assistant_summaries.clear_one_for_member(db, env.ids["c"], ME)
        second = await _turn(env, session=CURRENT, run_id="run-2")
        assert second == first, "the stored block of this chat keeps its bytes"
        # Another chat of the member: its first turn reads the store again.
        # Its own chat stays out, and the deleted summary is gone.
        fresh = await _turn(env, session=env.ids["a"], run_id="run-3")
        assert "Booked the van for Tuesday." not in fresh
        assert _chat_titles(fresh) == ["This chat", "Hiring plan", "Fourth chat"]
    finally:
        # The world is shared by the module: give the chat its summary back.
        with env.admin_engine.begin() as c:
            c.execute(text(
                "UPDATE chat_session SET member_summary = CAST(:sm AS jsonb) "
                "WHERE id = :s"),
                {"s": env.ids["c"], "sm": _summary("Booked the van for Tuesday.")})


async def test_12_a_stored_block_outlives_an_access_change(env) -> None:
    """D-PZ-38 item 3 (the owner, 2026-11-04), with a real removal: the role
    of the member loses ``feature:projects``. The stored block of this chat
    keeps the task titles and its bytes. A new chat reads fresh data, so it
    shows no task part."""
    from acb_auth.access import invalidate
    from acb_common import assistant_profile as ap

    first = await _turn(env, session=CURRENT, run_id="run-1")
    assert _task_titles(first) == ["Chase the supplier", "Ship the extruder"]
    role = {"s": f"pz34-{TAG}", "o": env.org_a}
    try:
        with env.admin_engine.begin() as c:
            gone = c.execute(text(
                "DELETE FROM org_role_permission WHERE permission = 'feature:projects' "
                "AND role_id IN (SELECT id FROM org_role WHERE slug = :s "
                "AND organization_id = CAST(:o AS uuid))"), role).rowcount
        assert gone == 1
        invalidate()
        second = await _turn(env, session=CURRENT, run_id="run-2")
        assert second == first, "the stored block of this chat keeps its bytes"
        assert len(env.redis.sets) == 1, "turn 2 built nothing"
        fresh = await _turn(env, session=env.ids["a"], run_id="run-3")
        assert ap.BRIEFING_TASKS_HEADER not in fresh
        assert _task_titles(fresh) == []
    finally:
        with env.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO org_role_permission (role_id, permission, organization_id) "
                "SELECT id, 'feature:projects', organization_id FROM org_role "
                "WHERE slug = :s AND organization_id = CAST(:o AS uuid)"), role)
        invalidate()
