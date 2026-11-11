"""WS-55 EA-18c: the agent questions on the My Day card (``agent_questions``).

Spec: ``project-docs/specs/external_agent_access.md`` §12.8 "My Day (EA-18c)".

Hermetic. The provider reads the list's own read, ``agent_questions.read_questions``,
and writes no SQL of its own. These cases hold the card kind, its source, its
group, its weight, the "3 oldest" cut, the count, the flag and the empty state.
The R8 case through the real provider is in ``test_feed_agent_questions_r8.py``.

**Mutations this suite catches.** Commit first, restore with ``git checkout``.

* M1. ``feed._agent_questions``: drop the ``agent_questions_allowed`` check.
* M2. ``feed._agent_questions``: drop the ``why is not None`` filter ("For me").
* M3. ``feed_rank.BASE``: drop the ``agent_questions`` row.
* M4. ``feed._agent_questions``: read ``total`` from the 3 shown, not the whole list.
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("gateway.routes.shell", reason="gateway not installed")

from gateway.routes.projects import agent_questions as aq
from gateway.routes.projects import agent_runs, filters
from gateway.routes.shell import feed, feed_rank, needs
from gateway.routes.shell import prefs as shell_prefs

NOW = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)
TZ = ZoneInfo("UTC")
TODAY = date(2026, 11, 11)
_REPO = Path(__file__).resolve().parents[2]


def _user():
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(email="me@x.test", role=UserRole.EMPLOYEE,
                       access=build_access(["feature:projects"]), organization_id="o-1")


def _pair(n: int, *, why: str | None = "created", body: str = "Which branch?",
          options: tuple[str, ...] = ("main", "dev")):
    run = SimpleNamespace(
        run_id=f"run-{n}", task_id=f"task-{n}", task_number=n, task_title=f"Task {n}",
        client_via="mcp:claude", run_started_at=NOW, project_id=f"p-{n}", why=why,
        may_answer=True)
    head = SimpleNamespace(id=f"act-{n}", run_id=f"run-{n}", created_at=NOW)
    question = SimpleNamespace(
        id=f"act-{n}", run_id=f"run-{n}", created_at=NOW, content={"body": body},
        signal_metadata={"options": [{"id": o, "label": o} for o in options]},
        content_metadata=None)
    return run, head, question


@pytest.fixture
def world(monkeypatch):
    """The list's read and its row builder, faked. The provider's glue is real."""
    w = SimpleNamespace(on=True, runs=True, pairs=[], reads=0)

    @asynccontextmanager
    async def session():
        yield object()

    async def resolve(db, user):
        return SimpleNamespace(params={})

    async def scope(db, vis, project_id, _flag):
        return "TRUE"

    async def read(db, vis, me, scope_sql, params):
        w.reads += 1
        return [(r, h) for r, h, _q in w.pairs]

    async def rows(db, vis, page, *, review):
        by_run = {q.run_id: q for _r, _h, q in w.pairs}
        return [aq.question_row(r, by_run[r.run_id], [{"id": "p", "name": "Metorite"},
                                                      {"id": "q", "name": "AI Studio"}],
                                review=review) for r, _h in page]

    monkeypatch.setattr(filters, "agent_questions_allowed", lambda org: w.on)
    monkeypatch.setattr(aq, "agent_runs_enabled", lambda: w.runs)
    monkeypatch.setattr(aq, "_tenant_session", session)
    monkeypatch.setattr(aq, "resolve_visibility", resolve)
    monkeypatch.setattr(aq, "scope_clause", scope)
    monkeypatch.setattr(aq, "read_questions", read)
    monkeypatch.setattr(aq, "question_rows", rows)
    return w


def test_the_provider_is_feed_only_gated_and_belongs_to_projects(monkeypatch) -> None:
    from acb_common import get_settings

    p = needs.REGISTRY["agent_questions"]
    assert p.needs is False and p.gated is True
    assert p.kinds == frozenset({"agent_questions"}) and p.features == ("projects",)
    assert p not in needs.needs_providers()
    # Gated: it runs only while MY_DAY_PROVIDERS lists it (the feed is byte-equal before).
    monkeypatch.setattr(get_settings(), "my_day_providers", "")
    assert p not in needs.feed_only_providers()
    monkeypatch.setattr(get_settings(), "my_day_providers", "agent_questions")
    assert p in needs.feed_only_providers()
    assert needs.source_app("agent_questions") == "projects"
    # Not a bell row: the bell keeps showing the notification (`ASKING_NOTIFICATIONS`).
    assert "agent_questions" not in needs.KIND_ORDER


def test_the_kind_is_in_each_place_a_feed_kind_must_be() -> None:
    assert feed_rank.BASE["agent_questions"] == feed_rank.BASE["decision"]
    assert "agent_questions" in shell_prefs.feed_kinds()
    assert len(shell_prefs.feed_kinds()) == shell_prefs.MAX_KINDS
    assert feed_rank.group("agent_questions", {}, NOW, TODAY) == "needs_you"
    ts = (_REPO / "workbench/control_plane/src/lib/shell/feed.ts").read_text(encoding="utf-8")
    assert 'agent_questions: "Waiting for your answer"' in ts
    spec = (_REPO / "project-docs/specs/my_day_feed.md").read_text(encoding="utf-8")
    assert "| `agent_questions` | Needs you |" in spec


def test_the_reason_arm_of_my_agent_stays_in_the_one_text() -> None:
    """The provider reads the list's own read. It never builds this text alone,
    and the "mine" scope it keeps is this ``why``."""
    assert "AND lower(r.member_email) = :me THEN 'my_agent'" in agent_runs.question_reason_sql("r")
    # The provider calls the Projects app's own read, and that read calls the
    # route's `read_questions`. Neither builds this text, or any SQL, alone.
    source = inspect.getsource(feed._agent_questions)
    assert "aq.read_mine(" in source and "question_reason_sql" not in source
    assert "text(" not in source and "SELECT" not in source
    mine = inspect.getsource(aq.read_mine)
    assert "read_questions(" in mine and "question_rows(" in mine
    assert "question_reason_sql" not in mine and "text(" not in mine and "SELECT" not in mine


async def test_the_flag_off_reads_absent_and_runs_no_read(world) -> None:
    world.on = False
    world.pairs = [_pair(1)]
    with pytest.raises(needs.SourceAbsent):
        await needs.REGISTRY["agent_questions"].run(_user())
    assert world.reads == 0


async def test_a_member_with_no_open_question_sees_no_card(world) -> None:
    found = await needs.REGISTRY["agent_questions"].run(_user())
    assert found.total == 0 and found.signals() == []
    # A question that is not for me gives no card either.
    world.pairs = [_pair(1, why=None)]
    found = await needs.REGISTRY["agent_questions"].run(_user())
    assert found.total == 0 and found.signals() == []


async def test_the_runs_flag_off_gives_no_card(world) -> None:
    world.runs = False
    world.pairs = [_pair(1)]
    found = await needs.REGISTRY["agent_questions"].run(_user())
    assert found.signals() == [] and world.reads == 0


async def test_the_card_shows_path_question_options_and_the_whole_count(world) -> None:
    world.pairs = [_pair(1), _pair(2), _pair(3), _pair(4), _pair(5, why=None)]
    found = await needs.REGISTRY["agent_questions"].run(_user())
    [signal] = found.signals()
    # The count is the list's "mine" count: 4, not the 3 shown and not 5.
    assert found.total == 4 and signal.facts["total"] == 4
    assert signal.title == "4 agent questions wait for you"
    assert [q["activity_id"] for q in signal.facts["questions"]] == ["act-1", "act-2", "act-3"]
    [card] = feed.build_cards([signal], NOW, TZ, TODAY)
    assert card["kind"] == "agent_questions" and card["group"] == "needs_you"
    assert card["why_key"] == "agent_questions"
    assert card["why"] == ("Metorite \u203a AI Studio: Which branch? Options: main, dev. "
                           "3 more questions wait.")
    assert [(a["id"], a["way"], a["href"]) for a in card["acts"]] == [
        ("answer", "open", "/projects?app=questions")]
    assert card["score"] == feed_rank.BASE["agent_questions"]
    assert card["signal_id"] == "questions:mine" and card["app"] == "projects"


async def test_one_question_reads_in_the_singular(world) -> None:
    world.pairs = [_pair(1, options=())]
    [signal] = (await needs.REGISTRY["agent_questions"].run(_user())).signals()
    assert signal.title == "An agent question waits for you"
    [card] = feed.build_cards([signal], NOW, TZ, TODAY)
    assert card["why"] == "Metorite \u203a AI Studio: Which branch?"


async def test_a_new_question_moves_the_fingerprint(world) -> None:
    world.pairs = [_pair(1)]
    [one] = (await needs.REGISTRY["agent_questions"].run(_user())).signals()
    world.pairs = [_pair(1), _pair(2)]
    [two] = (await needs.REGISTRY["agent_questions"].run(_user())).signals()
    a = feed.build_cards([one], NOW, TZ, TODAY)[0]["fingerprint"]
    b = feed.build_cards([two], NOW, TZ, TODAY)[0]["fingerprint"]
    assert a != b


async def test_a_long_question_and_many_options_are_clipped(world) -> None:
    world.pairs = [_pair(1, body="x" * 400, options=tuple(f"opt{i}" for i in range(9)))]
    [signal] = (await needs.REGISTRY["agent_questions"].run(_user())).signals()
    [fact] = signal.facts["questions"]
    assert len(fact["question"]) == feed.QUESTION_CLIP and fact["question"].endswith("\u2026")
    assert len(fact["options"]) == feed.OPTIONS_SHOWN


async def test_a_new_newer_question_moves_the_state_fingerprint_not_only_the_content(world) -> None:
    """P1-b. ``state_fp`` hashes ``STATE_FACTS`` only. The card keys ``waiting_since``
    on the NEWEST question, so two questions with the same oldest time and a newer
    second one give a new ``state_fp``, and a dismissed card comes back."""
    from datetime import timedelta

    from gateway.routes.shell import state

    world.pairs = [_pair(1)]
    [one] = (await needs.REGISTRY["agent_questions"].run(_user())).signals()
    newer = _pair(2)
    newer[1].created_at = NOW + timedelta(hours=1)
    newer[2].created_at = NOW + timedelta(hours=1)
    world.pairs = [_pair(1), newer]
    [two] = (await needs.REGISTRY["agent_questions"].run(_user())).signals()
    assert one.facts["waiting_since"] != two.facts["waiting_since"]
    assert state.state_fp("questions:mine", one.facts) != state.state_fp("questions:mine", two.facts)
    # The same list again keeps the fingerprint (no churn).
    [again] = (await needs.REGISTRY["agent_questions"].run(_user())).signals()
    assert state.state_fp("questions:mine", two.facts) == state.state_fp("questions:mine", again.facts)


def test_the_kind_has_a_reach_class() -> None:
    from gateway.reach import classes

    assert classes.REACH_CLASS["agent_questions"] == classes.REACH_CLASS["agent_asks"]
