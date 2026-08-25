"""WS-39 S3a-client slice 2 — the day planner's two stores must look alike.

Board WS-39 · spec `calendar_focus_os.md` §10 · **D53**.

The planner now reads through a `TaskSource`: `GTD_SOURCE` for the retiring
store, `LENS_SOURCE` for `pm_tasks` + `pm_task_personal`. Everything downstream
of those reads — the packer, the LLM ranker, the horizon parser, the capacity
arithmetic, the eviction rules — is shared, unchanged, and completely unaware of
which store it is working on.

**That is what makes the design worth having and it is also its one failure
mode.** The shared code reads attributes off rows by name. A source that returns
a row missing one of those names does not raise: `getattr(row, "energy", None)`
answers `None`, the packer treats the task as having no energy fit, and a day
gets planned slightly wrong forever. There is no error and no log line.

So the parity is asserted here rather than trusted — and asserted against the
CONSUMERS, by reading what the planner actually looks up, not against a list
somebody remembered to keep in step.

Hermetic: no database. The SQL itself is verified in
`tests/live/live_ws39_s3a_client2.py` (R8), which drives the derivation from
both sides against a real Postgres.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from gateway.routes.projects.planning import LENS_SOURCE, _pm_row
from gateway.routes.tasks import calendar as cal

CALENDAR_SRC = Path(cal.__file__).read_text(encoding="utf-8")
PLANNING_SRC = Path(
    inspect.getfile(LENS_SOURCE.__class__)
).read_text(encoding="utf-8")


# ── The two sources answer the same questions ───────────────────────────────

def _reads() -> list[str]:
    """The `TaskSource` methods the planner may call."""
    return [
        name for name, member in inspect.getmembers(cal.TaskSource)
        if not name.startswith("_") and callable(member)
    ]


def test_both_sources_implement_every_read() -> None:
    """The base class raises `NotImplementedError`; a source that forgot one
    would fail at request time, on whichever planner button nobody clicked
    during review."""
    missing: dict[str, list[str]] = {}
    for src in (cal.GTD_SOURCE, LENS_SOURCE):
        gaps = [
            name for name in _reads()
            if getattr(type(src), name, None) is getattr(cal.TaskSource, name)
        ]
        if gaps:
            missing[src.name] = gaps
    assert not missing, (
        f"a TaskSource does not override every read: {missing}. The base class "
        "raises, so this surfaces as a 500 on one planner button rather than "
        "at import."
    )


def test_the_two_sources_are_distinguishable() -> None:
    """A guard against the laziest possible regression — aliasing one source to
    the other, which makes every test above pass and plans the wrong store."""
    assert cal.GTD_SOURCE.name != LENS_SOURCE.name
    assert type(cal.GTD_SOURCE) is not type(LENS_SOURCE)


# ── The rows carry the names the shared code reads ──────────────────────────

def _names_the_planner_reads() -> set[str]:
    """Every attribute the shared planner looks up on a task row.

    Read out of `_candidate_brief` and `_replan_core` rather than listed here,
    because a list is a mirror: somebody adds `urgency` to the brief, forgets
    this file, and the pm source silently answers `None` for it.
    """
    body = CALENDAR_SRC[
        CALENDAR_SRC.index("def _candidate_brief("):
        CALENDAR_SRC.index("def _rank_fallback(")
    ] + CALENDAR_SRC[
        CALENDAR_SRC.index("async def _replan_core("):
        CALENDAR_SRC.index("def _window_of(")
    ]
    names = set(re.findall(r'getattr\(\s*[a-z_]+,\s*"([a-z_]+)"', body))
    names |= set(re.findall(r"\bm\.([a-z_]+)\b", body))
    # Not row attributes: locals and the request object's own fields.
    return names - {"tzinfo", "get", "items", "keys", "values"}


def test_the_lens_row_carries_every_name_the_planner_reads() -> None:
    row = _pm_row(SimpleNamespace(
        id="t1", title="A task", notes=None, due_at=None, project_id="p1",
        parent_task_id=None, next_action=None, context="@computer",
        energy="high", time_estimate_mins=30, scheduled_start=None,
        scheduled_end=None, flexible=None, is_hard_date=None,
        actual_start=None, actual_end=None, important=True, leveraged=None,
        deep_work=None, kept_mine=None, sort_key=None, is_mine=True,
        stated_disposition=None, status_category="todo", assignee_count=1,
    ))
    wanted = _names_the_planner_reads()
    # Sanity: an empty set would make this test assert nothing at all.
    assert {"energy", "flexible", "scheduled_start"} <= wanted, (
        f"the extractor found {sorted(wanted)} — it has stopped matching the "
        "planner's reads, so this fence is blind"
    )
    missing = sorted(n for n in wanted if not hasattr(row, n))
    assert not missing, (
        f"a lens row has no {missing}. The planner reads these by name and "
        "`getattr(..., None)` does not raise — the day just gets planned "
        "slightly wrong, forever."
    )


# ── The derivation, which is the whole point of the slice ───────────────────

def _row(**kw) -> SimpleNamespace:
    base = dict(
        id="t1", title="t", notes=None, due_at=None, project_id="p",
        parent_task_id=None, next_action=None, context=None, energy=None,
        time_estimate_mins=None, scheduled_start=None, scheduled_end=None,
        flexible=None, is_hard_date=None, actual_start=None, actual_end=None,
        important=None, leveraged=None, deep_work=None, kept_mine=None,
        sort_key=None, is_mine=False, stated_disposition=None,
        status_category="todo", assignee_count=0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        # The case this whole slice exists for. A member who has never triaged
        # a task has NO overlay row, so the stored disposition is NULL — and a
        # planner that filtered on the stored column would plan an empty day
        # for every member whose board is untriaged, which is every member on
        # day one.
        ({"is_mine": True, "assignee_count": 1}, "NEXT"),
        ({"status_category": "done", "is_mine": True, "assignee_count": 1},
         "DONE"),
        ({"status_category": "backlog", "is_mine": True, "assignee_count": 1},
         "SOMEDAY"),
        ({"is_mine": False, "assignee_count": 1}, "WAITING"),
        ({"is_mine": False, "assignee_count": 0}, "INBOX"),
    ],
)
def test_an_untriaged_task_gets_the_derived_disposition(kw, expected) -> None:
    assert _pm_row(_row(**kw)).disposition == expected


def test_a_stated_disposition_always_wins() -> None:
    """The other direction, and the one the SQL prune could get wrong.

    Each query narrows on the STATED column before Python rules on the
    effective one. That prune can only be wrong by dropping a row it should
    have kept — a member who filed a backlog task as NEXT — so the rule it
    prunes against has to be this one.
    """
    row = _pm_row(_row(status_category="backlog", is_mine=True,
                       assignee_count=1, stated_disposition="NEXT"))
    assert row.disposition == "NEXT"
    trashed = _pm_row(_row(is_mine=True, assignee_count=1,
                           stated_disposition="TRASH"))
    assert trashed.disposition == "TRASH"


# ── Structural: no second planner, no second membership rule ────────────────

def test_the_lens_does_not_re_derive_the_planner() -> None:
    """`planning.py` chooses ROWS. It must never grow geometry.

    The packer is where this feature's behaviour actually lives; a second copy
    of it would diverge on the first bug fix only one of them received, and the
    symptom would be "the calendar packs differently since we switched".
    """
    for banned in ("_free_intervals", "_place_one", "def _replan_core",
                   "_expand_templates", "_lunch_interval"):
        assert f"def {banned}" not in PLANNING_SRC and \
               f"{banned} = " not in PLANNING_SRC, (
            f"`{banned}` looks re-implemented in planning.py; import it from "
            "routes/tasks/calendar.py instead"
        )


def test_the_lens_composes_the_shared_membership_clause() -> None:
    """"Which tasks are mine" has exactly one definition (`MY_TASKS_FROM`).

    A hand-written FROM here would drift from `/projects/my/inbox`, and the
    drift that matters is the tenant clause: without it another organization
    can put a row in this member's plan by typing their address (WS-29b).
    """
    assert "MY_TASKS_FROM" in PLANNING_SRC
    assert "FROM pm_tasks t" not in PLANNING_SRC, (
        "planning.py writes its own FROM clause instead of composing "
        "MY_TASKS_FROM — two answers to the one question that must have one"
    )


def test_the_agent_planner_is_still_on_the_old_store() -> None:
    """Recorded, not accidental — slice 3 (H-33).

    The agent surface has no browser and so no flag to read. Giving it a
    server-side one would create a second flag that must agree with the client's,
    and two flags that must agree are a mismatch waiting to be found by a user.
    This test fails the day somebody routes it, so they have to come and read
    the paragraph above the agent endpoints.
    """
    agent = CALENDAR_SRC[CALENDAR_SRC.index('"/calendar/plan-today"'):]
    assert "LENS_SOURCE" not in agent
    assert "src=GTD_SOURCE" in agent, (
        "the agent planner no longer names its store explicitly; it must, "
        "because the default is the one that will be wrong after the cutover"
    )
