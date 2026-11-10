"""``POST /project-rules/projects/{id}/propose`` — Mira proposes the rules of
one pinned page (WS-ALN ALN-5b).

Spec: ``project-docs/specs/project_alignment.md`` §4.2 rules 2, 4 and 5, and
the ALN-5b row of §10 (D160).

A lead asks for the proposals of one page. Mira reads the page in ONE model
call with no tools, and answers each rule with the exact words of the page
that state it. The server checks each quote, drops each answer that fails,
and writes each one that stands as a rule row of ``state = 'proposed'`` and
``proposed_by = 'mira'``. A lead accepts each proposal through the accept
route of ALN-5a (``routes/project_rules.py``). Nothing here makes a rule
``active`` (D104).

**The trigger.** A lead calls the route. There is no job and no event. The
page signal is ALN-11. The route has no UI and no chat tool (ALN-5c).

**The source.** Only a page that is pinned to this project
(``pm_project_authorities``, state ``active``) is a source.
``routes/projects/authorities.py`` stays the one writer of a pin. The page
must be readable by the caller (:func:`knowledge.core.load_page`), published,
and not in a ``me`` space. Each holder of the project must read the page too:
the route runs the audience check of ALN-4
(``authorities._refuse_if_narrower``) again before the call and again under
the lock. A move of the page writes a version with the same text, so the pin
stays current, and only this check finds that the page got narrower. Mira
reads the NEWEST version of the page, and each proposal names that version.

**The call.** :func:`_ask` calls ``acb_llm.context.acompletion_with_fallback``
with ``model="tier-fast"``, JSON mode and no reasoning, as
``brief/phrase.py`` does. It runs as the caller
(``acb_common._log.job_member_scope``, app ``projects``), so the org's AI
allowance pays (ALN-Q4). It runs only with ``routed.routing_is_on()``, so a
box with the Router off makes no unbilled call. The page reaches the model in
ONE block of the untrusted frame (``acb_skills.untrusted``). The call passes
no ``tools`` and no ``tool_choice``, so the model can call nothing. No
session is open during the call: the route opens a session, reads, closes
it, calls the model, then opens a second session to write.

**The checks** (:func:`judge`, then :func:`fit`). The answer is cut to
:data:`PAGE_CAP` items first. Each sentence holds 1 to ``BODY_MAX``
characters after a strip. Each quote holds :data:`MIN_QUOTE` to
``QUOTE_MAX`` characters after a strip, and must be an exact substring of
the page version (:func:`knowledge.core.quote_occurs`, one form of line end).
An item that fails is dropped and never repaired. A sentence that equals an
active or proposed rule of the project (case and spaces folded) is a
duplicate. So is a sentence of a retired rule that Mira proposed from the
same page, so a retired proposal does not come back. The cap counts each rule
that Mira proposed from the same page version, in each state: an accept or a
retire does not make room. The route refuses a full version before the call,
and under the lock it writes at most ``PAGE_CAP`` less that count.

**The write.** One transaction under the advisory lock of the project
(``project_rules._lock_project``), so two calls cannot pass the cap or
write one sentence twice. Under the lock it checks the page again: still
pinned, published, readable by the caller and not narrower than the project.
Then it reads the text of the version that Mira read, and checks each quote
against it. An edit during the call keeps the answer that the org paid for:
each row names the version that Mira read. A page that left the pin, was
unpublished, got narrower or became unreadable gives a refusal and no row.
Each row goes through ``project_rules._insert``, the one writer of a rule row.

**The answer.** ``{"proposed": [...], "dropped": {<reason>: <count>}}``.
The counts are for each reason, never for each person (ALN-Q5).

The tenant and the actor come from the session (R5). The wire model forbids
extra keys, so a body that names an org or a member gets 422.

Fences: ``tests/unit/test_project_rules_propose.py`` (R8) and
``tests/unit/test_project_rules_static.py``. Eval:
``evals/project_rules_proposer/``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from acb_auth import UserContext, get_current_user, require_feature_router
from acb_common import get_logger
from acb_common.pg_text import storable
from acb_llm import routed
from acb_llm.voice import voice_prompt
from acb_skills.untrusted import UntrustedFrame
from fastapi import APIRouter, Depends, HTTPException
from gateway.db import tenant_session as _tenant_session
from gateway.routes import project_rules as rules
from gateway.routes.projects import authorities as pins
from gateway.routes.projects.core import actor
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

_log = get_logger("gateway.project_rules_propose")

#: The tier of the call. ``tier-fast`` first, as ``brief/phrase.py``. A
#: measure may move it to ``tier-balanced`` later (§10, ALN-5b).
MODEL = "tier-fast"
#: The ``source``, the ``feature`` and the app of the call, for the usage row.
SOURCE = "projects"
FEATURE = "projects.rules_propose"
APP = "projects"
#: The most proposals of one page version (§4.2 rule 4), in each state.
PAGE_CAP = 12
#: The shortest quote. A quote of a few characters proves nothing (§4.2).
MIN_QUOTE = 20
#: The longest page that one call reads. A longer page gives 422 and no call.
PAGE_MAX_CHARS = 60_000
#: The longest wait for the answer.
CALL_TIMEOUT_S = 30.0
#: Twelve rules of a sentence and a quote each fit in this, with room for
#: the JSON. The org pays only for the tokens that the model writes.
ANSWER_TOKENS = 6000
#: No reasoning. ``tier-fast`` thinks by default, and the reasoning spends the
#: cap of the answer (see ``chat_summary_job.NO_THINKING``).
NO_THINKING: dict[str, str] = {"type": "disabled"}
#: The label of the page block in the untrusted frame.
PAGE_LABEL = "KNOWLEDGE PAGE"
#: The writer of each proposal (``pm_agent_guidance.proposed_by``).
PROPOSER = "mira"

#: The reasons of a dropped item, in the order of the answer.
REASONS: tuple[str, ...] = (
    "no_sentence", "no_quote", "too_short", "too_long", "duplicate", "over_cap",
)

NOT_PINNED = (
    "This page is not pinned to this project. Pin the page first, then ask "
    "Mira again."
)
PAGE_EMPTY = (
    "This page has no text yet, so Mira has nothing to propose. Add the rules "
    "to the page, then ask again."
)
PAGE_TOO_LONG = (
    f"This page has more than {PAGE_MAX_CHARS:,} characters, so Mira cannot "
    "read it in one pass. Split the page, then ask again."
)
PAGE_FULL = (
    f"Mira already proposed {PAGE_CAP} rules from this version of the page. "
    "That is the most for one version, so Mira made no new call. Write a rule "
    "yourself, or ask again after the page changes."
)
PAGE_CHANGED = (
    "The page moved or changed while Mira read it, so no proposals were "
    "saved. Ask again to read the page as it is now."
)
ROUTER_OFF = (
    "The AI service is off on this server, so Mira made no proposals and "
    "nothing was charged. Try again later, or ask an admin."
)
NO_CREDITS = (
    "Your organization has no AI credits left, so Mira made no proposals. "
    "An admin can add credits."
)
ROUTER_REFUSED = (
    "The AI service refused the request, so Mira made no proposals. Try "
    "again later."
)
TIMED_OUT = "Mira took too long to read the page, so no proposals were saved. Try again."
CALL_FAILED = (
    "Mira could not read the page just now, so no proposals were saved. Try "
    "again later."
)
UNREADABLE = (
    "Mira gave an answer that could not be read, so no proposals were saved. "
    "Try again."
)

#: The flag of ALN-5a first, so a dark route answers 404. Then the feature
#: gate of the Projects router.
router = APIRouter(
    prefix="/project-rules", tags=["project-rules"],
    dependencies=[Depends(rules.require_project_rules), require_feature_router("projects")],
)


class ProposeIn(BaseModel):
    """The page to read. The tenant and the caller come from the session."""

    model_config = ConfigDict(extra="forbid")

    page_id: str = Field(min_length=1, max_length=64)


@dataclass(frozen=True)
class Source:
    """The page version that the call reads, with LF line ends."""

    page_id: str
    version: int
    title: str
    text: str


@dataclass(frozen=True)
class Proposal:
    """One rule that passed the checks."""

    sentence: str
    quote: str


@dataclass(frozen=True)
class Current:
    """The rules of the project now. *sentences* holds the folded sentence of
    each active or proposed rule, and of each retired rule that Mira proposed
    from the source page. *made* counts the rules that Mira proposed from the
    source page version, in each state."""

    sentences: frozenset[str] = frozenset()
    made: int = 0


@dataclass
class Judged:
    """The proposals that stand, and the count of each reason of a drop."""

    kept: list[Proposal] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=lambda: dict.fromkeys(REASONS, 0))


class UnreadableAnswer(ValueError):
    """The answer is not JSON of the shape ``{"rules": [...]}``."""


# ── The prompt ──────────────────────────────────────────────────────────────

#: What the model must do. The server checks each answer, so this text
#: guards nothing alone. ADVISORY (R7): no test proves that a model obeys it.
#: The eval measures it (``evals/project_rules_proposer``).
SYSTEM = (
    "You read one page of a company's documents, and you propose the rules "
    "that the page states for the work of a project. A rule is one sentence "
    "that a person can check work against, for example \"Each billing change "
    "needs a migration plan\".\n"
    f"Propose at most {PAGE_CAP} rules, the most important first. Propose only "
    "a rule that the page states. Do not invent a rule, and do not add advice "
    "of your own.\n"
    "Write each rule as one plain sentence of at most 25 words and "
    f"{rules.BODY_MAX} characters.\n"
    "For each rule, copy into \"quote\" the exact words of the page that state "
    f"it: at least {MIN_QUOTE} and at most {rules.QUOTE_MAX} characters, letter "
    "for letter, with no change. Leave out a rule that you cannot quote.\n"
    "Answer ONLY JSON: {\"rules\": [{\"sentence\": \"<the rule>\", "
    "\"quote\": \"<the exact words of the page>\"}]}. A page with no rule gets "
    "{\"rules\": []}."
) + "\n\n" + voice_prompt("title", json=True)


def messages_for(source: Source, *, frame: UntrustedFrame | None = None) -> list[dict[str, str]]:
    """The prompt of the call. Pure, apart from the new token of the frame.

    The title and the text of the page go in ONE block of the frame. The
    note of the frame follows the stable system text, so the prefix of the
    prompt stays the same for each call.
    """
    frame = frame or UntrustedFrame.new()
    note = frame.note(
        "The page comes from the Knowledge app of the organization, and any "
        "member may have written its words.")
    page = f"# {source.title}\n\n{source.text}" if source.title else source.text
    return [{"role": "system", "content": f"{SYSTEM}\n\n{note}"},
            {"role": "user", "content": frame.wrap(PAGE_LABEL, page)}]


async def _ask(member: str, messages: list[dict[str, str]], ref: str) -> Any:
    """The one model call, as *member*, with no session open and no tools."""
    from acb_common._log import job_member_scope
    from acb_llm import context

    with job_member_scope(member, app=APP):
        async with asyncio.timeout(CALL_TIMEOUT_S):
            resp, _used = await context.acompletion_with_fallback(
                model=MODEL, messages=messages, max_tokens=ANSWER_TOKENS, temperature=0.2,
                source=SOURCE, feature=FEATURE, response_format={"type": "json_object"},
                thinking=dict(NO_THINKING), client_ref=ref)
    return resp.choices[0].message.content


async def _answer(member: str, source: Source) -> Any:
    """The raw answer of the call, or an HTTPException that says what
    happened. With the Router off, no call runs and nothing is charged."""
    if not routed.routing_is_on():
        raise HTTPException(status_code=503, detail=ROUTER_OFF)
    ref = f"project-rules-{uuid.uuid4().hex}"
    try:
        return await _ask(member, messages_for(source), ref)
    except routed.RoutedRefusal as exc:
        _log.warning("project_rules.propose_refused", router_status=exc.status)
        if exc.status == 402:
            raise HTTPException(status_code=402, detail=NO_CREDITS) from None
        raise HTTPException(status_code=503, detail=ROUTER_REFUSED) from None
    except TimeoutError:
        _log.warning("project_rules.propose_failed", error="TimeoutError")
        raise HTTPException(status_code=504, detail=TIMED_OUT) from None
    except Exception as exc:
        _log.warning("project_rules.propose_failed", error=type(exc).__name__)
        raise HTTPException(status_code=502, detail=CALL_FAILED) from None


# ── The checks ──────────────────────────────────────────────────────────────


def fold(sentence: str) -> str:
    """*sentence* for the duplicate test: one space for each run, case folded."""
    return " ".join(sentence.split()).casefold()


def _items(raw: Any) -> list[Any]:
    """The ``rules`` list of the answer *raw*, or :class:`UnreadableAnswer`."""
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise UnreadableAnswer("the answer is not JSON") from exc
    items = parsed.get("rules") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        raise UnreadableAnswer("the answer has no rules list")
    return items


def _check(item: Any, page_text: str) -> tuple[str | None, Proposal | None]:
    """``(reason, None)`` for an item that fails, else ``(None, proposal)``."""
    kb = rules._kb()
    sentence = item.get("sentence") if isinstance(item, dict) else None
    quote = item.get("quote") if isinstance(item, dict) else None
    # A NUL or a lone surrogate cannot be stored. Drop, never repair.
    if not isinstance(sentence, str) or storable(sentence) != sentence or not sentence.strip():
        return "no_sentence", None
    sentence = sentence.strip()
    if len(sentence) > rules.BODY_MAX:
        return "too_long", None
    if not isinstance(quote, str) or not quote.strip():
        return "no_quote", None
    quote = kb.same_line_ends(quote.strip())
    if len(quote) < MIN_QUOTE:
        return "too_short", None
    if len(quote) > rules.QUOTE_MAX:
        return "too_long", None
    if not kb.quote_occurs(quote, page_text):
        return "no_quote", None
    return None, Proposal(sentence, quote)


def judge(raw: Any, page_text: str) -> Judged:
    """The proposals of the answer *raw* that pass each check. Pure.

    The answer is cut to :data:`PAGE_CAP` items first, and each item past the
    cut counts as ``over_cap``. A second item with the same sentence counts
    as ``duplicate``. Raises :class:`UnreadableAnswer` for an answer that
    does not parse.
    """
    items = _items(raw)
    out = Judged()
    out.dropped["over_cap"] = max(0, len(items) - PAGE_CAP)
    seen: set[str] = set()
    for item in items[:PAGE_CAP]:
        reason, proposal = _check(item, page_text)
        if proposal is None:
            out.dropped[str(reason)] += 1
            continue
        if fold(proposal.sentence) in seen:
            out.dropped["duplicate"] += 1
            continue
        seen.add(fold(proposal.sentence))
        out.kept.append(proposal)
    return out


def fit(judged: Judged, current: Current) -> Judged:
    """The proposals of *judged* that the project takes now. Pure.

    A sentence of :attr:`Current.sentences` is a ``duplicate``. The rest
    fill the room of the page version, :data:`PAGE_CAP` less the rules that
    Mira proposed from it, and each one past the room is ``over_cap``.
    """
    out = Judged(dropped=dict(judged.dropped))
    room = max(0, PAGE_CAP - current.made)
    for proposal in judged.kept:
        if fold(proposal.sentence) in current.sentences:
            out.dropped["duplicate"] += 1
        elif len(out.kept) >= room:
            out.dropped["over_cap"] += 1
        else:
            out.kept.append(proposal)
    return out


def hold(judged: Judged, page_text: str) -> Judged:
    """The proposals of *judged* whose quote occurs in *page_text*. Pure.

    The write reads the text of the version again under the lock, and each
    quote must hold in it. A quote that does not hold counts as ``no_quote``.
    """
    out = Judged(dropped=dict(judged.dropped))
    kb = rules._kb()
    for proposal in judged.kept:
        if kb.quote_occurs(proposal.quote, page_text):
            out.kept.append(proposal)
        else:
            out.dropped["no_quote"] += 1
    return out


# ── The reads ───────────────────────────────────────────────────────────────

#: An active pin of the page on the project. ``authorities.py`` writes it.
_PINNED_SQL = """
SELECT 1 FROM pm_project_authorities
 WHERE project_id = CAST(:pid AS uuid) AND page_id = CAST(:page AS uuid)
   AND state = 'active'
 LIMIT 1
"""

#: The newest version of each rule of one project that is active or
#: proposed, or that Mira proposed from the page. The WHERE on the writer
#: picks one writer, and groups nothing by a person (ALN-Q5).
_CURRENT_SQL = """
SELECT cur.body, cur.state, cur.proposed_by, cur.source_page_id,
       cur.source_page_version FROM (
    SELECT DISTINCT ON (g.rule_key) g.body, g.state, g.proposed_by,
           g.source_page_id, g.source_page_version
      FROM pm_agent_guidance g
     WHERE g.kind = 'rule' AND g.project_id = CAST(:pid AS uuid)
     ORDER BY g.rule_key, g.version DESC
) cur
 WHERE cur.state IN ('active', 'proposed')
    OR (cur.proposed_by = :mira AND cur.source_page_id = CAST(:page AS uuid))
"""


async def _source_page(
    db: Any, user: UserContext, project: Any, page_id: str,
) -> tuple[Any, Any]:
    """The page and its space, when the page may be a source of the project.

    404 for a page that the caller cannot see (the same 404 as an unknown
    page). 422 for a page that is not pinned to the project. 409 for a page
    that is not published, is in a ``me`` space, or is narrower than the
    project. The pin ran the audience check of ALN-4 once. A later move of
    the page does not make the pin stale, so this runs the check again.
    """
    kb = rules._kb()
    page = await kb.load_page(db, user, page_id)
    if page is None:
        raise kb.not_found()
    pinned = (await db.execute(
        text(_PINNED_SQL), {"pid": str(project.id), "page": str(page.id)})).first()
    if pinned is None:
        raise HTTPException(status_code=422, detail=NOT_PINNED)
    if str(page.state) != "published":
        raise HTTPException(status_code=409, detail=rules.NOT_PUBLISHED_REFUSED)
    space = await kb.load_space(db, user, str(page.space_id))
    if space is None:
        raise kb.not_found()
    if str(space.kind) == "me":
        raise HTTPException(status_code=409, detail=rules.PERSONAL_PAGE_REFUSED)
    await pins._refuse_if_narrower(db, user, project, str(page.id))
    return page, space


async def _read_source(
    db: Any, user: UserContext, project: Any, page_id: str,
) -> Source:
    """The newest version of a pinned page that the caller reads.

    The refusals of :func:`_source_page`, and 422 for a page with no text or
    too much text.
    """
    kb = rules._kb()
    page, space = await _source_page(db, user, project, page_id)
    version = await kb.latest_version(db, user, str(page.id))
    body = await kb.page_version_text(db, user, page, version, space=space)
    if body is None:
        raise kb.not_found()
    if not body.strip():
        raise HTTPException(status_code=422, detail=PAGE_EMPTY)
    if len(body) > PAGE_MAX_CHARS:
        raise HTTPException(status_code=422, detail=PAGE_TOO_LONG)
    return Source(str(page.id), int(version), str(page.title or ""), kb.same_line_ends(body))


async def _current(db: Any, project_id: str, source: Source) -> Current:
    """The rules of the project now, for the duplicate test and the room."""
    rows = (await db.execute(text(_CURRENT_SQL), {
        "pid": project_id, "mira": PROPOSER, "page": source.page_id})).fetchall()
    made = sum(
        1 for r in rows
        if r.proposed_by == PROPOSER and r.source_page_id is not None
        and str(r.source_page_id) == source.page_id
        and r.source_page_version is not None
        and int(r.source_page_version) == source.version
    )
    return Current(frozenset(fold(str(r.body)) for r in rows), made)


# ── The route ───────────────────────────────────────────────────────────────


async def _write(
    user: UserContext, project_id: str, source: Source, judged: Judged,
) -> dict[str, Any]:
    """Write each proposal that fits, in one transaction under the lock.

    The page must still be a source (:func:`_source_page`). The rows name
    the version that Mira read, and each quote must hold in its text. A
    version that the caller cannot read now, as after a move to another
    space, gives 409 and no row.
    """
    me = actor(user).strip().lower()
    kb = rules._kb()
    async with _tenant_session() as db:
        project = await rules._writable_project(db, user, project_id)
        await rules._lock_project(db, project_id)
        page, space = await _source_page(db, user, project, source.page_id)
        body = await kb.page_version_text(db, user, page, source.version, space=space)
        if body is None:
            raise HTTPException(status_code=409, detail=PAGE_CHANGED)
        held = hold(judged, kb.same_line_ends(body))
        fitted = fit(held, await _current(db, project_id, source))
        rows = [
            await rules._insert(db, pid=project_id, key=str(uuid.uuid4()), me=me, fields={
                "body": p.sentence, "page": source.page_id, "page_version": source.version,
                "quote": p.quote, "level": "soft", "state": "proposed",
                "proposed_by": PROPOSER,
            })
            for p in fitted.kept
        ]
        readable = await rules._readable_sources(db, user, rows)
    _log.info("project_rules.proposed", kept=len(rows), dropped=fitted.dropped)
    return {"proposed": [rules._rule_out(r, readable) for r in rows],
            "dropped": fitted.dropped}


@router.post("/projects/{project_id}/propose")
async def propose_rules(
    project_id: str, payload: ProposeIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Mira proposes the rules of one pinned page of the project.

    A lead (or a Projects admin) calls it. 403 for an agent connection and
    for a caller who is not a lead, 409 for an archived project, and the
    refusals of :func:`_read_source` for the page. 409 when the page version
    holds :data:`PAGE_CAP` rules that Mira proposed, in each state, with no
    call. 503 with the Router off, with no call. Each new row is ``proposed``.
    """
    rules._refuse_agent(user)
    pid = rules._uuid_or_404(project_id, "Project")
    async with _tenant_session() as db:
        project = await rules._writable_project(db, user, pid)
        source = await _read_source(db, user, project, payload.page_id)
        if (await _current(db, pid, source)).made >= PAGE_CAP:
            raise HTTPException(status_code=409, detail=PAGE_FULL)
    # No session is open from here to the write.
    raw = await _answer(actor(user).strip().lower(), source)
    try:
        judged = judge(raw, source.text)
    except UnreadableAnswer:
        _log.warning("project_rules.propose_failed", error="UnreadableAnswer")
        raise HTTPException(status_code=502, detail=UNREADABLE) from None
    return await _write(user, pid, source, judged)
