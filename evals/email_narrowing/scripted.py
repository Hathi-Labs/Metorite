"""The two paths of each question, as known tool sequences (WS-48 N2).

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §7.2.

A sequence is what a model would ask for. :class:`Session` plays it: each
:meth:`Session.turn` is ONE model request. The request carries the whole
conversation so far, as a chat completion does, so its prompt grows with each
tool result. The session calls the REAL agent tools, and the stub Router
counts each request on its tier.

* **Before** (today): ``query_inbox`` with the question's words and filters,
  then ``read_email`` for each email that it lists, :data:`READS_PER_TURN` to
  a request, then the answer.
* **After**: ONE ``narrow_and_read`` call, then the answer.

⚠️ **The before path is an assumption.** Nobody measured how many reads
today's model asks for in one request. :data:`READS_PER_TURN` is 5. The
runner also plays every read in ONE request, the best case for today, and
reports that ratio beside the gated one. Only a live sweep measures what a
model does.

⚠️ **The prompt holds the agent's instructions and its own tool schemas**
(the real ``to_json_schema_spec`` of each tool that ``build_agents`` gives).
It does not hold the platform tools or the addendum that the executor adds.
Each request of both paths carries that same missing part, so the estimate is
low for both paths. The ratio is low by less than the totals.

The tier of each request follows ``tier_policy``: the agent's own tier
(``tier-balanced``), and after a tool of ``TOOL_HINTS`` the tier of its kind.
So the answer after ``narrow_and_read`` goes to ``tier-powerful``, as for an
agent that ``AI_TIER_ROUTING`` covers.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from evals.email_narrowing.dataset import Dataset, Question
from evals.email_narrowing.stub_api import StubRouter, estimate_tokens

#: The reads that today's model asks for in one request (an assumption).
READS_PER_TURN = 5
#: ``query_inbox`` lists at most 100 rows.
INBOX_LIMIT = 100

_ID = re.compile(r"id=([0-9a-f-]{36})")
_ITEM = re.compile(r"^--- item ([0-9a-f-]{36}) \|", re.MULTILINE)


def tier_after(previous_calls: list[str]) -> str:
    """The tier of the next request, as ``tier_policy`` picks it."""
    from acb_skills import tier_policy

    for name in reversed(previous_calls):
        kind = tier_policy.TOOL_HINTS.get(name)
        if kind:
            return tier_policy.KIND_TIERS.get(kind) or tier_policy.DEFAULT_TIER
    return tier_policy.DEFAULT_TIER


@dataclass
class Session:
    """One chat turn of the agent, played request by request."""

    system: str
    schemas: str
    prompt: str
    tools: Mapping[str, Callable[..., Awaitable[str]]]
    router: StubRouter
    history_chars: int = 0
    previous: list[str] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.history_chars = len(self.system) + len(self.schemas) + len(self.prompt)

    def _request(self, completion: str) -> None:
        tier = tier_after(self.previous)
        self.router.add(tier, estimate_tokens(self.history_chars), estimate_tokens(len(completion)))
        self.history_chars += len(completion)

    async def turn(self, calls: list[tuple[str, dict[str, Any]]]) -> list[str]:
        """ONE model request that asks for *calls*, then the tool results."""
        self._request(json.dumps([{"name": n, "arguments": a} for n, a in calls]))
        results: list[str] = []
        for name, arguments in calls:
            result = await self.tools[name](**arguments)
            results.append(result)
            self.history_chars += len(result)
            self.calls.append({"name": name, "arguments": arguments, "result_chars": len(result)})
        self.previous = [n for n, _a in calls]
        return results

    def answer(self, text: str) -> None:
        """The last request: the model writes the answer."""
        self._request(text)
        self.previous = []


def _answer(ds: Dataset, q: Question, ids: list[str], checked: str) -> str:
    """A scripted answer: the senders of the answering mail that the path read."""
    names = []
    for message_id in ids:
        m = ds.by_id(message_id)
        if m and q.id in m.get("answers", []):
            names.append(f"- {m['from_address']['name']}: {m['subject']}")
    lead = f"{checked} {len(names)} of them answer your question."
    return "\n".join([lead, *names])


async def play_before(
    session: Session, ds: Dataset, q: Question, *, reads_per_turn: int = READS_PER_TURN,
) -> list[str]:
    """Today's path. Returns the ids that it read in full."""
    args = {"account_id": "", "limit": INBOX_LIMIT, **q.inbox_args()}
    [listing] = await session.turn([("query_inbox", args)])
    ids = _ID.findall(listing)
    per_turn = max(1, reads_per_turn if reads_per_turn > 0 else len(ids) or 1)
    for start in range(0, len(ids), per_turn):
        batch = ids[start:start + per_turn]
        await session.turn([("read_email", {"email_id": i}) for i in batch])
    session.answer(_answer(ds, q, ids, f"I read {len(ids)} emails."))
    return ids


async def play_after(session: Session, ds: Dataset, q: Question) -> tuple[list[str], str]:
    """The narrowing path. Returns the ids that it read in full, and the tool output."""
    filters = json.dumps(q.narrow_filters())
    [out] = await session.turn([("narrow_and_read", {"query": q.spec.prompt, "filters": filters})])
    ids = _ITEM.findall(out)
    first = out.splitlines()[0] if out else ""
    session.answer(_answer(ds, q, ids, first))
    return ids, out


def turns_of(found: int, reads_per_turn: int) -> int:
    """The requests of the before path for *found* listed emails."""
    per_turn = max(1, reads_per_turn if reads_per_turn > 0 else found or 1)
    return 2 + math.ceil(found / per_turn)
