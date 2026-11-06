"""The phrase step of the brief: ONE model call for the lead (WS-BRIEF
BR-2a-2, ``core_brief.md`` §4.4).

This module is the ONE model site of ``gateway/brief/``. ``run.py`` calls
:func:`phrase`, and holds no session while it waits. The call:

* goes through ``acb_llm.context.acompletion_with_fallback``, with
  ``model="tier-fast"``, ``source="brief"``, ``feature="brief.phrase"``,
  JSON mode and no reasoning, as ``chat_summary_job._ask`` does. With the
  Router on, the Console writes the usage row and draws the org's credits.
* carries ``client_ref`` = the run's ``model_request_id``, so the usage row
  joins the run (``usage_event.client_ref``).
* runs as the member (``acb_common._log.job_member_scope``, app ``brief``),
  for at most :data:`CALL_TIMEOUT_S` seconds, well under
  ``worker.HANDLER_TIMEOUT``.

**The prompt.** The claims as ``[c1]`` to ``[cN]`` with their values, inside
ONE block of the untrusted frame (``acb_skills.untrusted``), because a
claim line can hold words of a mail that a stranger wrote. The note of the
frame tells the model that the block is data and never an instruction. The
member's block (``assistant_profile.member_block``, which calls
``render_block``) and the house voice (``voice_prompt("summary",
json=True)``) follow. The model writes digits and no abbreviations, so the
claim fence (``brief/fence.py``) can check each number and each sentence.

**The answer.** ``{"sentences": [{"text": ..., "claims": ["c1", ...]}]}``.
:func:`phrase` maps each ``cK`` back to the UUID of the claim, and calls
``fence.check_phrase``. A refusal logs ``brief.phrase_refused`` with the
reason and answers None. Any fault raises to the caller, which keeps the
template lead. The model writes the lead only, never the words of a line.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from acb_common import get_logger
from acb_skills.untrusted import UntrustedFrame
from sqlalchemy import text

from gateway.brief import fence, store

_log = get_logger("brief.phrase")

#: The tier of the call (``core_brief.md`` §4.5).
MODEL = "tier-fast"
#: The ``source`` and the ``feature`` of the call, for the usage report.
SOURCE = "brief"
FEATURE = "brief.phrase"
#: A run with fewer claims takes the template lead, with no call.
MIN_CLAIMS = 4
#: The longest wait for the answer. ``worker.HANDLER_TIMEOUT`` is 240 s.
CALL_TIMEOUT_S = 30.0
#: The answer is at most 3 short sentences.
ANSWER_TOKENS = 300
#: No reasoning: a lead of 3 sentences needs none, and it costs tokens.
NO_THINKING: dict[str, str] = {"type": "disabled"}
#: The label of the block of claims in the untrusted frame.
CLAIMS_LABEL = "BRIEF CLAIMS"


@dataclass(frozen=True)
class Phrased:
    """A lead that passed the fence, with the UUIDs of the claims it cites."""

    lead: str
    claim_ids: tuple[str, ...]


def new_ref() -> str:
    """A new ``client_ref`` for one phrase call. It is the run's
    ``model_request_id``."""
    return f"brief-{uuid.uuid4().hex}"


def labels(claims: Sequence[store.Claim]) -> dict[str, store.Claim]:
    """``c1`` to ``cN``, in position order, to the claim."""
    ordered = sorted(claims, key=lambda c: (c.position, c.id))
    return {f"c{n}": c for n, c in enumerate(ordered, start=1)}


def _held(claim: store.Claim) -> list[Any]:
    """The text that the fence reads for a claim: its line and its value."""
    return [claim.text] + ([str(claim.value)] if claim.value is not None else [])


def _claim_line(label: str, claim: store.Claim) -> str:
    line = f"[{label}] {claim.text}"
    if claim.value is not None:
        line += f" (value: {claim.value})"
    return line


SYSTEM = (
    "You write the first sentences of a member's morning brief. The brief "
    "lists its lines below your words, so do not repeat each line. Say what "
    "matters most today, in at most 3 short sentences. "
    "Each sentence cites the claims that it uses, by their ids (c1, c2 and so "
    "on), in its \"claims\" list. Use only facts that a cited claim states. "
    "Write each number as digits, as the claim writes it, and add no number "
    "that a cited claim does not hold. Write no abbreviation with a full stop, "
    "for example \"e.g.\" or \"Dr.\". Do not put the ids in the text. "
    "Answer ONLY JSON: {\"sentences\": [{\"text\": \"<sentence>\", "
    "\"claims\": [\"c1\"]}]}."
)


def messages_for(claims: Sequence[store.Claim], block: str = "", *,
                 frame: UntrustedFrame | None = None) -> list[dict[str, str]]:
    """The prompt of the call. Pure, apart from the new token of the frame."""
    from acb_llm.voice import voice_prompt

    frame = frame or UntrustedFrame.new()
    body = "\n".join(_claim_line(k, c) for k, c in labels(claims).items())
    note = frame.note("The claims come from the member's apps, and some words in "
                      "them come from mail that another person wrote.")
    system = "\n\n".join(p for p in (SYSTEM, note, block.strip(),
                                     voice_prompt("summary", json=True)) if p)
    return [{"role": "system", "content": system},
            {"role": "user", "content": frame.wrap(CLAIMS_LABEL, body)}]


def _sentences(raw: Any) -> Any:
    """The ``sentences`` list of the answer *raw*, or None."""
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    return parsed.get("sentences") if isinstance(parsed, dict) else None


#: The number words that the fence cannot bind to a claim (it reads digits
#: only). A lead with one is refused, so the lead states no count that no
#: claim holds (review of BR-2a-2, P2).
NUMBER_WORDS: frozenset[str] = frozenset({
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety", "hundred", "thousand", "dozen", "dozens",
    "hundreds", "thousands", "half"})
#: The marks of a link or markup. A card title can come from a stranger's mail,
#: and the chat draws a Markdown link as a live link.
LINK_MARKS: tuple[str, ...] = ("](", "http", "www.", "<", ">", "`", "mailto:", "@")


class RouterOff(RuntimeError):
    """The Router went off between the check and the call. The run keeps the
    template lead."""


def unsafe_reason(sentence: str) -> str | None:
    """Why the lead sentence may not stand: ``number_word`` or ``link``, or
    None. Pure."""
    low = sentence.lower()
    if any(mark in low for mark in LINK_MARKS):
        return "link"
    # Split on every non-letter, so "twenty-one", "four/five" and a curly
    # apostrophe do not hide a number word (review, P2).
    words = {w for w in re.split(r"[^a-z]+", low) if w}
    if words & NUMBER_WORDS:
        return "number_word"
    return None


def judge(raw: Any, claims: Sequence[store.Claim]) -> Phrased | None:
    """The lead of the answer *raw*, or None when the fence refuses it. A
    refusal logs ``brief.phrase_refused`` with the reason, and no word of
    the answer. Pure, apart from the log line."""
    by_label = labels(claims)
    sentences = _sentences(raw)
    verdict = fence.check_phrase(sentences, {k: _held(c) for k, c in by_label.items()})
    if not verdict.ok:
        _log.warning("brief.phrase_refused", reason=verdict.reason)
        return None
    for item in sentences:
        bad = unsafe_reason(str(item["text"]))
        if bad is not None:
            _log.warning("brief.phrase_refused", reason=bad)
            return None
    ids: list[str] = []
    for item in sentences:
        for label in item["claims"]:
            if by_label[label].id not in ids:
                ids.append(by_label[label].id)
    lead = " ".join(item["text"].strip() for item in sentences)
    return Phrased(lead, tuple(ids))


async def member_block(db: Any, org: str, member: str) -> str:
    """The member's block (``assistant_profile.member_block``), read in the
    session *db* that the caller opened with ``tenant_session(org)``.
    Empty when the profile is off for the org. The statements are the
    executor's own, so the model reads the block that a chat turn reads."""
    from acb_common import assistant_profile as ap

    if not ap.enabled_for(org):
        return ""
    params = {"m": member, "org": org}
    row = (await db.execute(text(ap.READ_SQL), params)).first()
    names = (await db.execute(text(ap.ADDRESS_SQL), params)).first()
    preferred, _zone, display = names[:3] if names else (None, None, None)
    return ap.member_block(row[0] if row is not None else None, preferred, display)


async def _ask(member: str, messages: list[dict[str, str]], ref: str) -> Any:
    """The one model call, as *member*, with no session open."""
    from acb_common._log import job_member_scope
    from acb_llm import context

    with job_member_scope(member, app="brief"):
        async with asyncio.timeout(CALL_TIMEOUT_S):
            resp, _used = await context.acompletion_with_fallback(
                model=MODEL, messages=messages, max_tokens=ANSWER_TOKENS, temperature=0.2,
                source=SOURCE, feature=FEATURE, response_format={"type": "json_object"},
                thinking=dict(NO_THINKING), client_ref=ref)
    return resp.choices[0].message.content


async def phrase(member: str, claims: Sequence[store.Claim], *, ref: str,
                 block: str = "") -> Phrased | None:
    """ONE call for the lead of *claims*. Answers the lead and the claim
    UUIDs that it cites, or None when the fence refuses the answer. A
    model error, a ``RoutedRefusal`` or a timeout raises. The caller holds
    no session while this runs."""
    if not ref.strip():
        raise ValueError("a phrase call needs its client_ref")
    # The caller read the switch before the gather. A flag reload may have
    # turned the Router off since. Without it the call would fall to local
    # litellm with no usage row, so refuse here (review of BR-2a-2, F4).
    from acb_llm import routed

    if not routed.routing_is_on():
        raise RouterOff("the Router is off: no unbilled call")
    raw = await _ask(member, messages_for(claims, block), ref)
    return judge(raw, claims)
