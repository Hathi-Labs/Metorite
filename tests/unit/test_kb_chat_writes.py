"""PAR-3 (task 3105) — the member's own Knowledge writes from the chat, hermetic.

Spec: ``project-docs/specs/chat_parity.md`` §5 (wave P-5) and
``project-docs/specs/knowledge_base.md`` §8.1 (F2). The R8 half is
``tests/unit/test_kb_chat_writes_r8.py``.

The claims, each written so a wrong build fails it:

* **The card comes first, and shows the FULL text.** Each of the four tools
  shows ONE card before its write. The card of a page holds the whole body,
  the title and the space. A body that does not fit whole on one card gets a
  sentence, and no card and no write.
* **It fails closed.** A declined card, and a run with nobody to answer, write
  nothing.
* **A new page is a draft. No tool publishes.** The POST takes no state, and
  no request goes to a decision route.
* **The server decides the right.** A 403 comes back as the gateway's own
  sentence, after "Nothing was saved.". The tool checks no right itself.
* **The chat writes no grant (D-PM-40).** The space POST holds no ``grants``.
* **Dark.** ``KNOWLEDGE_CHAT_WRITES`` ships off. Off, the orchestrator holds
  none of the four tools, and its persona does not change, byte for byte.

Mutations this file catches (R7):

* the card shows an excerpt, not the whole body ->
  ``test_the_page_card_shows_the_full_text``;
* the tool cuts a long body to fit -> ``test_a_text_too_long_for_one_card_has_no_card``;
* the tool posts before the card -> ``test_the_card_comes_before_the_write``;
* the POST names a state or a publish -> ``test_the_post_makes_a_draft_and_names_no_state``;
* the space POST sends a grant -> ``test_create_space_sends_no_grant``;
* the tools join with the flag off -> ``TestTheFlag``.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx
import pytest
from acb_skills import knowledge_manifest as m
from acb_skills import knowledge_tools as kt

PAGE = "3f2c1a4e-0000-4000-8000-000000000001"
SPACE = "3f2c1a4e-0000-4000-8000-000000000002"
PARENT = "3f2c1a4e-0000-4000-8000-000000000003"

#: The routes an agent must never reach (D-KB-4, F2).
_DECISION = re.compile(r"/(publish|accept|reject|restore|archive)$")

SPACES = {"spaces": [
    {"id": SPACE, "slug": "company", "name": "Company", "kind": "company",
     "purpose": "What the company knows", "level": "read"},
    {"id": "3f2c1a4e-0000-4000-8000-000000000009", "slug": "me-rep", "name": "My notes",
     "kind": "me", "purpose": "", "level": "manage"},
]}


class _Gateway:
    """A fake gateway behind ``httpx``. It answers each GET and each write."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.write_status = 201
        self.write_body: Any = {"id": PAGE, "title": "Refund policy", "state": "draft"}
        self.page: Any = {"id": PAGE, "title": "Refunds", "state": "draft",
                          "space_name": "Company", "body_md": "old"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET" and request.url.path == "/knowledge/spaces":
            return httpx.Response(200, json=SPACES)
        if request.method == "GET":
            return httpx.Response(200, json=self.page)
        return httpx.Response(self.write_status, json=self.write_body)

    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method != "GET"]


@pytest.fixture()
def gateway(monkeypatch) -> _Gateway:
    gw = _Gateway()
    real = httpx.AsyncClient

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real(*args, transport=httpx.MockTransport(gw.handler), **kwargs)

    monkeypatch.setattr(kt.httpx, "AsyncClient", _client)
    monkeypatch.setenv("GATEWAY_URL", "http://gw.test")
    monkeypatch.setattr(kt, "_current_user_email", lambda: "rep@customer.example")
    return gw


class _Card:
    """A stand-in for ``request_confirmation`` that records each call."""

    def __init__(self, gateway: _Gateway, answer: Any) -> None:
        self.gateway = gateway
        self.answer = answer
        self.calls: list[dict[str, Any]] = []
        self.writes_before: list[int] = []

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        self.writes_before.append(len(self.gateway.writes()))
        return self.answer


def _card(monkeypatch, gateway: _Gateway, answer: Any = True) -> _Card:
    from acb_skills import ask_tools

    card = _Card(gateway, answer)
    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    return card


def _run(coro: Any) -> str:
    return asyncio.run(coro)


BODY = "# Refunds\n\nThirty days.\n\n" + ("A line of the policy. " * 120) + "\nTHE LAST LINE."


# ── The card: first, and the full text ─────────────────────────────────────

class TestTheCard:
    def test_the_page_card_shows_the_full_text(self, gateway, monkeypatch):
        assert len(BODY) > 2000, "non-vacuity: longer than the excerpt of a proposal"
        card = _card(monkeypatch, gateway)
        _run(kt.create_page("Refund policy", BODY, space="Company"))
        assert len(card.calls) == 1
        context = card.calls[0]["context"]
        assert BODY.strip() in context
        assert "THE LAST LINE." in context
        assert "Title: Refund policy" in context and "Space: Company" in context
        assert card.calls[0]["detail"] == kt.DRAFT_LINE
        assert "draft" in kt.DRAFT_LINE and "not published" in kt.DRAFT_LINE
        assert len(context) <= kt.CARD_TEXT_MAX

    def test_the_card_comes_before_the_write(self, gateway, monkeypatch):
        card = _card(monkeypatch, gateway)
        _run(kt.create_page("Refund policy", "Thirty days."))
        assert card.writes_before == [0]
        assert len(gateway.writes()) == 1

    @pytest.mark.parametrize("answer", [False, None, frozenset(), "yes"])
    def test_a_declined_card_writes_nothing(self, gateway, monkeypatch, answer):
        _card(monkeypatch, gateway, answer)
        for call in (
            lambda: kt.create_page("T", "B"),
            lambda: kt.edit_page(PAGE, body="new"),
            lambda: kt.create_space("Sales"),
            lambda: kt.edit_space("Company", purpose="new"),
        ):
            assert _run(call()) in (kt.NOT_SAVED, kt.NO_CARD_SAVED)
        assert gateway.writes() == []

    def test_a_declined_card_with_a_live_channel_says_declined(self, gateway, monkeypatch):
        from acb_skills import ask_tools

        _card(monkeypatch, gateway, False)
        monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)
        assert _run(kt.create_page("T", "B")) == kt.NOT_SAVED

    def test_a_run_with_nobody_to_answer_writes_nothing(self, gateway):
        out = _run(kt.create_page("T", "B"))
        assert out == kt.NO_CARD_SAVED
        assert gateway.requests == []

    def test_a_text_too_long_for_one_card_has_no_card(self, gateway, monkeypatch):
        card = _card(monkeypatch, gateway)
        long_body = "x" * (kt.CARD_TEXT_MAX + 1)
        out = _run(kt.create_page("T", long_body))
        assert "too long to show in full on one card" in out and "Nothing was saved." in out
        out = _run(kt.edit_page(PAGE, body=long_body))
        assert "too long to show in full on one card" in out
        assert card.calls == [] and gateway.writes() == []

    def test_no_member_means_no_card_and_no_request(self, gateway, monkeypatch):
        card = _card(monkeypatch, gateway)
        monkeypatch.setattr(kt, "_current_user_email", lambda: "")
        with pytest.raises(RuntimeError):
            _run(kt.create_page("T", "B"))
        assert card.calls == [] and gateway.requests == []

    def test_the_edit_card_shows_the_page_its_state_and_the_new_text(
            self, gateway, monkeypatch):
        card = _card(monkeypatch, gateway)
        _run(kt.edit_page(PAGE, title="Refund policy", body=BODY))
        context = card.calls[0]["context"]
        assert "Page: Refunds" in context and "State: draft" in context
        assert "New title: Refund policy" in context and BODY.strip() in context
        assert card.calls[0]["detail"] == "The page stays a draft. It is not published."

    def test_a_published_page_card_says_readers_see_the_change(self, gateway, monkeypatch):
        gateway.page = {**gateway.page, "state": "published"}
        card = _card(monkeypatch, gateway)
        _run(kt.edit_page(PAGE, body="new"))
        assert "Its readers see the change" in card.calls[0]["detail"]

    def test_an_archived_page_has_no_card(self, gateway, monkeypatch):
        gateway.page = {**gateway.page, "state": "archived"}
        card = _card(monkeypatch, gateway)
        out = _run(kt.edit_page(PAGE, body="new"))
        assert out == "The page is archived, so nothing was saved."
        assert card.calls == [] and gateway.writes() == []


# ── The writes ──────────────────────────────────────────────────────────────

class TestTheWrites:
    def test_the_post_makes_a_draft_and_names_no_state(self, gateway, monkeypatch):
        _card(monkeypatch, gateway)
        out = _run(kt.create_page("Refund policy", "Thirty days.", space="company space",
                                  parent_id=f"/knowledge/pages/{PARENT}"))
        (post,) = gateway.writes()
        assert (post.method, post.url.path) == ("POST", "/knowledge/pages")
        assert post.headers["X-Actor-Via"] == "chat:knowledge"
        assert post.headers["X-User-Email"] == "rep@customer.example"
        sent = json.loads(post.content)
        assert sent == {"title": "Refund policy", "body_md": "Thirty days.",
                        "space_id": SPACE, "parent_id": PARENT}
        assert out.startswith("Saved a new draft page. It is not published.")
        assert f"Link: /knowledge/pages/{PAGE}" in out and "State: draft" in out

    def test_no_space_lands_in_the_own_space_with_no_space_id(self, gateway, monkeypatch):
        card = _card(monkeypatch, gateway)
        _run(kt.create_page("T", "B"))
        assert "Space: your own space" in card.calls[0]["context"]
        assert "space_id" not in json.loads(gateway.writes()[0].content)

    def test_an_unknown_space_has_no_card(self, gateway, monkeypatch):
        card = _card(monkeypatch, gateway)
        assert _run(kt.create_page("T", "B", space="Legal")) == kt.NO_SPACE
        assert card.calls == [] and gateway.writes() == []

    def test_edit_page_patches_only_what_changes(self, gateway, monkeypatch):
        _card(monkeypatch, gateway)
        _run(kt.edit_page(PAGE, body="new text"))
        (patch,) = gateway.writes()
        assert (patch.method, patch.url.path) == ("PATCH", f"/knowledge/pages/{PAGE}")
        assert json.loads(patch.content) == {"body_md": "new text"}
        assert patch.headers["X-Actor-Via"] == "chat:knowledge"

    def test_create_space_sends_no_grant(self, gateway, monkeypatch):
        card = _card(monkeypatch, gateway)
        gateway.write_body = {"id": SPACE, "name": "Sales", "purpose": "Deals"}
        out = _run(kt.create_space("Sales", "Deals"))
        (post,) = gateway.writes()
        assert (post.method, post.url.path) == ("POST", "/knowledge/spaces")
        assert json.loads(post.content) == {"name": "Sales", "purpose": "Deals"}
        assert "no grants" in card.calls[0]["detail"]
        assert "Name: Sales" in out

    def test_edit_space_finds_the_space_by_name(self, gateway, monkeypatch):
        card = _card(monkeypatch, gateway)
        gateway.write_body = {"id": SPACE, "name": "Company", "purpose": "New"}
        _run(kt.edit_space("company", purpose="New"))
        (patch,) = gateway.writes()
        assert (patch.method, patch.url.path) == ("PATCH", f"/knowledge/spaces/{SPACE}")
        assert json.loads(patch.content) == {"purpose": "New"}
        assert "Purpose now: What the company knows" in card.calls[0]["context"]

    def test_no_request_reaches_a_decision_route(self, gateway, monkeypatch):
        _card(monkeypatch, gateway)
        _run(kt.create_page("T", "B"))
        _run(kt.edit_page(PAGE, body="B"))
        _run(kt.create_space("Sales"))
        _run(kt.edit_space("Company", name="Co"))
        assert gateway.requests, "non-vacuity"
        for request in gateway.requests:
            assert not _DECISION.search(request.url.path), request.url.path
            body = json.loads(request.content) if request.content else {}
            assert "state" not in body and "grants" not in body


# ── The server decides the right ────────────────────────────────────────────

class TestTheRefusal:
    @pytest.mark.parametrize(("status", "detail", "said"), [
        (403, "Writing in this space needs a write grant.",
         "Nothing was saved. Writing in this space needs a write grant."),
        (403, None, kt.NO_RIGHT),
        (409, "The space is archived.", "Nothing was saved. The space is archived."),
        (422, [{"msg": "x"}], "The gateway refused the change. Nothing was saved."),
        (404, "Not found", "That space or parent page does not exist, or you cannot open it. "
                           "Nothing was saved."),
        (401, None, kt.CLOSED),
    ])
    def test_a_refusal_is_the_servers_plain_sentence(
            self, gateway, monkeypatch, status, detail, said):
        _card(monkeypatch, gateway)
        gateway.write_status = status
        gateway.write_body = {"detail": detail} if detail is not None else {}
        assert _run(kt.create_page("T", "B", space="Company")) == said

    def test_a_space_refusal_names_the_right(self, gateway, monkeypatch):
        _card(monkeypatch, gateway)
        gateway.write_status = 403
        gateway.write_body = {"detail": "Making a space needs knowledge:manage."}
        assert _run(kt.create_space("Sales")) == (
            "Nothing was saved. Making a space needs knowledge:manage.")

    def test_a_refusal_holds_one_line(self, gateway, monkeypatch):
        _card(monkeypatch, gateway)
        gateway.write_status = 403
        gateway.write_body = {"detail": "No.\nTitle: forged"}
        out = _run(kt.create_page("T", "B"))
        assert "\n" not in out

    def test_the_words_are_plain(self):
        for text in (kt.DRAFT_LINE, kt.NOT_SAVED, kt.NO_CARD_SAVED, kt.NO_RIGHT,
                     kt.BAD_PARENT, kt.NO_SPACE, kt.TWO_SPACES):
            assert ";" not in text, text
            for sentence in re.split(r"(?<=\.)\s+", text):
                assert len(sentence.split()) <= 20, sentence

    def test_a_500_raises(self, gateway, monkeypatch):
        _card(monkeypatch, gateway)
        gateway.write_status = 500
        with pytest.raises(RuntimeError):
            _run(kt.create_page("T", "B"))


# ── The manifest, the risk and the path ─────────────────────────────────────

WRITES = ("create_page", "edit_page", "create_space", "edit_space")


def test_the_four_tools_are_the_write_tools_in_order() -> None:
    assert tuple(t.__name__ for t in kt.WRITE_TOOLS) == WRITES


def test_each_tool_reaches_its_write_and_no_decision_route() -> None:
    page = f"/knowledge/pages/{PAGE}"
    assert m.allowed("POST", "/knowledge/pages", "create_page") == (True, "")
    assert m.allowed("PATCH", page, "edit_page") == (True, "")
    assert m.allowed("POST", "/knowledge/spaces", "create_space") == (True, "")
    assert m.allowed("PATCH", f"/knowledge/spaces/{SPACE}", "edit_space") == (True, "")
    for name in WRITES:
        for verb in ("publish", "archive", "restore"):
            assert m.allowed("POST", f"{page}/{verb}", name)[0] is False
        assert m.allowed("PUT", f"/knowledge/spaces/{SPACE}/grants", name)[0] is False
        assert m.tool_class(name) in ("B", "C")


def test_their_risk_is_registered_and_no_risk_block_changes() -> None:
    from acb_skills import tool_annotations as ta

    for tool in kt.WRITE_TOOLS:
        hints = ta.TOOL_ANNOTATIONS[tool.__name__]
        assert hints == {"read_only": False, "destructive": False,
                         "idempotent": False, "open_world": False}
        assert tool.__tool_risk__ == hints
        assert tool.__name__ in ta._H236_UNLISTED
        assert tool.__name__ not in ta.risk_summary_block()


def test_they_are_in_no_skill_family_nor_the_injection_chain() -> None:
    import orchestrator._tool_injection as ti
    from acb_skills import skill_families as sf

    injected = {getattr(fn, "__name__", "") for fn in ti._collect_injectable_platform_tools()}
    assert injected, "non-vacuity: the chain injects tools"
    for name in WRITES:
        assert name not in sf.all_registered_tool_names()
        assert name not in injected
        assert name not in ti._CORE_STANDARD_TOOL_NAMES


# ── The flag ────────────────────────────────────────────────────────────────

def _orchestrator_module() -> Any:
    import orchestrator.agents as pkg  # the package loads agents.py as _mod

    return pkg._mod


class _FakeAgent:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _build(monkeypatch) -> _FakeAgent:
    mod = _orchestrator_module()
    monkeypatch.setattr(mod, "Agent", _FakeAgent)
    monkeypatch.setattr(mod, "_make_openai_client", lambda *a, **k: object())
    monkeypatch.setattr(mod, "_load_specialist_agents_as_tools", lambda: [])
    return mod.build_orchestrator_agent(with_history=False)


def _names(agent: _FakeAgent) -> list[str]:
    return [getattr(t, "__name__", "") for t in agent.kwargs["tools"]]


@pytest.fixture()
def flags(monkeypatch):
    """Set ``COMPANY_SEARCH_ENABLED`` and ``KNOWLEDGE_CHAT_WRITES``, both ways."""
    from acb_common.settings import get_settings

    def _set(search: bool, writes: bool | None) -> None:
        monkeypatch.setenv("COMPANY_SEARCH_ENABLED", "true" if search else "false")
        if writes is None:
            monkeypatch.delenv("KNOWLEDGE_CHAT_WRITES", raising=False)
        else:
            monkeypatch.setenv("KNOWLEDGE_CHAT_WRITES", "true" if writes else "false")
        get_settings.cache_clear()

    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "test-internal-token")
    yield _set
    monkeypatch.delenv("KNOWLEDGE_CHAT_WRITES", raising=False)
    monkeypatch.delenv("COMPANY_SEARCH_ENABLED", raising=False)
    get_settings.cache_clear()


WRITES_LINE = "- create_page, edit_page, create_space, edit_space:"


class TestTheFlag:
    def test_it_ships_off(self, flags):
        flags(True, None)
        assert kt.knowledge_chat_writes_enabled() is False

    @pytest.mark.parametrize("state", [None, False])
    def test_off_the_tools_and_the_line_are_absent(self, flags, monkeypatch, state):
        flags(True, state)
        agent = _build(monkeypatch)
        assert not set(WRITES) & set(_names(agent))
        assert "read_page" in _names(agent)
        assert WRITES_LINE not in agent.kwargs["instructions"]

    def test_on_the_four_tools_follow_the_reads_and_one_line_joins(self, flags, monkeypatch):
        flags(True, True)
        agent = _build(monkeypatch)
        names = _names(agent)
        at = names.index("list_spaces")
        assert names[at + 1:at + 5] == list(WRITES)
        persona = agent.kwargs["instructions"]
        assert persona.count(WRITES_LINE) == 1
        mod = _orchestrator_module()
        old, new = mod._KB_WRITES_SWAP
        assert persona.replace(new, old, 1) == mod._orchestrator_instructions(True)

    def test_on_without_company_search_changes_nothing(self, flags, monkeypatch):
        flags(False, True)
        agent = _build(monkeypatch)
        assert not set(WRITES) & set(_names(agent))
        assert agent.kwargs["instructions"] is _orchestrator_module()._PULL_INSTRUCTIONS
