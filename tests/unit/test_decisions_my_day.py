"""WS-ALN ALN-2b: a decision on screen (My Day card, bell verb, panel rules).

Spec: ``project-docs/specs/project_alignment.md`` §3.2 and the row ALN-2b of §10.

Hermetic. The R8 cases (the real routes, the real provider on Postgres, the
close on answer, the non-decider read) are in ``test_decisions.py``.

* The provider is feed-only, it gates on ``DECISIONS_ORGS``, and it gives one
  signal for each task with a link act (``needs.py`` has no real act until MD-8).
* A decision folds into the task's one card with its bell row, and the card
  asks for an answer (``needs_you``).
* ``decision`` is an asking notification, and the bell has its verb.
* The client keeps the same rules (``src/app/projects/lib/decisions.test.ts``).

**Mutations this suite catches.** Commit first, restore with ``git checkout``.

* M1. ``needs._decisions``: drop the ``decisions_allowed`` check.
* M2. ``needs.ASKING_NOTIFICATIONS``: drop ``"decision"``.
* M3. ``feed_rank.group``: drop ``"decision"`` from the ``needs_you`` kinds.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("gateway.routes.shell", reason="gateway not installed")

from gateway.routes.shell import feed, feed_rank, needs
from gateway.routes.shell import prefs as shell_prefs

NOW = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
TZ = ZoneInfo("UTC")
TODAY = date(2026, 11, 4)
TASK = "11111111-1111-4111-8111-111111111111"
_REPO = Path(__file__).resolve().parents[2]


def _decision(**extra: object) -> dict:
    return {
        "id": "d-1", "subject_id": TASK, "question": "Ship the extruder fix today?",
        "asked_by": "ravi@x.test", "task_title": "Fix the extruder",
        "created_at": "2026-11-04T08:00:00+00:00",
        "expires_at": "2026-11-05T08:00:00+00:00", **extra,
    }


@pytest.fixture
def provider(monkeypatch):
    from gateway.routes.projects import decisions

    async def fake_list(task_id=None, user=None):
        return {"decisions": fake_list.rows}

    fake_list.rows = [_decision()]
    monkeypatch.setattr(decisions, "list_decisions", fake_list)
    monkeypatch.setattr(decisions, "decisions_allowed", lambda org: fake_list.on)
    fake_list.on = True
    return fake_list


def _user():
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(email="d@x.test", role=UserRole.EMPLOYEE,
                       access=build_access(["feature:projects"]), organization_id="o-1")


def test_the_provider_is_feed_only_gated_and_registers_after_the_needs_providers(
    monkeypatch,
) -> None:
    from acb_common import get_settings

    p = needs.REGISTRY["decisions"]
    assert p.needs is False and p.gated is True and p.kinds == frozenset({"decision"})
    assert p not in needs.needs_providers()
    # Gated: it runs only while MY_DAY_PROVIDERS lists it.
    monkeypatch.setattr(get_settings(), "my_day_providers", "")
    assert p not in needs.feed_only_providers()
    monkeypatch.setattr(get_settings(), "my_day_providers", "decisions")
    assert p in needs.feed_only_providers()
    assert p.features == ("projects",) and needs.source_app("decisions") == "projects"
    assert "decision" not in needs.KIND_ORDER  # the bell shows it as a notification row


async def test_one_signal_for_each_task_with_a_link_act(provider) -> None:
    provider.rows = [
        _decision(id="d-1", expires_at="2026-11-06T08:00:00+00:00"),
        _decision(id="d-2", question="Or tomorrow?", expires_at="2026-11-05T08:00:00+00:00"),
    ]
    found = await needs.REGISTRY["decisions"].run(_user())
    [signal] = found.signals()
    assert found.total == 1 and found.next_edge == "2026-11-05T08:00:00+00:00"
    # The decision that closes first, and the number that wait.
    assert signal.facts["question"] == "Or tomorrow?" and signal.facts["open"] == 2
    assert signal.signal_id == f"tasks:{TASK}" and signal.title == "Fix the extruder"
    assert signal.href == f"/projects?task={TASK}"
    assert [(a["way"], a["href"]) for a in signal.acts] == [("open", signal.href)]
    assert signal.expires_at == "2026-11-05T08:00:00+00:00"


async def test_with_the_flag_off_the_source_reads_absent(provider) -> None:
    provider.on = False
    with pytest.raises(needs.SourceAbsent):
        await needs.REGISTRY["decisions"].run(_user())


async def test_an_empty_list_gives_no_card(provider) -> None:
    provider.rows = []
    found = await needs.REGISTRY["decisions"].run(_user())
    assert found.total == 0 and found.signals() == [] and found.next_edge is None


async def test_a_decision_is_a_needs_you_card_that_folds_with_its_bell_row(provider) -> None:
    [signal] = (await needs.REGISTRY["decisions"].run(_user())).signals()
    alone = feed.build_cards([signal], NOW, TZ, TODAY)
    assert [c["kind"] for c in alone] == ["decision"]
    assert alone[0]["group"] == "needs_you" and alone[0]["why_key"] == "decision"
    assert "Ship the extruder fix today?" in alone[0]["why"]
    assert "ravi asks you to decide" in alone[0]["why"]
    # Its bell row (unread) folds into the same card. One card, not two.
    note = needs._note_signals([{
        "id": "n-1", "task_id": TASK, "kind": "decision", "actor": "ravi@x.test",
        "task_title": "Fix the extruder", "excerpt": "Ship the extruder fix today?",
        "created_at": NOW - timedelta(hours=2)}])
    [card] = feed.build_cards([signal, *note], NOW, TZ, TODAY)
    assert card["kind"] == "notification" and card["folded"] == ["decision"]
    assert card["group"] == "needs_you" and "Ship the extruder fix today?" in card["why"]
    assert [a["id"] for a in card["acts"]] == ["read", "open"]  # one link to the place


def test_an_answered_bell_row_alone_still_asks_for_nothing_new() -> None:
    """A read bell row is not in the feed, so the card ends with the decision."""
    assert "decision" in needs.ASKING_NOTIFICATIONS
    note = needs._note_signals([{
        "id": "n-1", "task_id": TASK, "kind": "decision", "actor": "ravi@x.test",
        "task_title": "T", "excerpt": "Q?", "created_at": NOW}])
    assert feed_rank.group("notification", note[0].facts, NOW, TODAY) == "needs_you"


def test_the_kind_is_in_each_place_a_feed_kind_must_be() -> None:
    assert feed_rank.BASE["decision"] == feed_rank.BASE["agent_asks"]
    assert "decision" in shell_prefs.feed_kinds()
    assert len(shell_prefs.feed_kinds()) == shell_prefs.MAX_KINDS
    assert feed_rank.group("decision", {}, NOW, TODAY) == "needs_you"
    ts = (_REPO / "workbench/control_plane/src/lib/shell/feed.ts").read_text(encoding="utf-8")
    assert 'decision: "Waiting for your decision"' in ts
    verbs = (_REPO / "workbench/control_plane/src/app/projects/lib/notifications.ts").read_text(
        encoding="utf-8")
    assert 'decision: "asks you to decide on"' in verbs


def test_the_panel_calls_the_one_answer_route_and_the_task_read() -> None:
    api = (_REPO / "workbench/control_plane/src/app/projects/lib/api.ts").read_text(encoding="utf-8")
    assert "`decisions/${encodeURIComponent(decisionId)}/answer`" in api
    assert "`decisions?task_id=${encodeURIComponent(taskId)}`" in api
    panel = (_REPO / "workbench/control_plane/src/app/projects/components/DecisionPanel.tsx"
             ).read_text(encoding="utf-8")
    assert "decisionsApi.answer(" in panel and "fetch(" not in panel
