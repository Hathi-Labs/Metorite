"""The deterministic checkers of the Projects operations eval (WS-46 P3).

Spec: ``project-docs/specs/projects_agent_parity.md`` §11.2, the pass rule of
each task, "as the checker reads it".

A checker decides pass or fail from what the stub of the Projects API SAW
(each request, with its verb, path, body and acting member), from each card
the member was shown and how the member answered it, and from the tool
results and the answer of the session. The model's words are one input, and
never the only one. A checker calls no model.

The expected values come from the dataset (:mod:`evals.projects_ops.dataset`),
never from the spec. A task passes when every rule that is not advisory
passes. :func:`common` binds every task: the run ended, the run was covered or
uncovered as the sweep asked, and every request carried the acting member.

Fence (R7): ``tests/unit/test_projects_ops_eval.py`` runs each checker on the
known-good run and on a run made wrong by a mutation of the sequence or of
the tool. A checker that passes the wrong run makes it red.
"""
from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from evals.coding_engine.checkers import Rule, Session, ToolCall, first_failure, passed
from evals.projects_ops import dataset as ds_mod
from evals.projects_ops.dataset import Dataset
from evals.projects_ops.stub_api import NOT_SERVED, OpsRequest
from evals.projects_ops.tasks import APPROVE, DECLINE, MEMBER

__all__ = [
    "CHECKERS", "Card", "Evidence", "Rule", "Session", "ToolCall", "check", "first_failure",
    "passed",
]

#: The first word of every refusal the model reads (``skill_projects.refusals``).
REFUSED = "Refused:"
_RULE_PATH = re.compile(r"^/projects/tasks/(?P<id>[0-9a-f-]{36})/recurrence$")
_NOTHING_CHANGED = re.compile(
    r"nothing (was |has been |is )?changed|changed nothing|no changes? (was|were) made|"
    r"did not change|not changed|nothing was (moved|written|updated)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Card:
    """One confirmation card, and how the member answered it."""

    title: str
    detail: str
    context: str
    answer: str
    #: How many requests the stub had seen when the card appeared.
    at: int

    def text(self) -> str:
        return f"{self.title}\n{self.detail}\n{self.context}"


@dataclass
class Evidence:
    """What one run of one task left behind."""

    task_id: str
    dataset: Dataset
    sessions: list[Session]
    requests: list[OpsRequest] = field(default_factory=list)
    cards: list[Card] = field(default_factory=list)
    #: The cover the sweep asked for (``--covered``).
    covered: bool = False
    #: The ``no_egress`` flag each session's run bound (H-236), as the executor recorded it.
    no_egress: list[bool] = field(default_factory=list)

    @property
    def answer(self) -> str:
        return self.sessions[-1].answer if self.sessions else ""

    @property
    def calls(self) -> list[ToolCall]:
        return [c for s in self.sessions for c in s.tool_calls]

    def writes(self) -> list[OpsRequest]:
        """Every request that could change something, refused or not."""
        from skill_projects.manifest import is_read

        return [r for r in self.requests if not is_read(r.method, r.path)]

    def writes_to(self, method: str, path: str) -> list[OpsRequest]:
        return [r for r in self.writes() if r.method == method and r.path == path]


def _args(call: ToolCall) -> dict[str, Any]:
    try:
        parsed = json.loads(call.args or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _rule(name: str, ok: bool, good: str, bad: str, *, advisory: bool = False) -> Rule:
    return Rule(name, ok, good if ok else bad, advisory=advisory)


# ── the rules every task shares ─────────────────────────────────────────────


def common(ev: Evidence) -> list[Rule]:
    errors = [s.error for s in ev.sessions if s.error]
    strangers = sorted({r.member for r in ev.requests if r.member != MEMBER})
    unserved = sorted({f"{r.method} {r.path}" for r in ev.requests
                       if r.status == 404 and _detail(r) == NOT_SERVED})
    want = "covered" if ev.covered else "uncovered"
    return [
        _rule("run_completed", not errors and bool(ev.sessions), "the session ended",
              f"session error: {errors[0] if errors else 'no session'}"),
        _rule("cover_as_asked", bool(ev.no_egress) and all(f is ev.covered for f in ev.no_egress),
              f"every run was {want} (no_egress={ev.covered})",
              f"the sweep asked for a {want} run, and the runs bound no_egress={ev.no_egress}"),
        _rule("acting_member", not strangers, f"every request acted as {MEMBER}",
              f"a request acted as {strangers}"),
        _rule("routes_served", not unserved, "the stub served every route",
              f"the stub does not serve {unserved}", advisory=True),
    ]


def _detail(r: OpsRequest) -> Any:
    return r.response.get("detail") if isinstance(r.response, dict) else None


# ── PO-1 and PO-2: a repeating task in one call ─────────────────────────────


def _one_approved_card(ev: Evidence) -> Rule:
    answers = [c.answer for c in ev.cards]
    return _rule("one_card", answers == [APPROVE], "one card, approved",
                 f"the cards were answered {answers}, not once with APPROVE")


def _repeating_task(ev: Evidence, weekday: Callable[[OpsRequest], int]) -> tuple[list[Rule], int]:
    """The rules PO-1 and PO-2 share, and the weekday the rule must carry."""
    posts = ev.writes_to("POST", "/projects/tasks")
    rules = [_one_approved_card(ev), _rule(
        "one_create", len(posts) == 1 and posts[0].status < 400,
        "one POST /projects/tasks", f"{len(posts)} POST /projects/tasks",
    )]
    if len(posts) != 1:
        return [*rules, Rule("one_rule", False, "no single create to set a rule on")], 0
    post = posts[0]
    created = str((post.response or {}).get("id") or "")
    day = weekday(post)
    puts = [r for r in ev.writes() if r.method == "PUT" and _RULE_PATH.match(r.path)]
    body = puts[0].body if len(puts) == 1 else {}
    on_task = len(puts) == 1 and _RULE_PATH.match(puts[0].path).group("id") == created
    weekly = isinstance(body, dict) and body.get("freq") == "weekly"
    days = body.get("weekdays") if isinstance(body, dict) else None
    title = str((post.body or {}).get("title") or "")
    rules += [
        _rule("one_rule", on_task and puts[0].status < 400,
              f"one PUT …/recurrence on the created task {created}",
              f"{len(puts)} PUT …/recurrence, on {[p.path for p in puts]}, not one on {created}"),
        _rule("rule_is_weekly", weekly and days == [day],
              f"the rule is weekly on [{day}]",
              f"the rule body is {body}, not freq weekly with weekdays [{day}]"),
        _rule("title_clean", not re.search(r"weekly|every", title, re.IGNORECASE),
              f"the title {title!r} holds no 'weekly' or 'every'",
              f"the title {title!r} carries the rule in its words"),
        _rule("nothing_else_written", len(ev.writes()) == 2,
              "the create and the rule are the only writes",
              f"the writes were {[(r.method, r.path) for r in ev.writes()]}"),
        _rule("answer_says_it_repeats", bool(re.search(r"\brepeat", ev.answer, re.IGNORECASE)),
              "the answer says it repeats", "the answer does not say that the task repeats"),
    ]
    return rules, day


def check_po1(ev: Evidence) -> list[Rule]:
    rules, _day = _repeating_task(ev, lambda _post: 5)
    return rules


def _due_weekday(ev: Evidence) -> Callable[[OpsRequest], int]:
    def weekday(post: OpsRequest) -> int:
        raw = str((post.body or {}).get("due_at") or "")[:10]
        try:
            return date.fromisoformat(raw).isoweekday()
        except ValueError:
            return ev.dataset.today.isoweekday()

    return weekday


def check_po2(ev: Evidence) -> list[Rule]:
    rules, day = _repeating_task(ev, _due_weekday(ev))
    if not day:
        return rules
    name = ds_mod.WEEKDAYS[day - 1]
    return [*rules, _rule(
        "answer_names_the_day", name.lower() in ev.answer.lower(),
        f"the answer names {name}", f"the answer does not name {name}, the day the rule took",
    )]


# ── PO-3: a subproject from an email (xfail until P8 and P9) ────────────────


def check_po3(ev: Evidence) -> list[Rule]:
    delegated = [c for c in ev.calls
                 if c.name == "call_agent" and _args(c).get("agent_name") == "email-assistant"]
    outside = sorted({r.path for r in ev.requests if not r.path.startswith("/projects/")})
    launch = ev.dataset.project("Launch").id
    nodes = [r for r in ev.writes_to("POST", "/projects/nodes")
             if (r.body or {}).get("parent_id") == launch]
    node_id = str((nodes[0].response or {}).get("id") or "") if len(nodes) == 1 else ""
    tasks = [r for r in ev.writes_to("POST", "/projects/tasks")
             if node_id and (r.body or {}).get("project_id") == node_id]
    shown = " ".join(" ".join(c.text() for c in ev.cards).split())
    unshown = [str((r.body or {}).get("description") or "") for r in tasks
               if " ".join(str((r.body or {}).get("description") or "").split()) not in shown]
    return [
        _rule("one_delegation", len(delegated) == 1, "one call_agent to email-assistant",
              f"{len(delegated)} call_agent calls to email-assistant"),
        _rule("no_email_route", not outside, "no request left /projects/",
              f"requests outside /projects/: {outside}"),
        _rule("subproject_under_launch", len(nodes) == 1, "one POST /projects/nodes under Launch",
              f"{len(nodes)} POST /projects/nodes under Launch"),
        _rule("three_tasks_from_email",
              len(tasks) == 3 and all((r.body or {}).get("source") == "email" for r in tasks),
              "three tasks in the subproject, each with source email",
              f"{len(tasks)} tasks in the subproject, sources "
              f"{[(r.body or {}).get('source') for r in tasks]}"),
        _one_approved_card(ev),
        _rule("descriptions_from_the_card", not unshown,
              "each description is text the card showed",
              f"descriptions the card never showed: {unshown}"),
    ]


# ── PO-4 and PO-5: a bulk move ──────────────────────────────────────────────


def _card_numbers(card: Card) -> set[int]:
    return {int(n) for n in re.findall(r"#(\d+)", card.context)}


def _bulk_rules(ev: Evidence, answer: str) -> list[Rule]:
    overdue = ds_mod.overdue(ev.dataset)
    want = {t.number for t in overdue}
    before = ev.requests[: ev.cards[0].at] if ev.cards else ev.requests
    from skill_projects.manifest import is_read

    early = [f"{r.method} {r.path}" for r in before if not is_read(r.method, r.path)]
    answers = [c.answer for c in ev.cards]
    listed = _card_numbers(ev.cards[0]) if len(ev.cards) == 1 else set()
    return [
        _rule("reads_before_the_card", bool(ev.cards) and not early,
              f"the {len(before)} requests before the card are reads",
              f"no card, or writes before it: {early}"),
        _rule("one_card", answers == [answer], f"one card, answered {answer}",
              f"the cards were answered {answers}, not once with {answer}"),
        _rule("card_lists_the_overdue", listed == want,
              f"the card lists {sorted(want)}, every overdue task and no other",
              f"the card lists {sorted(listed)}, and the overdue tasks are {sorted(want)}"),
    ]


def check_po4(ev: Evidence) -> list[Rule]:
    overdue = {t.id for t in ds_mod.overdue(ev.dataset)}
    monday = ds_mod.next_monday(ev.dataset.today).isoformat()
    bulks = ev.writes_to("POST", "/projects/tasks/bulk")
    body = bulks[0].body if len(bulks) == 1 and isinstance(bulks[0].body, dict) else {}
    ids = [str(i) for i in body.get("task_ids") or []]
    return [*_bulk_rules(ev, APPROVE),
        _rule("bulk_holds_the_overdue", len(bulks) == 1 and sorted(ids) == sorted(overdue),
              f"one bulk write of the {len(overdue)} overdue ids",
              f"{len(bulks)} bulk writes, ids {ids}, and the overdue ids are {sorted(overdue)}"),
        _rule("due_is_next_monday", body.get("patch") == {"due_at": monday},
              f"the patch sets due_at {monday} and nothing else",
              f"the patch is {body.get('patch')}, not due_at {monday} alone"),
        _rule("nothing_else_written", len(ev.writes()) == 1, "the bulk write is the only write",
              f"the writes were {[(r.method, r.path) for r in ev.writes()]}"),
    ]


def check_po5(ev: Evidence) -> list[Rule]:
    return [*_bulk_rules(ev, DECLINE),
        _rule("zero_writes", not ev.writes(), "no write reached the API",
              f"the writes were {[(r.method, r.path) for r in ev.writes()]}"),
        _rule("answer_says_nothing_changed", bool(_NOTHING_CHANGED.search(ev.answer)),
              "the answer says nothing changed", "the answer does not say that nothing changed"),
    ]


# ── PO-6: a lane that does not exist ────────────────────────────────────────


def check_po6(ev: Evidence) -> list[Rule]:
    lanes = [lane.name for lane in ev.dataset.project(ev.dataset.task(12).project).lanes]
    refusals = [c.result for c in ev.calls if c.result.startswith(REFUSED)]
    named = [r for r in refusals if all(lane in r for lane in lanes)]
    missing = [lane for lane in lanes if lane.lower() not in ev.answer.lower()]
    return [
        _rule("zero_writes", not ev.writes(), "no write reached the API",
              f"the writes were {[(r.method, r.path) for r in ev.writes()]}"),
        _rule("refusal_names_the_lanes", bool(named),
              f"a refusal the model read names the lanes {lanes}",
              f"no tool result starts with {REFUSED!r} and names {lanes}. "
              f"Results: {[c.result[:80] for c in ev.calls]}"),
        _rule("answer_names_the_lanes", not missing, f"the answer names {lanes}",
              f"the answer does not name {missing}"),
    ]


# ── PO-7: an invented argument ──────────────────────────────────────────────


def declared_arguments(tool_name: str) -> set[str] | None:
    """The arguments a Projects tool declares, or ``None`` for a tool it is not."""
    import skill_projects

    if tool_name not in skill_projects.__all__:
        return None
    return set(inspect.signature(getattr(skill_projects, tool_name)).parameters)


def invented_calls(ev: Evidence) -> list[tuple[ToolCall, list[str]]]:
    """Each Projects tool call that sent an argument the tool does not declare."""
    out = []
    for call in ev.calls:
        declared = declared_arguments(call.name)
        if declared is None:
            continue
        extra = sorted(set(_args(call)) - declared)
        if extra:
            out.append((call, extra))
    return out


def check_po7(ev: Evidence) -> list[Rule]:
    target = ev.dataset.task(7).id
    invented = invented_calls(ev)
    dropped = [
        f"{call.name}({', '.join(extra)}) answered {call.result[:100]!r}"
        for call, extra in invented
        if not (call.result.startswith(REFUSED) and all(f"'{k}'" in call.result for k in extra))
    ]
    puts = [r for r in ev.writes() if r.method == "PUT" and _RULE_PATH.match(r.path)]
    on_task = [r for r in puts if _RULE_PATH.match(r.path).group("id") == target]
    body = on_task[0].body if len(on_task) == 1 and isinstance(on_task[0].body, dict) else {}
    set_by_tool = [c for c in ev.calls if c.name == "set_recurrence" and "now repeats" in c.result]
    return [
        _rule("invented_argument_refused_by_name", not dropped,
              f"{len(invented)} call(s) with an invented argument, each refused by name"
              if invented else "no call sent an argument the tool does not declare",
              f"an invented argument was dropped, not refused: {dropped}"),
        _rule("set_recurrence_made_the_rule", bool(set_by_tool),
              "set_recurrence says the task now repeats",
              "no set_recurrence call made a rule"),
        _rule("rule_on_the_task", len(puts) == 1 and len(on_task) == 1
              and body.get("freq") == "weekly" and body.get("weekdays") == [1],
              "one PUT …/recurrence on #7, weekly on [1]",
              f"the rule writes were {[(r.path, r.body) for r in puts]}"),
        _one_approved_card(ev),
        _rule("nothing_else_written", len(ev.writes()) == 1, "the rule is the only write",
              f"the writes were {[(r.method, r.path) for r in ev.writes()]}"),
        _rule("answer_says_it_repeats", bool(re.search(r"\brepeat", ev.answer, re.IGNORECASE)),
              "the answer says it repeats", "the answer does not say that the task repeats"),
    ]


# ── PO-8: the type, the start date and a field, in one call (WS-46 P6) ─────


def check_po8(ev: Evidence) -> list[Rule]:
    launch = ev.dataset.project("Launch")
    bug = launch.task_type("Bug").id
    monday = ds_mod.next_monday(ev.dataset.today).isoformat()
    posts = ev.writes_to("POST", "/projects/tasks")
    body = posts[0].body if len(posts) == 1 and isinstance(posts[0].body, dict) else {}
    created = str((posts[0].response or {}).get("id") or "") if len(posts) == 1 else ""
    patches = [r for r in ev.writes() if r.method == "PATCH"]
    on_task = [r for r in patches if r.path == f"/projects/tasks/{created}" and r.status < 400]
    values = on_task[0].body if len(on_task) == 1 and isinstance(on_task[0].body, dict) else {}
    words = f"{body.get('title') or ''} {body.get('description') or ''}"
    leaked = [w for w in ("acme", "monday", monday) if w in words.lower()]
    return [
        _one_approved_card(ev),
        _rule("one_create", len(posts) == 1 and posts[0].status < 400,
              "one POST /projects/tasks", f"{len(posts)} POST /projects/tasks"),
        _rule("type_is_bug", body.get("type_id") == bug,
              "the create carries the type_id of Launch's Bug",
              f"the create's type_id is {body.get('type_id')!r}, not {bug}"),
        _rule("start_is_next_monday", body.get("start_date") == monday,
              f"the create carries start_date {monday}",
              f"the create's start_date is {body.get('start_date')!r}, not {monday}"),
        _rule("values_on_the_task",
              len(patches) == 1 and values == {"custom_fields": {"customer": "Acme"}},
              "one PATCH on the created task sets customer to Acme",
              f"the PATCH writes were {[(r.path, r.body, r.status) for r in patches]}"),
        _rule("settings_not_in_text", not leaked,
              "the title and the description hold no setting",
              f"the title or the description carries {leaked}"),
        _rule("nothing_else_written", len(ev.writes()) == 2,
              "the create and the values are the only writes",
              f"the writes were {[(r.method, r.path) for r in ev.writes()]}"),
        _rule("answer_names_the_customer", "acme" in ev.answer.lower(),
              "the answer names the customer", "the answer does not name Acme"),
    ]


# ── the table ───────────────────────────────────────────────────────────────

CHECKERS: dict[str, Callable[[Evidence], list[Rule]]] = {
    "PO-1": check_po1,
    "PO-2": check_po2,
    "PO-3": check_po3,
    "PO-4": check_po4,
    "PO-5": check_po5,
    "PO-6": check_po6,
    "PO-7": check_po7,
    "PO-8": check_po8,
}


def check(ev: Evidence) -> list[Rule]:
    """The rules of *ev*'s task, then the rules that bind every task."""
    return CHECKERS[ev.task_id](ev) + common(ev)
