"""The scoring of the proposer eval (WS-ALN ALN-5b).

Spec: ``project-docs/specs/project_alignment.md`` §4.2 and the ALN-5b row of
§10.

:func:`score` reads the result of each case of one run. Six bars:

* ``quotes``: each proposal that stands quotes the page exactly. This check
  is its own plain substring test after one form of line end, so a defect in
  ``knowledge.core.quote_occurs`` cannot pass it. Each quote holds
  ``MIN_QUOTE`` characters or more.
* ``cap``: a case keeps at most ``PAGE_CAP`` proposals less the proposals
  that already wait on the page version.
* ``no_tools``: no request of the run holds ``tools``, ``tool_choice``,
  ``functions``, ``function_call`` or ``parallel_tool_calls``.
* ``framed``: the user message of each request is ONE block of the
  untrusted frame, and the system message holds the note of that frame.
* ``one_call``: each case made exactly one request.
* ``expected``: the count of kept proposals and each count of a drop equal
  the case. A scripted run replays answers that an agent wrote, so this bar
  must be exact.

The report counts proposals and reasons. It names no person (ALN-Q5).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The keys of a request that would let the model call a tool.
TOOL_KEYS: frozenset[str] = frozenset({
    "tools", "tool_choice", "functions", "function_call", "parallel_tool_calls",
})


@dataclass
class CaseResult:
    """What one case of a run did."""

    id: str
    text: str
    kept: list[tuple[str, str]] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)
    unreadable: bool = False
    waiting: int = 0
    requests: list[dict[str, Any]] = field(default_factory=list)
    token: str = ""


@dataclass
class Score:
    """The counts of one run, and the result of each bar."""

    scripted: bool = False
    cases: int = 0
    proposed: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    misses: dict[str, list[str]] = field(default_factory=dict)

    def miss(self, bar: str, what: str) -> None:
        self.misses.setdefault(bar, []).append(what)

    def bars(self) -> dict[str, bool]:
        names = ("quotes", "cap", "no_tools", "framed", "one_call")
        out = {name: not self.misses.get(name) for name in names}
        if self.scripted:
            out["expected"] = not self.misses.get("expected")
        return out

    def passed(self) -> bool:
        return all(self.bars().values())

    def summary(self) -> dict[str, Any]:
        return {"cases": self.cases, "proposed": self.proposed, "dropped": self.dropped,
                "bars": self.bars(), "passed": self.passed(), "misses": self.misses}


def _lf(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _check_quotes(result: CaseResult, score: Score, min_quote: int) -> None:
    page = _lf(result.text)
    for sentence, quote in result.kept:
        if len(quote) < min_quote or _lf(quote) not in page:
            score.miss("quotes", f"{result.id}: {sentence[:40]}")


def _check_requests(result: CaseResult, score: Score) -> None:
    if len(result.requests) != 1:
        score.miss("one_call", f"{result.id}: {len(result.requests)} requests")
    for request in result.requests:
        held = sorted(TOOL_KEYS & set(request))
        if held:
            score.miss("no_tools", f"{result.id}: {held}")
        messages = request.get("messages") or []
        if len(messages) != 2 or not result.token:
            score.miss("framed", f"{result.id}: {len(messages)} messages")
            continue
        system, user = str(messages[0]["content"]), str(messages[1]["content"])
        lines = user.split("\n")
        opens = lines[0].startswith("<<<") and lines[0].endswith(f" {result.token}>>>")
        closes = lines[-1].startswith("<<<END ") and lines[-1].endswith(f" {result.token}>>>")
        if not (opens and closes and user.count(result.token) == 2
                and result.token in system):
            score.miss("framed", result.id)


def _check_expected(result: CaseResult, expect: dict[str, Any], score: Score) -> None:
    if bool(expect.get("unreadable")) != result.unreadable:
        score.miss("expected", f"{result.id}: unreadable={result.unreadable}")
        return
    if result.unreadable:
        return
    if len(result.kept) != int(expect.get("kept", 0)):
        score.miss("expected", f"{result.id}: kept {len(result.kept)}")
    want = {k: v for k, v in dict(expect.get("dropped") or {}).items() if v}
    got = {k: v for k, v in result.dropped.items() if v}
    if want != got:
        score.miss("expected", f"{result.id}: dropped {got}")


def score(results: list[CaseResult], expects: dict[str, dict[str, Any]], *,
          page_cap: int, min_quote: int, scripted: bool = False) -> Score:
    """Score each case of one run."""
    out = Score(scripted=scripted)
    for result in results:
        out.cases += 1
        out.proposed += len(result.kept)
        for reason, count in result.dropped.items():
            out.dropped[reason] = out.dropped.get(reason, 0) + int(count)
        _check_quotes(result, out, min_quote)
        if len(result.kept) + result.waiting > page_cap and result.kept:
            out.miss("cap", f"{result.id}: {len(result.kept)} kept, {result.waiting} waiting")
        _check_requests(result, out)
        if scripted:
            _check_expected(result, expects.get(result.id, {}), out)
    return out
