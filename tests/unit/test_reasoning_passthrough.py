"""A thinking model can hold a tool-calling conversation. H-179.

Spec: ``customer_console.md`` §6A (the Router owns the vendor seam).

🔴 **The Projects chat broke the minute the Router started serving.** Owner
report, 2026-09-24. Four calls succeeded and the fifth returned 400::

    The `reasoning_content` in the thinking mode must be passed back to the API.

DeepSeek's thinking models hand back their reasoning in ``reasoning_content``
and demand it on every later assistant turn. The agent framework keeps
reasoning under ``reasoning_details`` — OpenRouter's name — so it saw nothing,
kept nothing, and returned nothing.

⚠️ **THE STUB AGREES WITH WHATEVER IT IS HANDED, and that is how this class of
bug shipped green before.** ``routed.py`` once sent a field the Console
forbids, and its suite stayed green because it stubbed the client. So the
shapes below were MEASURED against the live vendor first, on 2026-09-24, and
the table in :mod:`customer_console.reasoning` records all four probes. These
tests pin the shapes that probe proved. They do not re-prove them.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_reasoning_passthrough.py -v -rs
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("fastapi")

from customer_console.reasoning import (
    publish_reasoning_alias,
    reasoning_for_vendor,
)

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

_NEEDS_DB = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)


def _assistant(call_id: str, **extra):
    """One assistant turn that called a tool, as the framework emits it."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":"Pune"}'},
        }],
        **extra,
    }


class TestWhatGoesToTheVendor:
    """``reasoning_for_vendor`` — the framework's spelling becomes the
    vendor's."""

    def test_the_WHOLE_HISTORY_is_translated_not_just_the_last_turn(self):
        """🔴 Measured against the live vendor, and the reason this walks the
        list.

        A probe that carried reasoning on the FINAL assistant turn alone was
        refused 400, exactly as one carrying none was. So a fix that patched
        only the newest message would have looked right, passed a careless
        test, and still failed on the second tool call — which is precisely
        the call the owner's chat died on.
        """
        history = [
            {"role": "user", "content": "weather in Pune then Nashik"},
            _assistant("call_1", reasoning_details="first I check Pune"),
            {"role": "tool", "tool_call_id": "call_1", "content": "28C"},
            _assistant("call_2", reasoning_details="now Nashik"),
            {"role": "tool", "tool_call_id": "call_2", "content": "31C"},
        ]
        out = reasoning_for_vendor(history)
        carried = [m.get("reasoning_content") for m in out if m["role"] == "assistant"]
        assert carried == ["first I check Pune", "now Nashik"], (
            "an earlier assistant turn lost its reasoning, which the vendor "
            "refuses just as hard as losing the last one")

    def test_a_plain_STRING_becomes_the_vendor_field(self):
        """What :func:`publish_reasoning_alias` writes, round-tripped by the
        framework verbatim."""
        out = reasoning_for_vendor([_assistant("c1", reasoning_details="I thought")])
        assert out[0]["reasoning_content"] == "I thought"

    def test_an_OPENROUTER_shaped_LIST_is_flattened(self):
        """The other producer of this key. A vendor that speaks
        ``reasoning_details`` natively sends parts, not a string, and the list
        reaches us unchanged through the framework."""
        out = reasoning_for_vendor([_assistant("c1", reasoning_details=[
            {"type": "reasoning.text", "text": "step one"},
            {"type": "reasoning.text", "text": "step two"},
        ])])
        assert out[0]["reasoning_content"] == "step one\nstep two"

    def test_an_EXISTING_reasoning_content_WINS(self):
        """A caller that already speaks the vendor's spelling is right.

        Overwriting it with our flattened copy would quietly replace what the
        caller meant with a lossy rendering of the same thing.
        """
        out = reasoning_for_vendor([_assistant(
            "c1", reasoning_content="the caller's own", reasoning_details="ours")])
        assert out[0]["reasoning_content"] == "the caller's own"

    def test_a_USER_or_TOOL_message_is_never_touched(self):
        """⚠️ The vendor asks for reasoning on ASSISTANT turns. Writing the key
        onto a user message invents a claim about who thought what."""
        msgs = [
            {"role": "user", "content": "hi", "reasoning_details": "not mine"},
            {"role": "tool", "tool_call_id": "c1", "content": "x",
             "reasoning_details": "nor mine"},
        ]
        assert reasoning_for_vendor(msgs) == msgs

    def test_the_CALLERS_LIST_IS_NOT_MUTATED(self):
        """🔴 A failover chain builds one body PER STEP from the same
        ``req.messages``.

        If this edited in place, step two would inherit step one's edit. Today
        that is harmless, because both steps want the same thing. It stops
        being harmless the moment a chain mixes two vendors, which
        ``tier_binding`` already permits.
        """
        original = [_assistant("c1", reasoning_details="mine")]
        before = [dict(m) for m in original]
        reasoning_for_vendor(original)
        assert original == before, "the request's own messages were edited"

    def test_a_conversation_with_NO_reasoning_is_returned_UNCHANGED(self):
        """Most traffic. A plain chat must not grow a field it never had."""
        msgs = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"}]
        assert reasoning_for_vendor(msgs) == msgs

    def test_an_UNREADABLE_shape_leaves_the_message_ALONE(self):
        """⚠️ Not ``""``. An empty string is a value the vendor accepts and
        learns nothing from, so it would hide a shape we failed to read behind
        a call that merely answers badly. Left alone, the vendor's own error
        names the problem."""
        for junk in (None, 12, {}, [], [{"no_text": 1}], ""):
            out = reasoning_for_vendor([_assistant("c1", reasoning_details=junk)])
            assert "reasoning_content" not in out[0], junk

    def test_an_EMPTY_conversation_does_not_explode(self):
        assert reasoning_for_vendor([]) == []


class TestTheSplitTurnIsRejoined:
    """🔴 Found by the adversarial review, then MEASURED against the vendor.

    When the model writes text AND calls a tool in one turn, the framework
    sends it back as two assistant messages, and only the second carries the
    reasoning. The vendor refused the pair with the same 400 this ticket
    exists to fix. The first version of this fix would have shipped with it.
    """

    def test_text_then_tool_calls_become_ONE_turn_with_the_reasoning(self):
        out = reasoning_for_vendor([
            {"role": "user", "content": "weather in Pune"},
            {"role": "assistant", "content": "Checking Pune."},
            _assistant("call_1", reasoning_details="Pune first"),
            {"role": "tool", "tool_call_id": "call_1", "content": "28C"},
        ])
        assistants = [m for m in out if m["role"] == "assistant"]
        assert len(assistants) == 1, "the split turn was left split"
        turn = assistants[0]
        assert turn["content"] == "Checking Pune."
        assert turn["tool_calls"][0]["id"] == "call_1"
        assert turn["reasoning_content"] == "Pune first"

    def test_EVERY_split_turn_in_the_history_is_rejoined(self):
        """The second tool round is where the owner's chat died."""
        out = reasoning_for_vendor([
            {"role": "user", "content": "Pune then Nashik"},
            {"role": "assistant", "content": "Pune first."},
            _assistant("call_1", reasoning_details="one"),
            {"role": "tool", "tool_call_id": "call_1", "content": "28C"},
            {"role": "assistant", "content": "Now Nashik."},
            _assistant("call_2", reasoning_details="two"),
            {"role": "tool", "tool_call_id": "call_2", "content": "31C"},
        ])
        assistants = [m for m in out if m["role"] == "assistant"]
        assert [a["reasoning_content"] for a in assistants] == ["one", "two"]
        assert [a["content"] for a in assistants] == ["Pune first.", "Now Nashik."]

    def test_a_FINISHED_answer_before_a_user_message_is_NOT_joined(self):
        """⚠️ Only an adjacent pair. A text answer followed by the user is a
        complete turn, and the vendor accepts it bare (the fourth probe).
        Joining across the user would invent a turn nobody took."""
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Hello!"},
            {"role": "user", "content": "weather in Pune"},
            _assistant("call_1", reasoning_details="look it up"),
        ]
        out = reasoning_for_vendor(msgs)
        assert [m["role"] for m in out] == ["user", "assistant", "user", "assistant"]
        assert out[1] == {"role": "assistant", "content": "Hello!"}

    def test_the_merge_does_not_EDIT_the_callers_messages(self):
        """Same property as the translation half, for the same failover
        reason."""
        original = [
            {"role": "assistant", "content": "Checking."},
            _assistant("call_1", reasoning_details="r"),
        ]
        before = [dict(m) for m in original]
        reasoning_for_vendor(original)
        assert original == before


class _Msg:
    pass


class _Choice:
    def __init__(self, message):
        self.message = message


class _Resp:
    def __init__(self, choices):
        self.choices = choices


def _response(**fields):
    msg = _Msg()
    for k, v in fields.items():
        setattr(msg, k, v)
    return _Resp([_Choice(msg)])


class TestWhatComesBackToTheCaller:
    """``publish_reasoning_alias`` — the vendor's spelling gains the
    framework's."""

    def test_the_vendors_reasoning_gains_the_frameworks_NAME(self):
        """🔴 Without this the framework never sees reasoning at all, so
        there is nothing for the other half to send back a turn later. The two
        functions are one mechanism."""
        resp = publish_reasoning_alias(_response(reasoning_content="I thought"))
        assert resp.choices[0].message.reasoning_details == "I thought"

    def test_it_NEVER_OVERWRITES_a_native_reasoning_details(self):
        """A vendor that speaks the framework's name already put the richer
        structure there, and a flattened string is worse."""
        native = [{"type": "reasoning.text", "text": "rich"}]
        resp = publish_reasoning_alias(
            _response(reasoning_content="flat", reasoning_details=native))
        assert resp.choices[0].message.reasoning_details == native

    def test_a_response_with_NO_reasoning_gains_nothing(self):
        resp = publish_reasoning_alias(_response(content="hello"))
        assert getattr(resp.choices[0].message, "reasoning_details", None) is None

    def test_a_real_litellm_MODELRESPONSE_carries_the_alias_when_SERIALISED(self):
        """🔴 The production type, not a fake of it.

        Setting an attribute proves nothing on its own. The question is
        whether the field survives the trip into JSON, because that JSON is
        all the framework ever sees.
        """
        import json

        litellm = pytest.importorskip("litellm")
        resp = litellm.ModelResponse(**{
            "id": "x", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": "done",
                "reasoning_content": "I worked it out"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })
        publish_reasoning_alias(resp)
        wire = json.loads(resp.model_dump_json())
        assert wire["choices"][0]["message"]["reasoning_details"] == "I worked it out"

    def test_a_DICT_response_carries_the_alias_too(self):
        """🔴 The shape a first version skipped in silence.

        The route relays whatever the provider call returns, and that can be a
        mapping. Reading attributes alone found no ``choices`` on a dict and
        did nothing.
        """
        resp = {"choices": [{"message": {"reasoning_content": "I thought"}}]}
        publish_reasoning_alias(resp)
        assert resp["choices"][0]["message"]["reasoning_details"] == "I thought"

    def test_a_SHAPE_IT_CANNOT_WALK_never_costs_the_completion(self):
        """⚠️ The customer has already been charged by the time this runs.
        Losing the reasoning is a degraded answer. Raising here would lose the
        answer itself, for a field rename."""
        for junk in (None, object(), _Resp("not-a-list"), _Resp([object()])):
            publish_reasoning_alias(junk)  # must not raise


class TestTheTwoHalvesCompose:
    def test_a_ROUND_TRIP_restores_what_the_vendor_needs(self):
        """The whole mechanism, end to end, in the order it really runs.

        The vendor answers with its own spelling, we publish the alias, the
        framework hands that alias back on the next turn, and the vendor sees
        its own spelling again.
        """
        served = publish_reasoning_alias(_response(reasoning_content="I thought"))
        alias = served.choices[0].message.reasoning_details

        # What the framework sends on the NEXT turn: it knows only this key.
        next_turn = [_assistant("c1", reasoning_details=alias)]
        assert reasoning_for_vendor(next_turn)[0]["reasoning_content"] == "I thought"


@_NEEDS_DB
class TestTheServingRouteUsesBoth:
    """🔴 The unit tests above prove the functions. This proves they are
    WIRED, which is the half a green suite has hidden here before."""

    @staticmethod
    def _client_and_calls(monkeypatch):
        from customer_console import router as router_mod
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine, text

        from tests.unit._customer_console_ladder import (
            DEFAULT_DEPLOYMENT_LABEL,
            apply_ladder,
            ensure_deployment,
        )

        token = "test-operator-token"
        enc = "test-encryption-key-not-a-real-one"
        monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", token)
        monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", enc)

        eng = create_engine(_URL, future=True)
        with eng.begin() as conn:
            apply_ladder(conn)
            ensure_deployment(conn)

        seen: list[dict] = []

        async def _stub(**kwargs):
            seen.append(kwargs)
            return {
                "id": "chatcmpl-1", "object": "chat.completion",
                "created": 1_755_000_000, "model": "deepseek/deepseek-v4-pro",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": "done",
                    "reasoning_content": "I worked it out"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }

        # ⚠️ Swapped through monkeypatch, so teardown puts the real call
        # back. A stub left installed answers every suite that runs later.
        # NOT `set_provider_call`: it edits the module's list in place, so
        # restoring the list object would restore the stub along with it.
        monkeypatch.setattr(router_mod, "_PROVIDER_CALL", [_stub])

        from customer_console.main import app
        client = TestClient(app)

        slug = f"reason-{uuid.uuid4().hex[:8]}"
        op = {"Authorization": f"Bearer {token}"}
        client.post("/orgs/provision", headers=op, json={
            "slug": slug, "name": "N", "owner_email": f"o@{slug}.com",
            "deployment_label": DEFAULT_DEPLOYMENT_LABEL})
        key = client.post("/keys", headers=op, json={"org_slug": slug}).json()["token"]
        with eng.begin() as c:
            c.execute(
                text("INSERT INTO provider_credential (provider, secret_enc, label) "
                     "VALUES ('deepseek', :s, 'platform') ON CONFLICT DO NOTHING"),
                {"s": router_mod.encrypt_secret("sk-not-a-real-secret")})
        eng.dispose()
        return client, seen, {"Authorization": f"Bearer {key}"}

    def test_the_route_TRANSLATES_on_the_way_out_and_MIRRORS_on_the_way_back(
        self, monkeypatch
    ):
        client, seen, auth = self._client_and_calls(monkeypatch)

        r = client.post("/v1/chat/completions", headers=auth, json={
            "model": "tier-balanced", "max_tokens": 32,
            "messages": [
                {"role": "user", "content": "weather in Pune"},
                _assistant("call_1", reasoning_details="I check Pune"),
                {"role": "tool", "tool_call_id": "call_1", "content": "28C"},
            ]})
        assert r.status_code == 200, r.text

        # Outbound: the vendor got its own spelling.
        assert seen, "the provider was never called"
        sent = seen[-1]["messages"]
        assistant = next(m for m in sent if m["role"] == "assistant")
        assert assistant["reasoning_content"] == "I check Pune", (
            "the Router forwarded the framework's spelling unchanged, which "
            "is the 400 this ticket exists to fix")

        # Inbound: the caller got the framework's spelling back.
        body = r.json()
        assert body["choices"][0]["message"]["reasoning_details"] == "I worked it out", (
            "the framework cannot see the reasoning, so the NEXT turn will "
            "drop it and fail exactly as before")
