"""``find_guide`` and ``show_guide``: the chat finds a guide and shows it (WS-OB GD-7).

Spec: ``project-docs/specs/assistant_guide.md`` §5.4. The tools are the
orchestrator's own (``orchestrator/agents.py``), and the logic is
``orchestrator/guide_tools.py``. They list only when ``GUIDE_ORGS`` opens the
org, and they never call a model.

The questions that must find a guide, and those that must find none, are one
table (``test_the_question_table``). A second call that meets a move still in
progress gets the words of ``in_app_path.MOVE_PENDING_ERROR``.

Each refusal has a test: a pane the member lacks, a guide feature the member
lacks (a guide with no ``open`` step), an admin guide for a member,
a preview pane (through a fixture guide), an unknown id, a nested run, and a
second page move in one turn, in both orders with ``show_in_app``. Two calls
that run at once (``asyncio.gather``) move the page once.

The ranking has a test on the real catalog: a match on a generic verb only
finds nothing, and each guide case of the routing eval ranks its guide first
by score, not by file order.

The member's features and admin right come from the database in production.
These tests replace them with a fixed set, as ``test_show_in_app.py`` does.

Run::

    uv run pytest tests/unit/test_guide_tools.py -q
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from acb_common.app_manifests.guide_manifest import Guide, GuideStep
from orchestrator import guide_tools as gt
from orchestrator import in_app_path as iap

from evals.routing import dataset as ds

ORG = "org-guide-test"
#: The member holds the CRM and the tour. Not Projects.
HELD = frozenset({"crm", "email", "chat"})


class _Queue:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def put(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.lines.append((event, kw))


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> _Queue:
    """A run with GUIDE_ORGS open, a queue, and a member who holds ``HELD``."""
    pytest.importorskip("agent_framework", reason="MAF not installed")
    from acb_common import get_settings
    from orchestrator import executor

    queue = _Queue()
    monkeypatch.setattr(get_settings(), "guide_orgs", "*", raising=False)
    monkeypatch.setattr(gt, "run_org", lambda: ORG)
    monkeypatch.setattr(executor, "resolve_run_queue", lambda key=None: queue)

    async def _features() -> frozenset[str]:
        return HELD

    async def _admin() -> bool:
        return False

    monkeypatch.setattr(iap, "member_features", _features)
    monkeypatch.setattr(gt, "member_is_admin", _admin)
    return queue


def _find(question: str) -> dict[str, Any]:
    from orchestrator.agents import find_guide

    return asyncio.run(find_guide(question))


def _show(guide_id: str) -> dict[str, Any]:
    from orchestrator.agents import show_guide

    return asyncio.run(show_guide(guide_id))


def _open(path: str) -> dict[str, Any]:
    from orchestrator.agents import show_in_app

    return asyncio.run(show_in_app(path, "Here it is."))


def _guide_events(queue: _Queue) -> list[dict[str, Any]]:
    return [e for e in queue.events if e.get("name") == "guide"]


def _fixture_guide(**over: Any) -> Guide:
    base: dict[str, Any] = {
        "id": "fix.thing", "app": "fix", "feature": None, "admin_only": False,
        "title": "Do the thing", "asks": ("do the thing", "thing", "make a thing"),
        "steps": (GuideStep("open", "Open it.", path="/crm"),), "do_tool": None,
    }
    base.update(over)
    return Guide(**base)


# ── find_guide ──────────────────────────────────────────────────────────────

def test_find_ranks_add_a_deal_first(run: _Queue) -> None:
    out = _find("how do I add a deal")
    assert out["guides"][0]["id"] == "crm.add-deal"
    assert out["guides"][0]["steps"].startswith("1. ")
    assert set(out["guides"][0]) == {"id", "title", "app", "steps"}


def test_find_returns_at_most_three(run: _Queue) -> None:
    out = _find("add a deal or a lead or a note, find a contact, move a deal, see the pipeline")
    assert 1 <= len(out["guides"]) <= 3


def test_find_makes_no_page_move(run: _Queue) -> None:
    _find("how do I add a deal")
    assert run.events == []


def test_find_no_match_hints_and_logs_without_the_question(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _Log()
    monkeypatch.setattr(gt, "_log", log)
    question = "how do I fold the secretmoon"
    assert _find(question) == {"guides": [], "hint": "No guide matches. Offer to open the app."}
    assert log.lines == [("guide.find.no_match", {"org": ORG, "words": 6})]
    assert "secretmoon" not in repr(log.lines)


def test_find_hides_a_guide_of_a_pane_the_member_lacks(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _chat_only() -> frozenset[str]:
        return frozenset({"chat"})

    monkeypatch.setattr(iap, "member_features", _chat_only)
    out = _find("how do I add a deal")
    assert all(g["app"] != "crm" for g in out["guides"])


def _say_only_crm_guide() -> Guide:
    """A CRM guide with no ``open`` step, so only its ``feature`` can refuse it."""
    return _fixture_guide(feature="crm", steps=(GuideStep("say", "Press New deal."),))


def _chat_only_member(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _chat_only() -> frozenset[str]:
        return frozenset({"chat"})

    monkeypatch.setattr(iap, "member_features", _chat_only)


def test_find_hides_a_guide_whose_feature_the_member_lacks(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gt, "all_guides", lambda: (_say_only_crm_guide(),))
    assert _find("do the thing")["guides"][0]["id"] == "fix.thing"
    _chat_only_member(monkeypatch)
    assert _find("do the thing") == {"guides": [], "hint": gt.NO_MATCH_HINT}


def test_find_hides_an_admin_guide_from_a_member(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gt, "all_guides", lambda: (_fixture_guide(admin_only=True),))
    assert _find("do the thing")["guides"] == []


def test_find_shows_an_admin_guide_to_an_admin(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _admin() -> bool:
        return True

    monkeypatch.setattr(gt, "member_is_admin", _admin)
    monkeypatch.setattr(gt, "all_guides", lambda: (_fixture_guide(admin_only=True),))
    assert _find("do the thing")["guides"][0]["id"] == "fix.thing"


def test_find_hides_a_guide_that_opens_a_preview_pane(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    preview = _fixture_guide(steps=(GuideStep("open", "Open it.", path="/dashboard"),))
    monkeypatch.setattr(gt, "all_guides", lambda: (preview,))
    assert _find("do the thing")["guides"] == []


# ── the ranking, on the real catalog ────────────────────────────────────────

def _runnable() -> tuple[Guide, ...]:
    """Every guide that a member who holds each live pane may run."""
    held = frozenset(p.feature for p in iap.PANES if p.feature)
    return tuple(g for g in gt.all_guides() if gt.may_run(g, held, False))


def test_add_a_task_finds_no_guide(run: _Queue) -> None:
    # "add" is a generic verb: "add a lead", "add a deal" and "add a note to a
    # deal" each lack the subject word "task".
    assert gt.rank("How do I add a task?", _runnable()) == []
    assert _find("How do I add a task?") == {"guides": [], "hint": gt.NO_MATCH_HINT}


def test_a_note_on_a_project_does_not_find_the_crm_note() -> None:
    ids = [g.id for g in gt.rank("how do I add a note to my project", _runnable())]
    assert "crm.log-note" not in ids, ids


def test_a_generic_verb_alone_never_matches() -> None:
    q = gt.tokens("add create new make find see open get use set move give go")
    assert all(gt.ask_score(q, ask) == (0.0, 0)
               for g in gt.all_guides() for ask in (g.title, *g.asks))
    assert gt.ask_score(gt.tokens("open it"), "open it") == (0.0, 0)


def test_a_missing_generic_verb_still_matches_and_a_missing_subject_does_not() -> None:
    assert gt.ask_score(gt.tokens("deal for Acme"), "add a deal") == (0.0, 0)
    assert gt.ask_score(gt.tokens("deals by stage"), "see all my deals by stage") == (0.667, 2)
    assert gt.ask_score(gt.tokens("see a deal"), "add a deal") == (0.0, 0)
    assert gt.ask_score(gt.tokens("add a note"), "add a note to a deal") == (0.0, 0)


def test_a_generic_verb_matches_its_own_group_only() -> None:
    assert gt.tokens("make a new deal") == gt.tokens("create a deal") == gt.tokens("add a deal")
    assert gt.ask_score(gt.tokens("how to make a deal"), "add a deal") == (1.0, 2)
    # "open" is of the group of "see", not of "add".
    assert gt.ask_score(gt.tokens("how do I open a deal"), "add a deal") == (0.0, 0)


def test_a_one_letter_word_is_no_token() -> None:
    assert gt.tokens("a deal's stage") == frozenset({"deal", "stage"})


def test_a_one_word_ask_matches_only_a_question_of_that_word() -> None:
    assert gt.ask_score(gt.tokens("where is a contact"), "where is a contact") == (1.0, 1)
    assert gt.ask_score(gt.tokens("delete a contact"), "where is a contact") == (0.0, 0)


#: The questions that must find a guide (the id ranked first), and the
#: questions that must find none (None). Review round 2 of GD-7.
_TABLE: list[tuple[str, str | None]] = [
    *((c.words, c.guide) for c in ds.CASES if c.finds),
    ("how do I create a lead", "crm.add-lead"),
    ("new deal", "crm.add-deal"),
    ("log a call", "crm.log-note"),
    ("move a deal to won", "crm.move-deal-stage"),
    ("how to make a deal", "crm.add-deal"),
    ("how do I add a deal for Acme Corp", "crm.add-deal"),
    ("log a note on the Acme deal", "crm.log-note"),
    ("where is a contact", "crm.find-contact"),
    ("show me the pipeline", "crm.see-pipeline"),
    ("give me a tour", "home.tour"),
    ("tour", "home.tour"),
    # No guide: an ask of one word, a verb of another act, or another app.
    ("How do I start a project?", None),
    ("how do I start a timer", None),
    ("add a contact", None),
    ("delete a contact", None),
    ("edit a contact", None),
    ("import contacts", None),
    ("delete the pipeline", None),
    ("export the pipeline", None),
    ("rename the pipeline", None),
    ("How do I add a task?", None),
    ("how do I add a note to my project", None),
    ("where is the CRM", None),
]


@pytest.mark.parametrize(("words", "want"), _TABLE, ids=[w for w, _ in _TABLE])
def test_the_question_table(words: str, want: str | None) -> None:
    ranked = [g.id for g in gt.rank(words, _runnable())]
    if want is None:
        assert ranked == [], ranked
    else:
        assert ranked[:1] == [want], ranked


def test_no_guide_offers_an_add_contact_act() -> None:
    # "add a contact" must find none only while no guide teaches it.
    assert not any("contact" in g.id and "add" in g.id for g in gt.all_guides())


@pytest.mark.parametrize("case", [c for c in ds.CASES if c.finds], ids=lambda c: c.id)
def test_each_guide_case_ranks_its_guide_first_by_score(case: ds.Case) -> None:
    guides = _runnable()
    ranked = gt.rank(case.words, guides)
    if case.guide is None:
        assert ranked == [], [g.id for g in ranked]
        return
    assert ranked and ranked[0].id == case.guide, [g.id for g in ranked]
    if len(ranked) > 1:
        assert gt.score(case.words, ranked[0]) > gt.score(case.words, ranked[1])
    # File order does not decide it: the reversed catalog ranks the same guide first.
    assert gt.rank(case.words, guides[::-1])[0].id == case.guide


def test_give_me_a_tour_finds_the_tour(run: _Queue) -> None:
    assert _find("Give me a tour.")["guides"][0]["id"] == "home.tour"


# ── show_guide ──────────────────────────────────────────────────────────────

def test_show_emits_one_guide_event(run: _Queue) -> None:
    out = _show("crm.add-deal")
    assert out["started"] == "crm.add-deal"
    assert run.events == [{"type": "CUSTOM", "name": "guide", "value": {"guide_id": "crm.add-deal"}}]


def test_show_refuses_an_unknown_id(run: _Queue) -> None:
    assert _show("crm.nope") == {"error": "no guide has that id"}
    assert run.events == []


def test_show_refuses_a_pane_the_member_lacks(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _chat_only() -> frozenset[str]:
        return frozenset({"chat"})

    monkeypatch.setattr(iap, "member_features", _chat_only)
    assert _show("crm.add-deal") == {"error": "the member may not run that guide"}
    assert run.events == []


def test_show_refuses_a_guide_whose_feature_the_member_lacks(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gt, "all_guides", lambda: (_say_only_crm_guide(),))
    _chat_only_member(monkeypatch)
    assert _show("fix.thing") == {"error": "the member may not run that guide"}
    assert run.events == []


def test_show_refuses_an_admin_guide_for_a_member(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gt, "all_guides", lambda: (_fixture_guide(admin_only=True),))
    assert _show("fix.thing") == {"error": "the member may not run that guide"}
    assert run.events == []


def test_show_refuses_a_guide_that_opens_a_preview_pane(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    preview = _fixture_guide(steps=(GuideStep("open", "Open it.", path="/dashboard"),))
    monkeypatch.setattr(gt, "all_guides", lambda: (preview,))
    assert _show("fix.thing") == {"error": "the member may not run that guide"}
    assert run.events == []


def test_show_refuses_a_nested_run(run: _Queue) -> None:
    from acb_skills.agent_tools import _delegation_chain

    token = _delegation_chain.set(("email-assistant",))
    try:
        assert "error" in _show("crm.add-deal")
    finally:
        _delegation_chain.reset(token)
    assert run.events == []
    assert _show("crm.add-deal")["started"] == "crm.add-deal"


def test_show_then_show_in_app_is_one_move(run: _Queue) -> None:
    assert _show("crm.add-deal")["started"] == "crm.add-deal"
    assert _open("/email") == {"error": "already opened a page this turn"}
    assert [e["name"] for e in run.events] == ["guide"]


def test_show_in_app_then_show_is_one_move(run: _Queue) -> None:
    assert _open("/email")["opened"] == "/email"
    assert _show("crm.add-deal") == {"error": "already moved the page this turn"}
    assert [e["name"] for e in run.events] == ["navigate"]


def test_a_second_show_in_one_turn_is_refused(run: _Queue) -> None:
    assert _show("crm.add-deal")["started"] == "crm.add-deal"
    assert _show("crm.add-lead") == {"error": "already moved the page this turn"}
    assert len(_guide_events(run)) == 1


def test_a_refused_show_does_not_spend_the_move(run: _Queue) -> None:
    assert "error" in _show("crm.nope")
    assert _open("/email")["opened"] == "/email"


# ── one page move when tool calls run at once ───────────────────────────────

@pytest.fixture
def cold(run: _Queue, monkeypatch: pytest.MonkeyPatch) -> _Queue:
    """The member's features take one await, as a cold access cache does."""
    async def _features() -> frozenset[str]:
        await asyncio.sleep(0)
        return HELD

    monkeypatch.setattr(iap, "member_features", _features)
    return run


def _at_once(*calls: Any) -> list[dict[str, Any]]:
    async def main() -> list[dict[str, Any]]:
        return list(await asyncio.gather(*calls))

    return asyncio.run(main())


def _moves(queue: _Queue) -> list[str]:
    return [e["name"] for e in queue.events if e.get("name") in ("guide", "navigate")]


def test_show_guide_and_show_in_app_at_once_move_the_page_once(cold: _Queue) -> None:
    from orchestrator.agents import show_guide, show_in_app

    out = _at_once(show_guide("crm.add-deal"), show_in_app("/email", "Here it is."))
    assert _moves(cold) == ["guide"], out
    # The second call met the mark while the first still ran: pending words.
    assert out[1] == {"error": iap.MOVE_PENDING_ERROR}
    assert cold._show_in_app_done is True


def test_show_in_app_and_show_guide_at_once_move_the_page_once(cold: _Queue) -> None:
    from orchestrator.agents import show_guide, show_in_app

    out = _at_once(show_in_app("/email", "Here it is."), show_guide("crm.add-deal"))
    assert _moves(cold) == ["navigate"], out
    assert out[1] == {"error": iap.MOVE_PENDING_ERROR}


def test_a_refused_holder_leaves_the_other_call_pending_words_not_moved_words(
    cold: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.agents import show_guide, show_in_app

    real = gt.all_guides()
    monkeypatch.setattr(gt, "all_guides", lambda: (*real, _fixture_guide(admin_only=True)))
    out = _at_once(show_guide("fix.thing"), show_in_app("/email", "Here it is."))
    assert out == [{"error": "the member may not run that guide"},
                   {"error": iap.MOVE_PENDING_ERROR}]
    assert _moves(cold) == []
    # Nothing moved, so the move is still free.
    assert _open("/email")["opened"] == "/email"


def test_a_done_mark_keeps_the_moved_words(run: _Queue) -> None:
    assert _show("crm.add-deal")["started"] == "crm.add-deal"
    assert run._show_in_app_done is True
    assert _show("crm.add-lead") == {"error": "already moved the page this turn"}
    assert _open("/email") == {"error": "already opened a page this turn"}


def test_two_show_guides_at_once_move_the_page_once(cold: _Queue) -> None:
    from orchestrator.agents import show_guide

    out = _at_once(show_guide("crm.add-deal"), show_guide("crm.add-lead"))
    assert _moves(cold) == ["guide"], out
    assert len(_guide_events(cold)) == 1


def test_a_show_refused_after_the_mark_gives_it_back(
    cold: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = gt.all_guides()
    monkeypatch.setattr(gt, "all_guides", lambda: (*real, _fixture_guide(admin_only=True)))
    assert _show("fix.thing") == {"error": "the member may not run that guide"}
    assert _show("crm.add-deal")["started"] == "crm.add-deal"
    assert _moves(cold) == ["guide"]


def test_a_show_in_app_refused_after_the_mark_gives_it_back(cold: _Queue) -> None:
    # The member does not hold Projects.
    assert "error" in _open("/projects")
    assert _show("crm.add-deal")["started"] == "crm.add-deal"
    assert _moves(cold) == ["guide"]


# ── the admin right ─────────────────────────────────────────────────────────

def test_the_admin_check_is_the_executors(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("agent_framework", reason="MAF not installed")
    import acb_auth
    from orchestrator import executor

    class _Access:
        is_active = True

        def __init__(self, held: str) -> None:
            self.held = held

        def has(self, permission: str) -> bool:
            return permission == self.held

    holds: dict[str, str] = {"p": executor._FIRST_PARTY_ADMIN_PERMISSION}

    async def _resolve(_member: str) -> _Access:
        return _Access(holds["p"])

    monkeypatch.setattr(executor, "_verified_run_member", lambda: "admin@example.com")
    monkeypatch.setattr(acb_auth, "resolve_access", _resolve)
    assert asyncio.run(gt.member_is_admin()) is True
    holds["p"] = "admin:members:read"
    assert asyncio.run(gt.member_is_admin()) is False


def test_show_fails_closed_with_the_flag_off(run: _Queue, monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "guide_orgs", "", raising=False)
    assert _show("crm.add-deal") == {"error": "no guides here"}
    assert _find("how do I add a deal") == {"error": "no guides here"}
    assert run.events == []


# ── the tool list ───────────────────────────────────────────────────────────

def _tool_names(monkeypatch: pytest.MonkeyPatch, guide_orgs: str, org: str | None) -> list[str]:
    from acb_common import get_settings
    from orchestrator import agents as pkg

    mod = pkg._mod

    class _FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr(mod, "Agent", _FakeAgent)
    monkeypatch.setattr(mod, "_make_openai_client", lambda *a, **k: object())
    monkeypatch.setattr(mod, "_load_specialist_agents_as_tools", lambda: [])
    monkeypatch.setattr(gt, "run_org", lambda: None)
    monkeypatch.setattr(get_settings(), "guide_orgs", guide_orgs, raising=False)
    agent = mod.build_orchestrator_agent(with_history=False, organization_id=org)
    return [getattr(t, "__name__", "") for t in agent.kwargs["tools"]]


def test_the_flag_off_lists_neither_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("agent_framework", reason="MAF not installed")
    for orgs, org in (("", ORG), ("other-org", ORG), ("*", None)):
        names = _tool_names(monkeypatch, orgs, org)
        assert "find_guide" not in names and "show_guide" not in names, (orgs, org)


def test_the_flag_on_lists_both_after_the_page_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("agent_framework", reason="MAF not installed")
    off = _tool_names(monkeypatch, "", ORG)
    for orgs in (ORG, "*", f"x, {ORG.upper()}"):
        on = _tool_names(monkeypatch, orgs, ORG)
        assert "find_guide" in on and "show_guide" in on
        assert [n for n in on if n not in ("find_guide", "show_guide")] == off
        assert on.index("show_guide") == on.index("find_guide") + 1


def test_find_guide_stays_on_whatsapp_and_show_guide_does_not() -> None:
    from acb_skills import whatsapp_ui

    assert "show_guide" in whatsapp_ui.WITHHELD_TOOLS
    assert "find_guide" not in whatsapp_ui.WITHHELD_TOOLS
