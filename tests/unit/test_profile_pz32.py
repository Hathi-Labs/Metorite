"""WS-63 PZ-32: the card grounds a value, the offer to remember, two new areas.

Owning spec: ``project-docs/specs/personalization.md`` §5.5 and §14.3 to
§14.5. Owner decisions of 2026-10-30, in chat: the areas ``name`` and
``language``, the offer to remember (one card, once a chat), and Reach
reads neither ``rhythm`` nor ``channels`` until PZ-18.

Hermetic. The gateway is the fake of ``test_profile_write_tools``. The card
tests of section A run the REAL card gate (``ask_tools.request_confirmation``)
on path A of a live run, and answer it through the REAL
``executor.resolve_user_input``, the one function that the gateway's
``POST /agent/respond-input`` calls. So they prove the binding of an
approve: to the card that showed the payload, to the run's own member and
to the run's own thread.

Mutations this file catches (R7):

* ``_ground`` refuses before the card again, so a looked-up address never
  saves (P0-1);
* ``request_confirmation`` stops checking ``responder``, so an answer of
  another member approves a write;
* ``resolve_user_input`` stops checking the owner thread;
* the card comes before ``_write_gate``, or an approve skips the check after
  it, so a marked session writes;
* ``_fact_line`` drops the area (P1-1), ``profile_remember`` saves with an
  unknown area (P1-2), a sitting leaves ``last_asked_at`` (P1-3);
* the token count of a native session records the interview memo (P1-4);
* the persona says "auto-extracts" again, or rule 8 calls remember() (P1-5,
  P2-3);
* a second offer in one chat shows a card, an offer saves on no, or a
  sensitive offer saves on one yes;
* the area tag pushes the block over 6,000 characters.

Repair round 1 (verifier and adversarial review), section I:

* a stop writes no ``stopped_at``, or ``due_area`` and ``open_areas`` ignore
  it, or read an old stopped row as open, so a stopped member is asked
  "What should I call you?" (P1-1);
* a reset after a stop leaves the member stopped, or opens every area;
* a sensitive value that the member did not type saves on one yes (P1-2);
* an offer skips the typed words, so a memory or a Continue turn is offered
  as "You said it in this chat" (P1-3);
* a refusal of an area names a tool or an id in the member's line (P2-5);
* a turn with no typed words, or a style note, gets the card (P2-6);
* the peek around the fallback string goes, or an import fault of the
  structured run skips the interview memo (P2-7);
* the card writes or the offer line run with
  ``ASSISTANT_PROFILE_CARD_WRITES_ORGS`` off for the org (P2-8).
"""
# ruff: noqa: F811 — the imported fixtures are the arguments of each test
from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from acb_common import assistant_profile as ap
from acb_skills import ask_tools as _ask_tools
from acb_skills import egress
from acb_skills import profile_tools as pt

from tests.unit import test_executor_profile_block as epb
from tests.unit.test_executor_profile_block import run  # noqa: F401
from tests.unit.test_profile_write_tools import (  # noqa: F401
    _REAL_CHANNEL,
    _REAL_CONFIRM,
    MEMBER,
    _orchestrator_module,
    _refused,
    _run,
    _status_of,
    _undo_of,
    gateway,
    own_chat,
)

#: The thread of the run that asks each card.
THREAD = "t-pz32-own-chat"
#: The member's typed words of the turn of test A.
RAJ_AND_PRIYA = "Raj and Priya"
RAJ = "raj.patel@alpha.example"
PRIYA = "priya@alpha.example"
#: The org of each run here. The card writes are on for it, unless a test
#: turns them off (``ASSISTANT_PROFILE_CARD_WRITES_ORGS``).
ORG = "0a0a0a0a-0000-4000-8000-000000003232"
#: The member's typed words of each offer test: each offered text is in them.
OFFER_WORDS = ("I run the Pune plant. I like long walks. I have diabetes. I like tea. "
               "I work from Pune.")


@pytest.fixture(autouse=True)
def _no_saved_alias(request: pytest.FixtureRequest) -> None:
    """The fake profile holds an alias "Raj". These tests save a new one."""
    if "own_chat" in request.fixturenames:
        request.getfixturevalue("own_chat").profile["aliases"] = []


def _card_writes(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "assistant_profile_card_writes_orgs", value,
                        raising=False)


@pytest.fixture(autouse=True)
def _cards_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The run's org, with the card writes of PZ-32 on for it. A test of the
    flag turns them off with :func:`_card_writes`."""
    from orchestrator import executor as ex

    monkeypatch.setattr(ex, "_current_run_org", lambda: ORG)
    _card_writes(monkeypatch, ORG)


# ── A. The card grounds a value, bound to its payload, member and thread ───


@pytest.fixture()
def real_card(own_chat, monkeypatch):
    """The real card gate in place of the fixture's fake card."""
    monkeypatch.setattr(_ask_tools, "request_confirmation", _REAL_CONFIRM)
    monkeypatch.setattr(_ask_tools, "confirmation_channel_open", _REAL_CHANNEL)
    return own_chat


async def _in_live_run(calls: list[Any], answer: Any) -> tuple[list[str], list[dict]]:
    """Run each call in one live run of *THREAD*, and answer each card.

    The run binds a run queue (path A of ``request_confirmation``) and its
    relay thread, as ``run_agent_stream`` does. ``answer(rid, card)`` answers
    each ``confirmation_requested`` card as the gateway route would."""
    from orchestrator import executor as ex

    queue: asyncio.Queue = asyncio.Queue()
    tq = ex._active_run_queue.set(queue)
    tt = ex._stream_relay_thread_id.set(THREAD)
    cards: list[dict] = []
    try:
        tasks = [asyncio.create_task(c()) for c in calls]
        while not all(t.done() for t in tasks):
            get = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait({get, *tasks}, timeout=10,
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                raise TimeoutError("no card and no result")
            if get not in done:
                get.cancel()
                continue
            event = get.result()
            if event.get("name") == "confirmation_requested":
                cards.append(event["value"])
                await answer(event["value"]["request_id"], event["value"])
        return [t.result() for t in tasks], cards
    finally:
        ex._stream_relay_thread_id.reset(tt)
        ex._active_run_queue.reset(tq)


def _resolve(rid: str, answer: str = "APPROVE", *, thread: str = THREAD,
             responder: str | None = MEMBER, declined: bool = False) -> bool:
    from orchestrator import executor as ex

    return ex.resolve_user_input(rid, answer, False, thread_id=thread,
                                 responder=responder, declined=declined)


def _alias_posts(gw: Any) -> list[dict[str, Any]]:
    return [json.loads(r.content) for r in gw.writes()
            if r.method == "POST" and r.url.path.endswith("/aliases")]


def _two_aliases() -> list[Any]:
    return [lambda: pt.profile_alias("Raj", RAJ), lambda: pt.profile_alias("Priya", PRIYA)]


def test_card_grounds_alias_values(real_card) -> None:
    """The member typed only the names. The lookup gave the addresses. One
    approved card each saves both aliases, with the exact address shown."""
    async def _approve(rid: str, card: dict) -> None:
        assert _resolve(rid) is True

    with ap.member_typed(RAJ_AND_PRIYA):
        results, cards = _run(_in_live_run(_two_aliases(), _approve))
    assert sorted(c["context"] for c in cards) == [f"Priya = {PRIYA}", f"Raj = {RAJ}"]
    for card in cards:
        assert pt.ASK_ADDRESS in card["detail"] and pt.ASK_NOT_TYPED in card["detail"]
    assert all(_undo_of(r)["op"] == "delete" for r in results)
    assert sorted(_alias_posts(real_card), key=lambda b: b["alias"]) == [
        {"alias": "Priya", "email": PRIYA}, {"alias": "Raj", "email": RAJ}]


def test_card_bound_to_payload_and_member(real_card) -> None:
    """An approve binds to the ONE card that showed its payload, to the run's
    member and to the run's thread. A decline saves nothing."""
    async def _answer(rid: str, card: dict) -> None:
        if card["context"].startswith("Raj"):
            # Another thread cannot answer it, and another member's answer
            # is no approval.
            assert _resolve(rid, thread="t-another-room") is False
            assert _resolve(rid, responder="eve@alpha.example") is True
        else:
            assert _resolve(rid, "", declined=True) is True

    with ap.member_typed(RAJ_AND_PRIYA):
        results, cards = _run(_in_live_run(_two_aliases(), _answer))
    assert len(cards) == 2
    assert [_status_of(r)["reason"] for r in results] == ["declined", "declined"]
    assert real_card.writes() == []


def test_an_answer_with_no_responder_is_no_approval(real_card) -> None:
    async def _answer(rid: str, card: dict) -> None:
        assert _resolve(rid, responder=None) is True

    with ap.member_typed("Raj"):
        (result,), _ = _run(_in_live_run([lambda: pt.profile_alias("Raj", RAJ)], _answer))
    assert _status_of(result)["reason"] == "declined"
    assert real_card.writes() == []


def test_an_approve_of_one_card_never_approves_another(real_card) -> None:
    async def _answer(rid: str, card: dict) -> None:
        if card["context"] == f"Raj = {RAJ}":
            assert _resolve(rid) is True
        else:
            assert _resolve(rid, "REJECT") is True

    with ap.member_typed(RAJ_AND_PRIYA):
        results, _ = _run(_in_live_run(_two_aliases(), _answer))
    assert _alias_posts(real_card) == [{"alias": "Raj", "email": RAJ}]
    assert _status_of(results[1])["reason"] == "declined"


@pytest.mark.parametrize("call,body", [
    (lambda: pt.profile_remember("Prefers headlines before details", "preference"),
     {"text": "Prefers headlines before details", "kind": "preference"}),
    (lambda: pt.profile_set("address", {"name": "Annie Rao"}), None),
])
def test_a_card_grounds_a_fact_and_a_name(real_card, call, body) -> None:
    """A rephrase of the member's words (incident 2026-10-27) asks one card
    with the exact text, and the approve writes exactly that."""
    async def _approve(rid: str, card: dict) -> None:
        assert _resolve(rid) is True

    with ap.member_typed("give me the headline first please"):
        (result,), (card,) = _run(_in_live_run([call], _approve))
    assert pt.ASK_NOT_TYPED in card["detail"]
    (write,) = real_card.writes()
    if body is not None:
        assert card["context"] == f"{body['kind']}: {body['text']}"
        assert json.loads(write.content) == body
    else:
        assert json.loads(write.content)["value"]["name"] == "Annie Rao"
        assert '"name": "Annie Rao"' in card["context"]
    assert _undo_of(result)["op"] in ("delete", "put")


def test_the_route_takes_the_responder_from_the_session(monkeypatch) -> None:
    """``POST /agent/respond-input`` names the answerer from the session. A
    ``responder`` in the body is no field of the request, so a body cannot
    name the member who approves."""
    from types import SimpleNamespace

    from acb_auth import UserContext, UserRole
    from acb_skills import reply_card
    from gateway.routes import agent as agent_routes
    from orchestrator import executor as ex

    assert "responder" not in agent_routes.UserInputResponseRequest.model_fields
    monkeypatch.setattr(agent_routes, "_resolve_room",
                        lambda *_a, **_k: SimpleNamespace(can_send=True))

    async def _no_record(*_a: Any, **_k: Any) -> bool:
        return False

    monkeypatch.setattr(reply_card, "record_reply_answer", _no_record)

    async def _go() -> dict:
        fut = asyncio.get_running_loop().create_future()
        ex._pending_user_input.park("a" * 32, fut, THREAD)
        try:
            req = agent_routes.UserInputResponseRequest.model_validate(
                {"request_id": "a" * 32, "answer": "APPROVE", "thread_id": THREAD,
                 "responder": "eve@alpha.example"})
            await agent_routes.respond_user_input(
                req, user=UserContext(email=MEMBER.upper(), role=UserRole.EMPLOYEE))
            return fut.result()
        finally:
            ex._pending_user_input.pop("a" * 32, None)

    assert _run(_go())["responder"] == MEMBER


def test_marked_session_no_card_ground(own_chat, monkeypatch) -> None:
    """A marked session refuses before any card. A session that turns marked
    while the card waits writes nothing after the approve."""
    async def _marked(ctx: Any = None) -> bool:
        return True

    monkeypatch.setattr(egress, "session_is_marked", _marked)
    with ap.member_typed("Raj"):
        out = _run(pt.profile_alias("Raj", RAJ))
    assert _refused(out, "marked") == pt.MARKED
    assert own_chat.cards == [] and own_chat.requests == []

    seen: list[int] = []

    async def _later(ctx: Any = None) -> bool:
        seen.append(1)
        return len(seen) > 1  # clean at the gate, marked after the card

    monkeypatch.setattr(egress, "session_is_marked", _later)
    with ap.member_typed("Raj"):
        out = _run(pt.profile_alias("Raj", RAJ))
    assert _refused(out, "marked") == pt.MARKED
    assert len(own_chat.cards) == 1 and own_chat.writes() == []


def test_a_read_while_the_card_waits_closes_the_write(own_chat, monkeypatch) -> None:
    from acb_skills import ask_tools

    async def _card(title: str, detail: str = "", context: str = "", **kw: Any) -> Any:
        ap.close_writes()  # another tool of the turn read other content
        return True

    monkeypatch.setattr(ask_tools, "request_confirmation", _card)
    with ap.member_typed("Raj"):
        out = _run(pt.profile_alias("Raj", RAJ))
    assert _refused(out, "closed_by_read") == pt.CLOSED_BY_READ
    assert own_chat.writes() == []


@pytest.mark.parametrize("where", ["no_card", "whatsapp"])
def test_no_card_means_the_typed_words_still_rule(own_chat, where) -> None:
    own_chat.card_channel = False
    with ap.member_typed(RAJ_AND_PRIYA):
        if where == "whatsapp":
            own_chat.card_channel = True
            with ap.profile_channel("whatsapp", writes=True):
                out = _run(pt.profile_alias("Raj", RAJ))
        else:
            out = _run(pt.profile_alias("Raj", RAJ))
    assert _refused(out, "not_your_words") == pt.NOT_YOUR_WORDS_TEXT
    assert own_chat.cards == [] and own_chat.writes() == []


# ── B, C, D. The fact line, an unknown area, the sitting ───────────────────


def test_fact_line_prints_area() -> None:
    raw = {"facts": [
        {"id": "f1", "text": "Ship the CRM import", "kind": "work", "area": "goals",
         "at": "2026-10-25T10:00:00Z"},
        {"id": "f2", "text": "I like tea", "kind": "preference", "at": "2026-10-25T10:00:00Z"},
        {"id": "f3", "text": "Short answers", "kind": "preference", "area": "detail"},
    ]}
    lines = ap.render_block(raw).splitlines()
    assert "- [f1] Ship the CRM import (work, area goals, 2026-10-25)" in lines
    assert "- [f2] I like tea (preference, 2026-10-25)" in lines
    assert "- [f3] Short answers (preference, area detail)" in lines


@pytest.mark.parametrize("area", ["hobbies", "Goal", "name", "language"])
def test_unknown_area_refused(own_chat, area) -> None:
    out = _run(pt.profile_remember("I like tea", "preference", area=area))
    text = _refused(out, "invalid")
    assert "Saved" not in out and "Nothing saved" in text
    # Repair P2-5: the member reads only the first line. It names no tool
    # and no area id. The rest is for the model.
    member, model = text.split(f"\n{pt.FOR_ASSISTANT}", 1)
    assert member == pt.AREA_REFUSED == "I could not save that."
    if area in ap.SET_AREAS:
        assert "profile_set" in model
    else:
        assert all(a in model for a in ap.AREAS if a not in ap.SET_AREAS)
    assert own_chat.requests == [] and own_chat.interview_calls == []


def test_next_sitting_sets_last_asked() -> None:
    now = datetime(2026, 10, 30, 9, 0, 0, tzinfo=UTC)
    sat = ap.start_interview({"started_at": "2026-09-01T10:00:00Z",
                              "last_asked_at": "2026-09-01T10:00:00Z"}, now, sitting=True)
    stamp = now.strftime(ap.INTERVIEW_TIME)
    assert sat["sitting_at"] == sat["last_asked_at"] == stamp
    assert ap.due_area(sat, now + timedelta(hours=23, minutes=59)) == ""
    assert ap.due_area(sat, now + timedelta(hours=24, minutes=1)) == "name"
    # A "no thanks" is not a sitting: it sets no last_asked_at, so no due line.
    assert "last_asked_at" not in ap.start_interview(None, now)


# ── E. The token count of a native session keeps the interview memo ───────


def test_native_session_keeps_interview_memo(run, monkeypatch) -> None:
    import acb_llm
    from orchestrator import executor as ex
    from orchestrator import native_session_store

    now = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)
    run["raw"] = {**copy.deepcopy(epb.EXAMPLE), "interview": {
        "started_at": "2026-09-01T10:00:00Z", "last_asked_at": "2026-10-24T01:00:00Z"}}
    monkeypatch.setattr(ex, "_interview_now", lambda: now)
    monkeypatch.setattr(ex, "_native_sessions_enabled", lambda: True)
    monkeypatch.setattr(ex, "_native_session_skip", lambda *a, **k: None)
    counted: list[str] = []

    def _count(messages: list[dict], model: str = "") -> int:
        counted.append(messages[0]["content"])
        return 10

    async def _begin_turn(**kwargs: Any) -> str:
        return "turn"

    monkeypatch.setattr(acb_llm, "count_message_tokens", _count)
    monkeypatch.setattr(native_session_store, "begin_turn", _begin_turn)
    ex._INTERVIEW_SHOWN.clear()
    token = ap.bind_writes(True)
    try:
        turn = _run(ex._begin_native_session(
            None, "orchestrator", {"message": "hi"}, {}, run_id="r1",
            caller_thread_id="t-native", delegated=False, instance=""))
        assert turn == "turn"
        assert "Interview (one question is due)" in counted[0]
        assert ex._INTERVIEW_SHOWN == {}
        # The build that the model reads still gets the line, and records it.
        assert "Interview (one question is due)" in ex._run_context_text({}, {}, "orchestrator")
        assert set(ex._INTERVIEW_SHOWN) == {(epb.ORG, epb.MEMBER)}
    finally:
        ap.release_writes(token)
        ex._INTERVIEW_SHOWN.clear()


# ── F. The two areas in the table ──────────────────────────────────────────


def test_the_table_has_twelve_areas_with_name_and_language_first() -> None:
    assert len(ap.AREAS) == 12
    assert list(ap.AREAS)[:3] == ["name", "language", "role"]
    assert set(ap.AREA_INTENTS) == set(ap.AREAS)
    assert set(pt.NOT_MARKED_BY_AREA) == {"name", "language", "detail", "people"}
    assert ap.SET_AREAS == {"name": "address", "language": "style"}
    assert "today" not in ap.AREAS["good_day"]
    from typing import get_args

    from gateway.routes.admin.assistant_profile import AreaId

    assert list(get_args(AreaId)) == list(ap.AREAS)


@pytest.mark.parametrize("last,areas", [
    ({"op": "put", "key": "address", "after": {"name": "Ana"}, "prior": {}}, {"name"}),
    ({"op": "put", "key": "address", "after": {"name": "Ana", "formality": "formal"},
      "prior": {"name": "Ana"}}, {"name"}),
    ({"op": "put", "key": "address", "after": {"greeting": "warm"}, "prior": {}}, set()),
    ({"op": "put", "key": "style", "after": {"language": "hi-IN"}, "prior": {}}, {"language"}),
    ({"op": "put", "key": "style", "after": {"length": "short", "language": "hi-IN"},
      "prior": {"length": "short"}}, {"language"}),
    ({"op": "put", "key": "style", "after": {"length": "short", "language": "en"},
      "prior": {"language": "en"}}, {"detail"}),
    ({"op": "put", "key": "style", "after": {"length": "short", "language": "hi"},
      "prior": {}}, {"detail", "language"}),
    # A record with no value answers detail for style, as before PZ-32.
    ({"op": "put", "key": "style"}, {"detail"}),
    ({"op": "put", "key": "address"}, set()),
    ({"op": "post", "list": "aliases"}, set()),
])
def test_a_put_answers_its_own_areas(last, areas) -> None:
    assert set(ap.put_areas(last)) == areas


def test_a_fact_never_answers_name_or_language() -> None:
    t0, t1 = "2026-10-20T10:00:00Z", "2026-10-27T10:00:00Z"
    for area in ("name", "language"):
        profile = {"facts": [{"id": "f1", "text": "Call me Ana", "kind": "about",
                              "area": area, "at": t1}],
                   "interview": {"started_at": t0,
                                 "areas": {area: {"state": "asked", "at": t1}}}}
        assert ap.area_has_answer(profile, area) is False


# ── G. The persona and the offer to remember ───────────────────────────────


def test_persona_no_auto_extract_claim() -> None:
    mod = _orchestrator_module()
    on = mod._orchestrator_instructions(False, profile=True)
    for persona in (mod._PULL_INSTRUCTIONS, on):
        assert "auto-extract" not in persona
        rule8 = persona[persona.index("\n8. "):persona.index("\n9. ")]
        assert "remember(" not in rule8
    flat = " ".join(mod._PROFILE_CARD_SWAP[1].split())
    assert "call profile_remember with offer=true" in flat
    assert "The card is the question, so do not ask in words first." in flat
    assert "Do not offer on WhatsApp." in flat
    assert "Jev" not in flat
    # Repair P2-8: the offer line comes only with the card writes on.
    assert "offer=true" not in mod._PROFILE_SWAP[1]
    assert "offer=true" not in on
    cards = mod._orchestrator_instructions(False, profile=True, card_writes=True)
    assert cards == on.replace(*mod._PROFILE_CARD_SWAP, 1) != on
    assert mod._orchestrator_instructions(False, card_writes=True) == mod._PULL_INSTRUCTIONS


class _FakeRedis:
    """The tenant Redis of the offer: one ``SET NX`` store."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.fail = False

    async def set(self, k: Any, value: Any, nx: bool = False, ex: int | None = None) -> Any:
        if self.fail:
            raise ConnectionError("redis is down")
        raw = k.value
        assert ex == pt.OFFER_TTL_S
        if nx and raw in self.store:
            return None
        self.store[raw] = value
        return True


@pytest.fixture()
def offer_chat(own_chat, monkeypatch):
    """The member's own web chat, with a tenant and a session for the offer."""
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
    from orchestrator import executor as ex

    redis = _FakeRedis()
    monkeypatch.setattr(pt, "get_tenant_redis", lambda: redis)
    assert ex._current_run_org() == ORG  # `_cards_on` binds it
    # Repair P1-3: only the typed words ground an offer.
    with artifact_context_scope(), ap.member_typed(OFFER_WORDS):
        bind_artifact_context(session_id="chat-1")
        own_chat.redis = redis
        yield own_chat


def _offer(text: str = "I run the Pune plant", kind: str = "work") -> Any:
    return pt.profile_remember(text, kind, offer=True)


def test_offer_once_per_chat(offer_chat) -> None:
    out = _run(_offer())
    assert _undo_of(out)["op"] == "delete"
    (card,) = offer_chat.cards
    assert card["title"] == pt.OFFER_TITLE and card["context"] == "work: I run the Pune plant"
    assert pt.OFFER_DETAIL in card["detail"]
    (key,) = offer_chat.redis.store
    assert key.startswith(f"cc:{ORG}:profile-offer:chat-1:") and MEMBER not in key
    # The second offer of the chat asks nothing and saves nothing.
    again = _run(_offer("I like long walks", "preference"))
    assert _refused(again, "no_offer") == pt.OFFER_USED
    assert len(offer_chat.cards) == 1 and len(offer_chat.writes()) == 1
    # Another chat may offer once.
    from acb_skills.write_artifact import bind_artifact_context

    bind_artifact_context(session_id="chat-2")
    assert _undo_of(_run(_offer("I like long walks", "preference")))["op"] == "delete"


def test_an_offer_saves_nothing_on_no(offer_chat) -> None:
    offer_chat.approve = False
    out = _run(_offer())
    assert _refused(out, "declined") == pt.DECLINED_TEXT
    assert offer_chat.writes() == [] and len(offer_chat.cards) == 1
    # The no counts as the offer of this chat.
    offer_chat.approve = True
    assert _status_of(_run(_offer("I like tea", "preference")))["reason"] == "no_offer"
    assert offer_chat.writes() == []


def test_a_sensitive_offer_needs_the_second_yes(offer_chat, monkeypatch) -> None:
    from acb_skills import ask_tools

    answers = iter([True, False])

    async def _card(title: str, detail: str = "", context: str = "", **kw: Any) -> Any:
        offer_chat.cards.append({"title": title, "detail": detail, "context": context})
        return next(answers)

    monkeypatch.setattr(ask_tools, "request_confirmation", _card)
    out = _run(_offer("I have diabetes", "about"))
    assert _status_of(out)["reason"] == "declined"
    assert offer_chat.writes() == []
    first, second = offer_chat.cards
    assert first["title"] == pt.OFFER_TITLE and pt.ASK_SENSITIVE not in first["detail"]
    assert pt.ASK_SENSITIVE in second["detail"]
    assert first["context"] == second["context"] == "about: I have diabetes"


def test_a_sensitive_offer_saves_on_two_yeses(offer_chat) -> None:
    out = _run(_offer("I have diabetes", "about"))
    assert _undo_of(out)["op"] == "delete"
    assert len(offer_chat.cards) == 2


def test_an_offer_never_asks_for_a_fact_that_is_saved(offer_chat) -> None:
    # The fake profile holds "I work from Pune" as a work fact.
    out = _run(_offer("I work from Pune", "about"))
    assert _refused(out, "duplicate") == "Already saved."
    assert offer_chat.cards == [] and offer_chat.redis.store == {}


@pytest.mark.parametrize("why", ["no_card", "whatsapp", "redis_down", "no_session"])
def test_an_offer_with_no_card_or_no_record_asks_nothing(offer_chat, why) -> None:
    from acb_skills.write_artifact import bind_artifact_context

    if why == "no_card":
        offer_chat.card_channel = False
    if why == "redis_down":
        offer_chat.redis.fail = True
    if why == "no_session":
        bind_artifact_context()
    if why == "whatsapp":
        with ap.profile_channel("whatsapp", writes=True):
            out = _run(_offer())
    else:
        out = _run(_offer())
    assert _status_of(out)["reason"] == "no_offer"
    assert offer_chat.cards == [] and offer_chat.writes() == []


@pytest.mark.parametrize("gate", ["shared_room", "learning_off", "marked"])
def test_an_offer_keeps_every_gate(offer_chat, monkeypatch, gate) -> None:
    if gate == "shared_room":
        monkeypatch.setattr(pt, "_current_user_email", lambda: "room:thread-1")
    if gate == "learning_off":
        offer_chat.profile["learning"] = False

    async def _marked(ctx: Any = None) -> bool:
        return True

    if gate == "marked":
        monkeypatch.setattr(egress, "session_is_marked", _marked)
    out = _run(_offer())
    assert _status_of(out)["reason"] in ("not_your_turn", "learning_off", "marked")
    assert offer_chat.cards == [] and offer_chat.writes() == []
    assert offer_chat.redis.store == {}


# ── H. The block cap with the area tags ────────────────────────────────────


def test_render_cap_with_area_tags() -> None:
    areas = list(ap.AREAS)
    facts = [{"id": f"f{n:03d}", "text": f"fact {n} " + "x" * 260, "kind": "work",
              "area": areas[n % len(areas)], "at": f"2026-10-{(n % 28) + 1:02d}T10:00:00Z"}
             for n in range(ap.FACTS_MAX)]
    raw = {"address": {"name": "Ana", "formality": "casual"},
           "style": {"length": "short", "language": "hi-IN", "notes": ["n" * 200] * 5},
           "aliases": [{"id": f"a{n}", "alias": f"Al{n}", "email": f"a{n}@x.example"}
                       for n in range(10)],
           "facts": facts}
    assert {f["area"] for f in facts} == set(areas)
    block = ap.render_block(raw)
    assert len(block) <= ap.RENDER_MAX
    fact_lines = [ln for ln in block.splitlines() if ln.startswith("- [f")]
    assert fact_lines, "non-vacuity: some facts fit"
    assert all(", area " in ln for ln in fact_lines)
    assert len(fact_lines) < ap.FACTS_MAX  # the cap shed the oldest facts


# ── I. Repair round 1 ──────────────────────────────────────────────────────
#
# I.1 (P1-1): a stop holds, a stop from before PZ-32 holds, a reset ends it.

_T0 = "2026-09-01T10:00:00Z"
_LATER = datetime(2026, 12, 1, 9, 0, 0, tzinfo=UTC)
_NOW = datetime(2026, 10, 30, 9, 0, 0, tzinfo=UTC)


def _old_stop(never: str = "goals") -> dict[str, Any]:
    """A row that a stop before PZ-32 wrote: each old area is answered or
    never, there is no ``stopped_at``, and ``name`` and ``language`` have no
    entry."""
    return {"started_at": _T0, "last_asked_at": _T0, "areas": {
        a: {"state": "never" if a == never else "answered", "at": _T0} for a in ap.OLD_AREAS}}


def test_an_old_stopped_row_asks_nothing() -> None:
    row = _old_stop()
    assert "stopped_at" not in row and ap.interview_stopped(row)
    assert ap.open_areas(row, _LATER) == []
    assert ap.due_area(row, _LATER) == ""
    assert ap.interview_due_line(row, _LATER) == ""
    sitting = {**row, "sitting_at": _LATER.strftime(ap.INTERVIEW_TIME)}
    assert ap.may_ask(sitting, _LATER) is False
    # Not a stop: each old area answered, with no never. Or one old area open.
    answered = _old_stop(never="")
    assert not ap.interview_stopped(answered)
    assert ap.due_area(answered, _LATER) == "name"
    one_open = copy.deepcopy(row)
    del one_open["areas"]["role"]
    assert not ap.interview_stopped(one_open)
    assert ap.open_areas(one_open, _LATER) == ["name", "language", "role"]


def test_a_new_stop_writes_stopped_at_and_holds_for_a_later_area() -> None:
    before = {"started_at": _T0, "last_asked_at": _T0, "areas": {
        "role": {"state": "answered", "at": _T0}, "goals": {"state": "skipped", "at": _T0}}}
    stopped = ap.stop_interview(before, _NOW)
    assert stopped["stopped_at"] == _NOW.strftime(ap.INTERVIEW_TIME)
    assert stopped["areas"]["role"]["state"] == "answered"
    assert all(stopped["areas"][a]["state"] == "never" for a in ap.AREAS if a != "role")
    # An area of a later release has no entry. The stop still holds for it.
    later = copy.deepcopy(stopped)
    del later["areas"]["name"]
    assert ap.interview_stopped(later)
    assert ap.open_areas(later, _LATER) == [] and ap.due_area(later, _LATER) == ""
    # The lenient read keeps the key, so the block and the tools see it.
    assert ap.stored_profile({"interview": later})["interview"]["stopped_at"]


@pytest.mark.parametrize("row", ["new", "old"])
def test_a_reset_of_one_area_after_a_stop_asks_it_again(row) -> None:
    stopped = ap.stop_interview(_old_stop(), _NOW) if row == "new" else _old_stop()
    reset = ap.reset_interview_area(stopped, "goals", _NOW)
    assert "stopped_at" not in reset and not ap.interview_stopped(reset)
    assert reset["reset_at"] == _NOW.strftime(ap.INTERVIEW_TIME)
    # Only the area that the member reset opens. The `name` and `language`
    # of an old stop stay closed.
    assert ap.open_areas(reset, _LATER) == ["goals"]
    assert ap.due_area(reset, _LATER) == "goals"
    with pytest.raises(ValueError):
        ap.reset_interview_area(stopped, "hobbies", _NOW)


def test_a_reset_with_no_stop_opens_only_that_area() -> None:
    row = {"started_at": _T0, "areas": {"goals": {"state": "answered", "at": _T0}}}
    reset = ap.reset_interview_area(row, "goals", _NOW)
    assert reset["areas"] == {} and "stopped_at" not in reset
    assert ap.open_areas(reset, _LATER)[:3] == ["name", "language", "role"]


def test_the_stop_tool_writes_once_and_the_chat_asks_nothing_after(own_chat) -> None:
    own_chat.profile["interview"] = _old_stop()
    out = _run(pt.profile_interview("stop"))
    assert own_chat.interview_calls == [("POST", "", {"stop": True})]
    assert own_chat.profile["interview"]["stopped_at"]
    assert "Stopped. 2 areas" in out  # `name` and `language` of the old stop
    assert _run(pt.profile_interview("next")) == pt.NO_OPEN_AREA


# I.2 (P1-2): a sensitive value that the member did not type asks twice.


def _answers(own_chat: Any, monkeypatch: pytest.MonkeyPatch, answers: list[Any]) -> None:
    from acb_skills import ask_tools

    queue = iter(answers)

    async def _card(title: str, detail: str = "", context: str = "", **kw: Any) -> Any:
        own_chat.cards.append({"title": title, "detail": detail, "context": context})
        return next(queue)

    monkeypatch.setattr(ask_tools, "request_confirmation", _card)


@pytest.mark.parametrize("call", [
    lambda: pt.profile_remember("I have diabetes", "about"),
    lambda: pt.profile_set("address", {"name": "Annie Rao"}, sensitive=True),
], ids=["profile_remember", "profile_set"])
def test_a_sensitive_value_not_typed_needs_the_second_yes(own_chat, monkeypatch, call) -> None:
    _answers(own_chat, monkeypatch, [True, False])
    with ap.member_typed("please keep that"):
        out = _run(call())
    assert _status_of(out)["reason"] == "declined"
    assert own_chat.writes() == []
    first, second = own_chat.cards
    assert pt.ASK_NOT_TYPED in first["detail"] and pt.ASK_SENSITIVE not in first["detail"]
    assert second["detail"] == f"{pt.ASK_SENSITIVE} {pt.CONFIRM_CHECK}"
    assert first["context"] == second["context"]
    # Two yeses write it, once.
    own_chat.cards.clear()
    _answers(own_chat, monkeypatch, [True, True])
    with ap.member_typed("please keep that"):
        assert _undo_of(_run(call()))["op"] in ("delete", "put")
    assert len(own_chat.cards) == 2 and len(own_chat.writes()) == 1


def test_a_typed_sensitive_fact_still_asks_one_card(own_chat) -> None:
    with ap.member_typed("I have diabetes"):
        out = _run(pt.profile_remember("I have diabetes", "about"))
    assert _undo_of(out)["op"] == "delete"
    (card,) = own_chat.cards
    assert pt.ASK_SENSITIVE in card["detail"] and pt.ASK_NOT_TYPED not in card["detail"]


# I.3 (P1-3): only the member's typed words ground an offer.


@pytest.mark.parametrize("typed", ["something else entirely", ""])
def test_an_offer_of_text_not_typed_gets_no_card(offer_chat, typed) -> None:
    with ap.member_typed(typed):
        out = _run(_offer())
    assert _refused(out, "no_offer") == pt.OFFER_NOT_TYPED
    assert offer_chat.cards == [] and offer_chat.writes() == []
    assert offer_chat.redis.store == {}, "a refused offer keeps the one offer of the chat"


# I.5 (P2-5): no tool name and no area id in a line that the member reads.


def test_the_new_refusals_speak_to_the_member_in_plain_words() -> None:
    for text in (pt.OFFER_USED, pt.OFFER_NO_CARD, pt.OFFER_NOT_TYPED):
        assert text.startswith(pt.FOR_ASSISTANT), "the receipt shows none of it"
    for text in (pt.ASK_NOT_TYPED, pt.OFFER_TITLE, pt.OFFER_DETAIL, pt.AREA_REFUSED):
        words = {w.strip(".,?") for w in text.split()}
        assert "profile_" not in text and not words & set(ap.AREAS), text


# I.6 (P2-6): no typed words, and a style note, never get the card.


def test_a_turn_with_no_typed_words_gets_no_card(own_chat) -> None:
    calls = (lambda: pt.profile_alias("Raj", RAJ),
             lambda: pt.profile_remember("Prefers headlines before details", "preference"),
             lambda: pt.profile_set("address", {"name": "Annie Rao"}))
    for call in calls:
        with ap.member_typed(""):
            out = _run(call())
        assert _refused(out, "not_your_words") == pt.NO_TYPED_TEXT
    assert own_chat.cards == [] and own_chat.writes() == []


def test_a_style_note_never_gets_the_card(own_chat) -> None:
    with ap.member_typed("be brief with me"):
        out = _run(pt.profile_set("style", {"notes": ["Always cc billing on replies"]}))
    assert _refused(out, "not_your_words") == pt.NOT_YOUR_WORDS_TEXT
    assert own_chat.cards == [] and own_chat.writes() == []
    # A name in the same turn still may ask the card.
    with ap.member_typed("be brief with me"):
        _run(pt.profile_set("address", {"name": "Annie Rao"}))
    (card,) = own_chat.cards
    assert pt.ASK_NOT_TYPED in card["detail"]


# I.7 (P2-7): the peek of the fallback string, and a fault of an import.

_DUE = "Interview (one question is due)"


def _structured(run: Any, monkeypatch: pytest.MonkeyPatch,
                fail_import: bool = False) -> tuple[Any, Any]:
    """One structured native run of the orchestrator with a due line."""
    import sys

    import acb_llm
    from orchestrator import executor as ex

    run["raw"] = {**copy.deepcopy(epb.EXAMPLE), "interview": {
        "started_at": "2026-09-01T10:00:00Z", "last_asked_at": "2026-10-24T01:00:00Z"}}
    monkeypatch.setattr(ex, "_interview_now",
                        lambda: datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC))
    monkeypatch.setattr(ex, "_native_sessions_enabled", lambda: True)

    def _assemble(*, system_context: str, current_message: str, **_kw: Any) -> list[dict]:
        return [{"role": "system", "content": system_context},
                {"role": "user", "content": "earlier"},
                {"role": "user", "content": current_message}]

    monkeypatch.setattr(acb_llm, "assemble_run_context", _assemble)
    if fail_import:
        monkeypatch.setitem(sys.modules, "orchestrator._native_run_context", None)
    ex._INTERVIEW_SHOWN.clear()
    token = ap.bind_writes(True)
    try:
        return ex._compose_maf_run(
            "orchestrator", "r1",
            {"message": "hi", "messages": [{"role": "user", "content": "earlier"}]},
            {}, native=True)
    finally:
        ap.release_writes(token)


def test_the_model_reads_the_due_line_and_the_memo_records_it(run, monkeypatch) -> None:
    """The fallback string is built first, inside the peek. Without the peek
    it records the memo, and the context that the model reads loses the
    line."""
    from orchestrator import executor as ex

    try:
        run_input, provider = _structured(run, monkeypatch)
        assert provider is not None and isinstance(run_input, list), "the structured branch"
        assert _DUE in provider.text
        assert set(ex._INTERVIEW_SHOWN) == {(epb.ORG, epb.MEMBER)}
    finally:
        ex._INTERVIEW_SHOWN.clear()


def test_an_import_fault_still_records_the_memo(run, monkeypatch) -> None:
    """The model then reads the fallback string, which holds the line. The
    memo is recorded, so the line does not come again in each turn."""
    from orchestrator import executor as ex

    try:
        run_input, provider = _structured(run, monkeypatch, fail_import=True)
        assert provider is None and isinstance(run_input, str)
        assert _DUE in run_input
        assert set(ex._INTERVIEW_SHOWN) == {(epb.ORG, epb.MEMBER)}
    finally:
        ex._INTERVIEW_SHOWN.clear()


# I.8 (P2-8): the two new write paths are dark behind one per-org flag.


def test_card_writes_for_reads_the_org_list(monkeypatch) -> None:
    other = "22222222-2222-4222-8222-222222222222"
    _card_writes(monkeypatch, "")
    assert ap.card_writes_for(ORG) is False
    _card_writes(monkeypatch, "*")
    assert ap.card_writes_for(ORG) is True
    assert ap.card_writes_for("") is False and ap.card_writes_for(None) is False
    _card_writes(monkeypatch, f"{ORG.upper()}, 11111111-1111-4111-8111-111111111111")
    assert ap.card_writes_for(ORG) is True and ap.card_writes_for(other) is False
    # A per-org list, so a SIGHUP reload applies it, and no tenant writes it.
    from acb_common import env_guard
    from gateway.flag_reload import RELOADABLE

    assert "ASSISTANT_PROFILE_CARD_WRITES_ORGS" in RELOADABLE
    assert env_guard.is_platform_env("ASSISTANT_PROFILE_CARD_WRITES_ORGS")


@pytest.mark.parametrize("flag", ["", "11111111-1111-4111-8111-111111111111"],
                         ids=["empty", "another_org"])
def test_flag_off_a_value_not_typed_refuses_as_before(own_chat, monkeypatch, flag) -> None:
    _card_writes(monkeypatch, flag)
    with ap.member_typed("Raj"):
        alias = _run(pt.profile_alias("Raj", RAJ))
    with ap.member_typed("give me the headline first please"):
        fact = _run(pt.profile_remember("Prefers headlines before details", "preference"))
        name = _run(pt.profile_set("address", {"name": "Annie Rao"}))
    for out in (alias, fact, name):
        assert _refused(out, "not_your_words") == pt.NOT_YOUR_WORDS_TEXT
    assert own_chat.cards == [] and own_chat.writes() == []


def test_flag_off_an_offer_asks_nothing(offer_chat, monkeypatch) -> None:
    _card_writes(monkeypatch, "")
    out = _run(_offer())
    assert _refused(out, "no_offer") == pt.OFFER_NO_CARD
    assert offer_chat.cards == [] and offer_chat.writes() == []
    assert offer_chat.redis.store == {}
    # On for the org, the same offer asks its one card.
    _card_writes(monkeypatch, ORG)
    assert _undo_of(_run(_offer()))["op"] == "delete"
    assert len(offer_chat.cards) == 1


def test_the_offer_line_follows_the_org(monkeypatch) -> None:
    from tests.unit.test_profile_write_tools import _build

    mod = _orchestrator_module()
    org = "11111111-1111-4111-8111-111111111111"  # the org that `_build` passes
    _card_writes(monkeypatch, "")
    off = _build(monkeypatch, True)
    _card_writes(monkeypatch, org)
    on = _build(monkeypatch, True)
    old, new = mod._PROFILE_CARD_SWAP
    assert "offer=true" not in off.kwargs["instructions"]
    assert on.kwargs["instructions"] == off.kwargs["instructions"].replace(old, new, 1)
    assert "offer=true" in on.kwargs["instructions"]
    # The profile off: no profile line at all, so no offer line either.
    assert _build(monkeypatch, False).kwargs["instructions"] is mod._PULL_INSTRUCTIONS
