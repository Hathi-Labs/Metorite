"""WS-27v — watchers, and mentions that behave.

Spec: `ai-company-brain/specs/project_management_app.md` §9.1 (WS-27v), from
`plane_pm_research_2026-08.md` §3.2 (P-2, P-20).

Four claims, each of which is the ticket:

* **auto-subscribe is idempotent, from every trigger** — commenting, editing,
  being assigned, being @mentioned. One helper (`ensure_watchers`), one UNIQUE
  pair, so the fourth comment does not stack a fourth row;
* **the audience is watchers ∪ assignees, minus the actor, gated by the
  RECIPIENT'S visibility** — Plane's membership-only check is the
  counterexample: a watcher row is an intent to hear, never a right to see,
  and `resolve_visibility_for` stays the gate;
* **mention diffing** — editing a comment or description notifies only the
  NEWLY added mentions, proven by editing one comment twice;
* **watch/unwatch answer R5** — an invisible task is 404, never 403.

Run against the shared `_projects_fakes.FakeProjectsDB` so the real route
functions execute end to end: the fake mirrors the audience UNION and the
visibility probe off the statement text, so a route that loses either arm
loses it here too. Hermetic: no Postgres, no TestClient.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from gateway.routes.projects import activities as pm_activities
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import notifications as pm_notify
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import watchers as pm_watchers
from gateway.routes.projects.notifications import new_mentions
from gateway.routes.projects.watchers import watchable

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    member_user,
    projects_user,
    silence_events,
)

MODULES = (pm_core, pm_tasks, pm_activities, pm_notify, pm_watchers)
USER = projects_user()
MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"
PACKAGE = Path(pm_watchers.__file__).parent


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    silence_events(monkeypatch, MODULES)
    return fake


def _task(db: FakeProjectsDB, *, subject: str | None = "org"):
    # In a space: a space holds no tasks (`core.assert_holds_tasks`).
    project = db.seed_in_space(name="Ops", subject=subject)
    todo = db.seed_status(project.id, name="To do", category="todo")
    task = db.seed_task(project.id, todo.id)
    return project, todo, task


def _watchers(db: FakeProjectsDB, task_id: str) -> list[str]:
    return sorted(
        r["watcher"] for r in db.rows("pm_task_watchers")
        if str(r["task_id"]) == str(task_id)
    )


def _notified(db: FakeProjectsDB, kind: str | None = None) -> list[str]:
    return [
        str(r["recipient"]) for r in db.rows("pm_notifications")
        if kind is None or r["kind"] == kind
    ]


# ── The pure half ───────────────────────────────────────────────────────────

def test_watchable_folds_dedupes_and_drops_agents_and_blanks():
    """R10 on the way in, and WS-27j's rule 2: an agent subscription would feed
    an inbox nobody ever opens."""
    assert watchable(
        ["Priya@Fracktal.IN", "priya@fracktal.in", "agent:builder", " ", None,
         "not-an-address"],
    ) == ["priya@fracktal.in"]


def test_new_mentions_is_the_set_difference_in_first_seen_order():
    assert new_mentions(
        "ping @a@x.co", "ping @a@x.co then @c@x.co and @b@x.co",
    ) == ["c@x.co", "b@x.co"]


def test_new_mentions_of_a_first_body_is_every_mention():
    assert new_mentions(None, "hey @a@x.co") == ["a@x.co"]


def test_a_removed_then_retyped_mention_is_not_new():
    """Case-insensitively (R10): re-spelling `@A@X.CO` is the same person."""
    assert new_mentions("hi @a@x.co", "hi @A@X.CO") == []


# ── Auto-subscribe: all four triggers, each idempotent ──────────────────────

async def test_commenting_subscribes_the_commenter_once(db: FakeProjectsDB):
    _, _, task = _task(db)
    for _ in range(2):
        await pm_activities.add_comment(
            str(task.id), pm_activities.CommentIn(body="on it"), user=USER,
        )
    assert _watchers(db, task.id) == ["owner@fracktal.in"]


async def test_editing_subscribes_the_editor_once(db: FakeProjectsDB):
    _, _, task = _task(db)
    for title in ("First", "Second"):
        await pm_tasks.patch_task(
            str(task.id), pm_tasks.TaskIn(title=title), user=USER,
        )
    assert _watchers(db, task.id) == ["owner@fracktal.in"]


async def test_a_noop_patch_is_not_a_touch(db: FakeProjectsDB):
    _, _, task = _task(db)
    await pm_tasks.patch_task(str(task.id), pm_tasks.TaskIn(), user=USER)
    assert _watchers(db, task.id) == []


async def test_an_added_assignee_is_subscribed_once_and_agents_never(
    db: FakeProjectsDB,
):
    _, _, task = _task(db)
    for _ in range(2):
        await pm_tasks.set_assignees(
            str(task.id),
            pm_tasks.AssigneesIn(
                assignees=["priya@fracktal.in", "agent:builder"],
            ),
            user=USER,
        )
    assert _watchers(db, task.id) == ["priya@fracktal.in"]


async def test_a_delivered_mention_subscribes_its_target_once(
    db: FakeProjectsDB,
):
    _, _, task = _task(db)
    for _ in range(2):
        await pm_activities.add_comment(
            str(task.id),
            pm_activities.CommentIn(body="see @priya@fracktal.in"),
            user=USER,
        )
    assert "priya@fracktal.in" in _watchers(db, task.id)
    assert _watchers(db, task.id).count("priya@fracktal.in") == 1


async def test_an_undeliverable_mention_subscribes_nobody(db: FakeProjectsDB):
    """A person who cannot open the task must not be enrolled in a stream of
    its titles — the subscription follows the delivery, and the delivery is
    gated by the recipient's own visibility."""
    _, _, task = _task(db, subject="owner@fracktal.in")
    await pm_activities.add_comment(
        str(task.id),
        pm_activities.CommentIn(body="see @outsider@fracktal.in"),
        user=USER,
    )
    assert "outsider@fracktal.in" not in _watchers(db, task.id)
    assert _notified(db, "mention") == []


async def test_the_creator_watches_their_own_task(db: FakeProjectsDB):
    """What keeps WS-27j's author-hears-about-comments once the audience is
    watchers ∪ assignees; migration 165 seeds the same for existing tasks."""
    project, _, _ = _task(db)
    created = await pm_tasks.create_task(
        pm_tasks.TaskIn(project_id=str(project.id), title="Mine"), user=USER,
    )
    assert _watchers(db, created["id"]) == ["owner@fracktal.in"]


# ── Watch / unwatch ─────────────────────────────────────────────────────────

async def test_watch_is_idempotent_and_folded(db: FakeProjectsDB):
    _, _, task = _task(db)
    caller = projects_user(email="Owner@Fracktal.IN")
    for _ in range(2):
        result = await pm_watchers.watch_task(str(task.id), user=caller)
    assert result == {"task_id": str(task.id), "watching": True}
    # R10: stored folded, or the unwatch and the fan-out would both miss it.
    assert _watchers(db, task.id) == ["owner@fracktal.in"]


async def test_unwatch_removes_only_the_caller(db: FakeProjectsDB):
    _, _, task = _task(db)
    db.seed("pm_task_watchers", task_id=task.id, watcher="other@fracktal.in")
    await pm_watchers.watch_task(str(task.id), user=USER)
    result = await pm_watchers.unwatch_task(str(task.id), user=USER)
    assert result == {"task_id": str(task.id), "watching": False}
    assert _watchers(db, task.id) == ["other@fracktal.in"]


async def test_the_watchers_read_names_the_callers_own_state(
    db: FakeProjectsDB,
):
    _, _, task = _task(db)
    db.seed("pm_task_watchers", task_id=task.id, watcher="owner@fracktal.in")
    result = await pm_watchers.list_watchers(str(task.id), user=USER)
    assert result == {"watchers": ["owner@fracktal.in"], "watching": True}


@pytest.mark.parametrize(
    "route", [pm_watchers.watch_task, pm_watchers.unwatch_task,
              pm_watchers.list_watchers],
)
async def test_an_invisible_task_is_404_never_403(db: FakeProjectsDB, route):
    """R5. "not yours" and "no such task" must be one answer, or the endpoint
    becomes an oracle for which ids exist in another department."""
    _, _, task = _task(db, subject=None)
    with pytest.raises(HTTPException) as exc:
        await route(str(task.id), user=member_user("nobody@fracktal.in"))
    assert exc.value.status_code == 404


def test_the_identity_comes_from_the_session_not_a_parameter():
    """R3 — a `watcher` parameter would let anyone subscribe anyone else to a
    stream of a task's titles."""
    import inspect

    for route in (pm_watchers.watch_task, pm_watchers.unwatch_task):
        assert "watcher" not in set(inspect.signature(route).parameters)


# ── The audience: watchers ∪ assignees, minus the actor, gated per person ───

async def test_a_comment_reaches_watchers_and_assignees_but_never_the_actor(
    db: FakeProjectsDB,
):
    _, _, task = _task(db)
    db.seed("pm_task_watchers", task_id=task.id, watcher="watcher@fracktal.in")
    db.seed(
        "pm_task_assignees", task_id=task.id, assignee="holder@fracktal.in",
        assigned_by="owner@fracktal.in",
    )
    await pm_activities.add_comment(
        str(task.id), pm_activities.CommentIn(body="no names here"), user=USER,
    )
    # The commenter auto-subscribed above and is STILL not notified — the
    # actor-exclusion rule re-asserted over the new audience.
    assert sorted(_notified(db, "comment")) == [
        "holder@fracktal.in", "watcher@fracktal.in",
    ]


async def test_a_watcher_who_lost_visibility_is_not_notified(
    db: FakeProjectsDB,
):
    """The deliberate divergence from Plane: the watcher row survives, the
    delivery does not. `resolve_visibility_for` is the gate, per recipient."""
    _, _, task = _task(db, subject="insider@fracktal.in")
    db.seed("pm_task_watchers", task_id=task.id, watcher="insider@fracktal.in")
    db.seed("pm_task_watchers", task_id=task.id, watcher="gone@fracktal.in")
    await pm_activities.add_comment(
        str(task.id), pm_activities.CommentIn(body="quarterly update"),
        user=USER,
    )
    assert _notified(db, "comment") == ["insider@fracktal.in"]
    # The row is intact: visibility is checked at delivery, never persisted.
    assert "gone@fracktal.in" in _watchers(db, task.id)


async def test_a_mentioned_watcher_gets_the_mention_not_the_comment_too(
    db: FakeProjectsDB,
):
    _, _, task = _task(db)
    db.seed("pm_task_watchers", task_id=task.id, watcher="priya@fracktal.in")
    await pm_activities.add_comment(
        str(task.id),
        pm_activities.CommentIn(body="over to @priya@fracktal.in"),
        user=USER,
    )
    mine = [
        r["kind"] for r in db.rows("pm_notifications")
        if r["recipient"] == "priya@fracktal.in"
    ]
    assert mine == ["mention"]


def test_the_audience_is_the_one_helper_not_a_second_query():
    """`task_audience` is the single place "who hears" is derived; a second
    UNION grown beside it is how the arms start disagreeing."""
    source = Path(pm_notify.__file__).read_text(encoding="utf-8")
    body = source.split("async def task_audience", 1)[1]
    body = body.split("\nasync def ", 1)[0].split("\n# ──", 1)[0]
    assert "pm_task_watchers" in body
    assert "pm_task_assignees" in body


# ── Mention diffing: the edit-twice proof ───────────────────────────────────

async def test_editing_a_comment_twice_notifies_only_the_new_mention(
    db: FakeProjectsDB,
):
    """THE ticket's test. First edit adds @b — b is notified. Second edit keeps
    @b and adds @c — ONLY c is notified, or every typo fix re-pings the whole
    thread."""
    _, _, task = _task(db)
    posted = await pm_activities.add_comment(
        str(task.id), pm_activities.CommentIn(body="draft"), user=USER,
    )
    await pm_activities.edit_comment(
        posted["id"],
        pm_activities.CommentIn(body="draft @b@fracktal.in"),
        user=USER,
    )
    assert _notified(db, "mention") == ["b@fracktal.in"]

    await pm_activities.edit_comment(
        posted["id"],
        pm_activities.CommentIn(body="draft @b@fracktal.in @c@fracktal.in"),
        user=USER,
    )
    # One NEW row, addressed to c alone — b was not re-notified.
    assert _notified(db, "mention") == ["b@fracktal.in", "c@fracktal.in"]
    # And both delivered mentions became watchers, once each.
    assert set(_watchers(db, task.id)) >= {"b@fracktal.in", "c@fracktal.in"}


async def test_a_description_edit_notifies_only_added_mentions(
    db: FakeProjectsDB,
):
    """Same diff on the task body, and NOTHING else: watchers at large hear
    nothing about a description edit — the diffed mentions are the fan-out."""
    _, _, task = _task(db)
    db.seed("pm_task_watchers", task_id=task.id, watcher="watcher@fracktal.in")
    await pm_tasks.patch_task(
        str(task.id),
        pm_tasks.TaskIn(description="scope for @b@fracktal.in"), user=USER,
    )
    assert _notified(db) == ["b@fracktal.in"]

    await pm_tasks.patch_task(
        str(task.id),
        pm_tasks.TaskIn(
            description="scope for @b@fracktal.in and @c@fracktal.in",
        ),
        user=USER,
    )
    assert _notified(db) == ["b@fracktal.in", "c@fracktal.in"]
    assert _notified(db, "comment") == []


# ── One helper, one INSERT site ─────────────────────────────────────────────

def test_every_subscription_goes_through_ensure_watchers():
    """The auto-subscribe rule lives once. A copy-pasted INSERT would casefold
    or fence agents differently the day one of them is edited alone."""
    inserting = [
        path.name for path in sorted(PACKAGE.glob("*.py"))
        if "INSERT INTO pm_task_watchers" in path.read_text(encoding="utf-8")
    ]
    assert inserting == ["watchers.py"]
    for module in ("tasks.py", "activities.py"):
        assert "ensure_watchers(" in (PACKAGE / module).read_text(
            encoding="utf-8",
        )


# ── Migration 165, read as text (R1: found by content, never by number) ─────

def _watchers_migration() -> Path:
    found = [
        path for path in sorted(MIGRATIONS.glob("*.sql"))
        if path.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS pm_task_watchers"
        in path.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, (
        f"expected exactly one migration creating pm_task_watchers, found "
        f"{[p.name for p in found]}"
    )
    return found[0]


@pytest.fixture(scope="module")
def sql() -> str:
    raw = _watchers_migration().read_text(encoding="utf-8")
    # Comments stripped: an assertion the header's prose could satisfy is an
    # assertion that fails open.
    return "\n".join(re.sub(r"--.*$", "", line) for line in raw.splitlines())


def test_the_table_is_guarded_and_unique_per_pair(sql: str):
    assert "CREATE TABLE IF NOT EXISTS pm_task_watchers" in sql
    assert re.search(r"UNIQUE\s*\(\s*task_id\s*,\s*watcher\s*\)", sql, re.I)


def test_a_watcher_is_stored_folded(sql: str):
    """R10 — every read compares folded; a mixed-case row is a subscription
    that never fires."""
    assert re.search(r"watcher\s*=\s*lower\s*\(\s*watcher\s*\)", sql)


def test_an_agent_cannot_be_a_watcher(sql: str):
    assert re.search(r"watcher\s+NOT\s+LIKE\s+'agent:%'", sql)


def test_a_deleted_task_takes_its_watchers_with_it(sql: str):
    block = sql.split("CREATE TABLE IF NOT EXISTS pm_task_watchers", 1)[1]
    block = block.split(");", 1)[0]
    assert re.search(
        r"task_id\s+UUID\s+NOT\s+NULL\s+REFERENCES\s+pm_tasks\s*\(id\)"
        r"\s+ON\s+DELETE\s+CASCADE",
        block, re.I,
    )


def test_the_tenant_key_is_carried_and_derived(sql: str):
    """D-MT-3 (the column) and migration 161's trigger (the fill-and-verify),
    attached exactly as the other pm_* tables attach it."""
    block = sql.split("CREATE TABLE IF NOT EXISTS pm_task_watchers", 1)[1]
    block = block.split(");", 1)[0]
    assert re.search(
        r"organization_id\s+UUID\s+NOT\s+NULL\s+REFERENCES\s+organization"
        r"\s*\(id\)\s+ON\s+DELETE\s+CASCADE",
        block, re.I,
    )
    assert re.search(
        r"CREATE OR REPLACE TRIGGER \S+\s+BEFORE INSERT OR UPDATE ON "
        r"pm_task_watchers",
        sql,
    )
    assert "pm_organization_from_parent('pm_tasks', 'task_id')" in sql


def test_the_seed_replays_as_a_noop_and_seeds_only_humans(sql: str):
    """The runner replays every migration on every deploy; and the seeded
    authors must satisfy the table's own CHECKs or the second deploy dies."""
    seed = re.search(r"INSERT\s+INTO\s+pm_task_watchers(.*?);", sql, re.S)
    assert seed is not None
    assert re.search(r"ON\s+CONFLICT\s*\(\s*task_id\s*,\s*watcher\s*\)", seed.group(1))
    assert "NOT LIKE 'agent:%'" in seed.group(1)
    assert "lower(t.created_by)" in seed.group(1)
