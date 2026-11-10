"""The logic of ``find_guide`` and ``show_guide`` (WS-OB GD-7).

Spec: ``project-docs/specs/assistant_guide.md`` §5.4. The two tools are the
orchestrator's own, in ``orchestrator/agents.py``, beside ``show_in_app``.
Their listing is dark behind ``GUIDE_ORGS`` (``acb_common.guide_flags``).

A guide is data (``acb_common.app_manifests.guide_manifest``). This module
decides which guides a member may run, ranks them against a question with a
plain token match (no model call), and starts one on the client.

A member may run a guide when ALL of these hold:

* its ``feature`` is null or one the member holds;
* an ``admin_only`` guide needs ``admin:members:manage``;
* every ``open`` step passes ``in_app_path.refusal``, so a guide never walks
  to a pane that the member lacks or that is dark (a preview pane).

``find_guide`` scores each ask of a guide on its own (:func:`ask_score`). An
ask matches when the question holds each of its subject words, and at least
``COVER`` of all its words. A generic verb (``GENERIC``) may be missing, a
generic verb matches another of its group, and a match on generic verbs only
finds nothing. An ask of one word matches only a question of that one word.

``show_guide`` shares ``queue._show_in_app_done`` with ``show_in_app``: the
page moves once a turn, whichever tool runs first. Each tool takes the mark
before its first await, because MAF can run two tool calls at once. The mark
reads ``in_app_path.MOVE_PENDING`` while its holder runs, and True when the
page moved. A refused call gives the mark back, so it does not spend the move.
Fence: ``tests/unit/test_guide_tools.py``.
"""
from __future__ import annotations

import functools
import re
from typing import Any

from acb_common import get_logger
from acb_common.app_manifests.guide_manifest import Guide, load_guides
from acb_common.guide_flags import guide_allowed

from orchestrator import in_app_path as _iap

_log = get_logger("orchestrator.guide_tools")

#: ``find_guide`` returns at most this many guides.
TOP = 3
NO_MATCH_HINT = "No guide matches. Offer to open the app."
#: An ask matches when the question holds at least this share of its words.
#: 0.6 lets one generic verb of a three-word ask be missing ("create a deal"
#: for "create a new deal"), and refuses a two-word ask with its verb missing
#: ("see a deal" for "add a deal").
COVER = 0.6

_STOPWORDS = frozenset(
    ["a", "an", "the", "i", "me", "my", "we", "you", "your", "to", "do", "does", "did", "how", "can", "could", "should", "would", "in", "on", "at", "of", "for", "with", "and", "or", "is", "are", "it", "this", "that", "please", "show", "tell", "where", "what", "all", "by"]
)
#: The verbs that many asks share, in groups of one meaning. :func:`tokens`
#: writes each one as the first verb of its group, so a verb matches another
#: of its group: "make a deal" finds "add a deal". A verb of another group does
#: not match, so "open a deal" does not find "add a deal".
_VERB_GROUPS = (
    ("add", "create", "make", "new"),
    ("see", "show", "open", "find", "get", "go"),
    ("move", "set"),
    ("give",),
    ("use",),
    ("do",),
)
_GROUP_OF = {verb: group[0] for group in _VERB_GROUPS for verb in group}
#: The generic verbs. An ask's other words are its subject words. A question
#: must hold each subject word of an ask. A generic verb may be missing, so
#: "create a deal" finds "create a new deal". A match on generic verbs only
#: finds nothing, so "add a task" does not find "add a lead".
GENERIC = frozenset(_GROUP_OF)
_WORD = re.compile(r"[a-z0-9]+")


def run_org() -> str | None:
    """The org of the run on this frame, from the server side only (R5)."""
    try:
        from acb_common.db import current_tenant

        org = current_tenant()
        if org:
            return str(org)
        from orchestrator.executor import _current_run_org

        return _current_run_org()
    except Exception:
        return None


def guide_enabled(org: str | None = None) -> bool:
    """True when ``GUIDE_ORGS`` opens the guide for the run's org. Fails closed.

    *org* is the org of the caller's server-side ``UserContext``. With none,
    the bound tenant and then the run's org answer, as for ``spawn_subagents``.
    """
    try:
        return guide_allowed(org or run_org())
    except Exception:
        return False


@functools.lru_cache(maxsize=1)
def all_guides() -> tuple[Guide, ...]:
    """Every guide, loaded once."""
    return load_guides()


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def tokens(text: str) -> frozenset[str]:
    """Lower case words, with no stopword and no one-letter word.

    A plural folds to its singular ("deals" is "deal"), and a generic verb to
    the first verb of its group ("make" is "add"). A one-letter word goes, so
    the "s" of "deal's" is no word.
    """
    out = (_stem(w) for w in _WORD.findall(text.lower()) if len(w) > 1 and w not in _STOPWORDS)
    return frozenset(_GROUP_OF.get(w, w) for w in out)


def ask_score(question: frozenset[str], ask: str) -> tuple[float, int]:
    """``(cover, matched)`` of one ask against the question's tokens.

    *cover* is the share of the ask's words that the question holds, and
    *matched* is their count. ``(0.0, 0)`` when the ask does not match: the
    question lacks a subject word of the ask, the ask has no subject word,
    *cover* is under ``COVER``, or the ask is one word and the question is more.

    An ask can keep one word only, such as "contact" of "where is a contact".
    That word alone says nothing of the act, so "delete a contact" must not
    find it. A one-word ask matches only a question of that one word.
    """
    words = tokens(ask)
    subject = words - GENERIC
    if not subject or not subject <= question:
        return (0.0, 0)
    if len(words) == 1 and len(question) > 1:
        return (0.0, 0)
    matched = len(words & question)
    cover = matched / len(words)
    return (round(cover, 3), matched) if cover >= COVER else (0.0, 0)


def score(question: str, guide: Guide) -> tuple[float, int]:
    """The best :func:`ask_score` of the guide's title and asks."""
    q = tokens(question)
    return max(ask_score(q, ask) for ask in (guide.title, *guide.asks))


def rank(question: str, guides: tuple[Guide, ...]) -> list[Guide]:
    """The guides with a matching ask, best first. At most ``TOP``.

    The order is the cover of the best ask, then its matched count, then
    file order.
    """
    scored = [(score(question, g), i, g) for i, g in enumerate(guides)]
    kept = [t for t in scored if t[0][1] > 0]
    kept.sort(key=lambda t: (-t[0][0], -t[0][1], t[1]))
    return [g for _s, _i, g in kept[:TOP]]


async def member_is_admin() -> bool:
    """True when the run's verified member holds the executor's admin right.

    The right is ``executor._FIRST_PARTY_ADMIN_PERMISSION``, the one admin
    check of the orchestrator.
    """
    try:
        from acb_auth import resolve_access

        from orchestrator.executor import _FIRST_PARTY_ADMIN_PERMISSION, _verified_run_member

        member = _verified_run_member()
        if not member:
            return False
        access = await resolve_access(member)
        return bool(getattr(access, "is_active", False)
                    and access.has(_FIRST_PARTY_ADMIN_PERMISSION))
    except Exception:
        return False


def may_run(guide: Guide, features: frozenset[str] | None, admin: bool) -> bool:
    """The rule in the module doc. *features* None means nobody is known."""
    if guide.feature is not None and (features is None or guide.feature not in features):
        return False
    if guide.admin_only and not admin:
        return False
    return all(
        _iap.refusal(step.path or "", features) is None
        for step in guide.steps
        if step.kind == "open"
    )


async def runnable_guides() -> list[Guide]:
    """The guides that the run's member may run, in file order."""
    features = await _iap.member_features()
    admin = await member_is_admin()
    return [g for g in all_guides() if may_run(g, features, admin)]


def steps_text(guide: Guide) -> str:
    """The ``say`` line of each step, numbered, one per line."""
    return "\n".join(f"{i}. {s.say}" for i, s in enumerate(guide.steps, 1))


async def find(question: str) -> dict[str, Any]:
    """The tool body of ``find_guide``."""
    question = str(question or "").strip()
    if not guide_enabled():
        return {"error": "no guides here"}
    best = rank(question, tuple(await runnable_guides()))
    if not best:
        # The question text stays out of the log: a member may type anything.
        _log.info("guide.find.no_match", org=run_org(), words=len(question.split()))
        return {"guides": [], "hint": NO_MATCH_HINT}
    return {"guides": [
        {"id": g.id, "title": g.title, "app": g.app, "steps": steps_text(g)} for g in best
    ]}


async def show(guide_id: str) -> dict[str, Any]:
    """The tool body of ``show_guide``."""
    from orchestrator.executor import resolve_run_queue

    guide_id = str(guide_id or "").strip()
    try:
        from acb_skills.agent_tools import _delegation_chain

        nested = bool(_delegation_chain.get())
    except ImportError:  # pragma: no cover — acb_skills ships with the platform
        nested = False
    if nested:
        return {"error": "only the member's own assistant starts a guide"}
    if not guide_enabled():
        return {"error": "no guides here"}
    guide = next((g for g in all_guides() if g.id == guide_id), None)
    if guide is None:
        return {"error": "no guide has that id"}
    queue = resolve_run_queue()
    if queue is None:
        return {"error": "no page to start a guide from here"}
    # The one page move of the turn, shared with show_in_app. The mark lives on
    # the queue because MAF copies the context per tool call (see show_in_app).
    mark = getattr(queue, "_show_in_app_done", False)
    if mark == _iap.MOVE_PENDING:
        return {"error": _iap.MOVE_PENDING_ERROR}
    if mark:
        return {"error": "already moved the page this turn"}
    # Take the mark before the first await. MAF can run two tool calls at once
    # (asyncio.gather), and the other call must see the mark. It is pending
    # until the check ends. A refusal gives it back, so a refused call does
    # not spend the move.
    queue._show_in_app_done = _iap.MOVE_PENDING  # type: ignore[attr-defined]
    features = await _iap.member_features()
    if not may_run(guide, features, await member_is_admin()):
        queue._show_in_app_done = False  # type: ignore[attr-defined]
        return {"error": "the member may not run that guide"}
    queue._show_in_app_done = True  # type: ignore[attr-defined]
    await queue.put({"type": "CUSTOM", "name": "guide", "value": {"guide_id": guide.id}})
    return {
        "started": guide.id,
        "note": "the guide starts on the page only while the member watches this chat, "
                "or when the member taps Show me",
    }
