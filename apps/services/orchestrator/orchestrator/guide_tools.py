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

``show_guide`` shares ``queue._show_in_app_done`` with ``show_in_app``: the
page moves once a turn, whichever tool runs first. A refused call does not
spend the move. Fence: ``tests/unit/test_guide_tools.py``.
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
#: The admin check of ``executor._FIRST_PARTY_ADMIN_PERMISSION``.
ADMIN_PERMISSION = "admin:members:manage"
NO_MATCH_HINT = "No guide matches. Offer to open the app."

_STOPWORDS = frozenset(
    ["a", "an", "the", "i", "me", "my", "we", "you", "your", "to", "do", "does", "did", "how", "can", "could", "should", "would", "in", "on", "at", "of", "for", "with", "and", "or", "is", "are", "it", "this", "that", "please", "show", "tell", "where", "what"]
)
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
    """Lower case words, with no stopword, a plural folded to its singular."""
    return frozenset(_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOPWORDS)


def score(question: str, guide: Guide) -> int:
    """The overlap of the question's tokens with the title and the asks."""
    return len(tokens(question) & tokens(" ".join((guide.title, *guide.asks))))


def rank(question: str, guides: tuple[Guide, ...]) -> list[Guide]:
    """The guides that share a token with the question, best first.

    A tie keeps file order. At most ``TOP``.
    """
    scored = [(score(question, g), i, g) for i, g in enumerate(guides)]
    scored = [t for t in scored if t[0] > 0]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [g for _s, _i, g in scored[:TOP]]


async def member_is_admin() -> bool:
    """True when the run's verified member holds ``admin:members:manage``."""
    try:
        from acb_auth import resolve_access

        from orchestrator.executor import _verified_run_member

        member = _verified_run_member()
        if not member:
            return False
        access = await resolve_access(member)
        return bool(getattr(access, "is_active", False) and access.has(ADMIN_PERMISSION))
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
    if getattr(queue, "_show_in_app_done", False):
        return {"error": "already moved the page this turn"}
    features = await _iap.member_features()
    if not may_run(guide, features, await member_is_admin()):
        return {"error": "the member may not run that guide"}
    queue._show_in_app_done = True  # type: ignore[attr-defined]
    await queue.put({"type": "CUSTOM", "name": "guide", "value": {"guide_id": guide.id}})
    return {
        "started": guide.id,
        "note": "the guide starts on the page only while the member watches this chat, "
                "or when the member taps Show me",
    }
