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


# ── The second flag, and why it is allowed to exist ──────────────────────

def test_the_flag_is_off_unless_a_deployment_says_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default OFF is load-bearing, not caution.

    `gtd_items` still holds every task anybody has captured and the S3b backfill
    is owner-gated and unrun, so turning this on early does not degrade the
    assistant — it makes every answer empty, on a 200.
    """
    monkeypatch.delenv(cal.TASKS_LENS_FLAG, raising=False)
    assert cal.tasks_lens_enabled() is False
    for off in ("", "0", "false", "no", "off"):
        monkeypatch.setenv(cal.TASKS_LENS_FLAG, off)
        assert cal.tasks_lens_enabled() is False, off
    for on in ("1", "true", "yes", "on", " ON "):
        monkeypatch.setenv(cal.TASKS_LENS_FLAG, on)
        assert cal.tasks_lens_enabled() is True, on


def test_the_flag_is_read_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flip is a RESTART, never a release — and never a re-import.

    Read at import time this would be frozen at whatever the environment said
    when the first module touched it, which in a test process is "whatever ran
    first" and on a box is "before the operator edited `.env`".
    """
    monkeypatch.delenv(cal.TASKS_LENS_FLAG, raising=False)
    assert cal.agent_source() is cal.GTD_SOURCE
    monkeypatch.setenv(cal.TASKS_LENS_FLAG, "1")
    assert cal.agent_source() is LENS_SOURCE
    monkeypatch.setenv(cal.TASKS_LENS_FLAG, "0")
    assert cal.agent_source() is cal.GTD_SOURCE


def test_every_browserless_surface_asks_which_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⚠️ **Re-cut 2026-08-25 (slice 3).** This test used to assert the agent
    planner was still PINNED to `gtd_items`, which was the honest thing to say
    while the mechanism was undecided. It is decided, so the invariant flips:
    every server-side surface that touches a member's tasks must now go through
    `agent_source()`, and none may name a store directly.

    The list is the surfaces that have **no browser**, so they cannot pick a
    store by picking a route the way the Calendar UI does. Two of them WRITE,
    and the nightly sweep writes unattended, per tenant, every night — which is
    why "we will get to it" was not an acceptable answer for long.
    """
    surfaces = {
        "plan_today": '"/calendar/plan-today"',
        "replan_today": '"/calendar/replan-today"',
        "rollover_today": '"/calendar/rollover-today"',
        "day_summary": '"/calendar/day-summary"',
        "_apply_plan_blocks": "async def _apply_plan_blocks(",
        "_rollover_one_user": "async def _rollover_one_user(",
    }
    pinned = []
    for name, anchor in surfaces.items():
        start = CALENDAR_SRC.index(anchor)
        # The next TOP-LEVEL thing after this surface. A decorator is
        # followed immediately by its own `def` with no blank line, so
        # bounding on "\n\n" is what tells a route's body apart
        # from the decorator line that introduces it. Getting this wrong
        # gives every route an EMPTY body and the fence passes on nothing.
        ends = [
            CALENDAR_SRC.find("\n@router", start + 1),
            CALENDAR_SRC.find("\n\nasync def ", start + 1),
            CALENDAR_SRC.find("\n\ndef ", start + 1),
        ]
        end = min([e for e in ends if e > start] or [len(CALENDAR_SRC)])
        body = CALENDAR_SRC[start:end]
        # ⚠️ Two conditions, and the second exists because the first draft of
        # this fence PASSED a deliberate regression. It read `if
        # "agent_source()" not in body and "src." not in body`, and pinning the
        # nightly sweep back with `src = GTD_SOURCE` left every `src.` call in
        # place — so the fence saw a source variable and approved. A fence that
        # holds a bug still is worse than none.
        asks = "agent_source()" in body
        names_a_store = "GTD_SOURCE" in body or "LENS_SOURCE" in body
        if not asks or names_a_store:
            pinned.append(name)
    assert not pinned, (
        f"{pinned} name a store directly instead of asking `agent_source()`. "
        "These surfaces have no browser, so a hard-coded store is one nobody "
        "can change without a code release — and the nightly sweep would go on "
        "writing the retiring store after the cutover, every night, for every "
        "customer."
    )


def test_the_mismatch_is_reportable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole justification for allowing a SECOND flag.

    The browser's flag is read by the Next.js build; the gateway's by this
    process. They cannot be one variable, so they must at least be one
    QUESTION — and `/version` is where it is answered, unauthenticated, from a
    laptop, mid-incident. Without this the two disagreeing is silent: the UI
    reads one store while the assistant and the roll-over write the other.
    """
    from gateway.main import Version, version

    src = inspect.getsource(version)
    assert "tasks_lens_enabled()" in src, (
        "/version no longer reports the store flag; a mismatch between the "
        "browser build and the gateway becomes unobservable again"
    )
    assert "tasks_lens" in Version.model_fields
