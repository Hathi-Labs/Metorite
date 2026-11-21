"""The pure checks of an answer (SEM-6). No I/O, no model, no database.

An answer is ``(status, body)``: the HTTP status of the route and its JSON body.
A check returns the list of what is wrong. An empty list is a pass. Each check
reads the expectation of the fixture, so a mutation of the answer (a 422 where
a count was due, a zero where a count was due, a refusal with no working step)
fails here and the run prints FAIL.
"""

from __future__ import annotations

import math
from typing import Any

from evals.semantic.questions import Step

#: A database error that must never reach a member as the answer of a range.
#: What a total says when its filters matched no record (CRM-Q34-3). The check holds
#: its own copy of the words, so a change of the tool text fails here.
NO_MATCH_LINE = "No record matches these filters"
CAST_WORDS = ("invalid input syntax", "cannot cast", "numeric field overflow", "invalid cast")


def _groups(body: dict[str, Any]) -> dict[str, float]:
    return {
        str(g.get("key")): float(g.get("value") or 0)
        for g in body.get("groups") or []
        if isinstance(g, dict)
    }


def _detail(body: dict[str, Any]) -> tuple[str, str]:
    detail = body.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("code") or ""), str(detail.get("message") or "")
    return "", str(detail or "")


def check_step(step: Step, status: int, body: dict[str, Any]) -> list[str]:
    """What is wrong with the answer of one step, or an empty list."""
    bad: list[str] = []
    code, message = _detail(body)
    if any(w in message.lower() for w in CAST_WORDS):
        bad.append("the answer is a database cast error")
    if status != step.status:
        bad.append(f"status {status}, wanted {step.status}")
        return bad
    if step.status == 200:
        want = step.expect
        if "total" in want:
            got = body.get("total")
            if not isinstance(got, (int, float)) or not math.isclose(
                float(got), float(want["total"]), abs_tol=1e-9
            ):
                bad.append(f"total {got!r}, wanted {want['total']}")
        if "groups" in want:
            got_groups = _groups(body)
            wanted = {k: float(v) for k, v in want["groups"].items()}
            if got_groups.keys() != wanted.keys() or any(
                not math.isclose(got_groups[k], wanted[k], abs_tol=1e-9) for k in wanted
            ):
                bad.append(f"groups {got_groups}, wanted {wanted}")
        return bad
    if step.expect.get("code") and code != step.expect["code"]:
        bad.append(f"code {code!r}, wanted {step.expect['code']!r}")
    for word in step.expect.get("has", ()):
        if word not in message:
            bad.append(f"the refusal does not say {word!r}")
    order = [message.find(w) for w in step.expect.get("order", ())]
    if order and (-1 in order or order != sorted(order)):
        bad.append(f"the words {step.expect['order']} are missing or out of order")
    return bad


def good_answer(step: Step) -> tuple[int, dict[str, Any]]:
    """An answer that passes *step*, built from its expectation. The tests use it
    as the base of each mutation."""
    if step.status == 200:
        if "groups" in step.expect:
            return 200, {
                "groups": [
                    {"key": k, "label": k, "value": v} for k, v in step.expect["groups"].items()
                ]
            }
        return 200, {"total": step.expect["total"], "records": step.expect["total"]}
    words = [*step.expect.get("order", ()), *step.expect.get("has", ())]
    return step.status, {"detail": {"code": step.expect["code"], "message": " ... ".join(words)}}


# ── The tool text of the chat (CRM-Q34-3) ───────────────────────────────────


def check_zero_text(text: str) -> list[str]:
    """A total whose filters matched nothing says so, and never "no values recorded"."""
    bad = []
    if NO_MATCH_LINE not in text:
        bad.append(f"the zero does not say {NO_MATCH_LINE!r}")
    if "no values recorded" in text:
        bad.append("the zero says 'no values recorded'")
    return bad


def check_refusal_budget(outs: list[str], spent: int, answered_spent: int) -> list[str]:
    """Every refused total reads as a refusal and spends no total budget, and the
    next answered total spends one."""
    bad = []
    if not all(o.startswith("Refused") for o in outs):
        bad.append("a refused total did not read as a refusal")
    if spent != 0:
        bad.append(f"{spent} refused totals spent total budget, wanted 0")
    if answered_spent != 1:
        bad.append(f"the answered total spent {answered_spent}, wanted 1")
    return bad


def check_status_text(text: str, value: str) -> list[str]:
    """``status=`` on a module is refused with the working form."""
    bad = []
    if not text.startswith("Refused"):
        bad.append("the tool did not refuse a status on a module")
    form = f"field_filters {{'@status': '{value}'}}"
    if form not in text:
        bad.append(f"the refusal does not name {form}")
    return bad
