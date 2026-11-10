"""Unit tests for the WhatsApp companion MAF agent (W5).

The agent module is loaded by file path (as the Dynamic Agent Loader does), not
as an installed package. No gateway / MAF runtime: the module-level `_get`/`_post`
seams are monkeypatched so the tool surface, formatting, and the drafts-only
doctrine are pinned without a live stack.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
from pathlib import Path

_AGENT_DIR = (
    Path(__file__).resolve().parents[2]
    / "apps" / "agents" / "agent-whatsapp-assistant"
)


def _load_agent():
    spec = importlib.util.spec_from_file_location(
        "wa_assistant_agent", _AGENT_DIR / "agents.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_M = _load_agent()


# ── tool surface ──────────────────────────────────────────────────────────────

def test_all_tools_are_async_and_documented() -> None:
    assert _M._TOOLS, "the agent must expose tools"
    for fn in _M._TOOLS:
        assert inspect.iscoroutinefunction(fn), f"{fn.__name__} must be async"
        assert (fn.__doc__ or "").strip(), f"{fn.__name__} needs a docstring"


def test_register_agent_tools_maps_names() -> None:
    reg = _M._register_agent_tools()
    assert reg["whatsapp_brief"] is _M.whatsapp_brief
    assert "draft_whatsapp_reply" in reg
    assert "draft_waiting_on_nudge" in reg


def test_no_send_tool_by_design() -> None:
    # The companion drafts; the founder sends. No tool name may imply a send.
    names = {fn.__name__ for fn in _M._TOOLS}
    assert not any("send" in n or "broadcast" in n for n in names)


def test_config_scope_matches_tools() -> None:
    # own_tool_scope in config.json must not drift from the registered tools.
    # WS-48 N4: a flag-gated tool (narrow_and_read) is in the scope and is not
    # in _TOOLS. test_own_tool_scope_parity.py builds it with the flag on.
    from tests.unit.test_own_tool_scope_parity import FLAG_GATED

    config = json.loads((_AGENT_DIR / "config.json").read_text(encoding="utf-8"))
    scope = set(config["own_tool_scope"])
    assert scope - set(FLAG_GATED) == {fn.__name__ for fn in _M._TOOLS}
    assert "narrow_and_read" in scope
    assert config["runtime"] == "maf"


# ── formatting / behavior (mocked gateway) ────────────────────────────────────

def _patch(monkeypatch, *, get=None, post=None):
    async def _fake_get(path, params=None):
        return get(path, params) if callable(get) else get

    async def _fake_post(path, body=None):
        return post(path, body) if callable(post) else post

    monkeypatch.setattr(_M, "_get", _fake_get)
    monkeypatch.setattr(_M, "_post", _fake_post)


async def test_brief_formats_counts_and_waiting(monkeypatch) -> None:
    digest = {
        "counts": {"needs_reply": 3, "waiting": 2, "groups": 5, "muted": 12},
        "needs_you": [
            {"name": "Ravi", "snippet": "kitna margin?", "intent": "pricing",
             "chat_id": "c1"},
        ],
        "commitment_watch": [
            {"text": "send the quote", "due_hint": "Friday", "has_task": False},
        ],
        "waiting_on": [
            {"id": "k1", "chat_id": "c2", "text": "share the AWB",
             "due_hint": "tomorrow"},
        ],
        "waiting_on_count": 1,
    }
    _patch(monkeypatch, get=digest)
    out = await _M.whatsapp_brief()
    assert "3 need reply" in out
    assert "Ravi" in out and "chat_id=c1" in out
    assert "not yet a task" in out
    assert "commitment_id=k1" in out


async def test_waiting_on_lists_commitment_ids(monkeypatch) -> None:
    def _get(path, params):
        if path == "/whatsapp/accounts":
            return [{"id": "acc-1"}]
        return [
            {"id": "k1", "chat_id": "c1", "text": "share the AWB",
             "due_hint": "tomorrow"},
        ]

    _patch(monkeypatch, get=_get)
    out = await _M.whatsapp_waiting_on()
    assert "commitment_id=k1" in out
    assert "share the AWB" in out


async def test_draft_reply_is_marked_draft_only(monkeypatch) -> None:
    _patch(monkeypatch, post={"chat_id": "c1", "draft_text": "Ji, bhej deta hoon 🙏",
                              "language": "hi"})
    out = await _M.draft_whatsapp_reply("c1")
    assert "bhej deta hoon" in out
    assert "Draft only" in out              # never claims to have sent


async def test_nudge_is_marked_draft_only(monkeypatch) -> None:
    _patch(monkeypatch, post={"commitment_id": "k1", "chat_id": "c1",
                              "nudge_text": "Following up on the AWB 🙏",
                              "language": "en"})
    out = await _M.draft_waiting_on_nudge("k1")
    assert "Following up" in out
    assert "Draft only" in out


async def test_read_chat_renders_voice_transcript(monkeypatch) -> None:
    msgs = [
        {"direction": "in", "sender_name": "Ravi", "kind": "voice",
         "body_text": "", "transcript_text": "kal AWB bhej dunga"},
    ]
    _patch(monkeypatch, get=msgs)
    out = await _M.read_whatsapp_chat("c1")
    assert "kal AWB bhej dunga" in out
    assert "🎙" in out
