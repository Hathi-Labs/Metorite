"""The default model of ``/compact`` and the suggestions route (task 1128 follow-up).

Both routes defaulted to the bare id ``deepseek/deepseek-chat``. With the
Router on, the gateway sends the name to the Console, and the Console refuses a
bare model id with a 400 (D32.7). Compaction then failed on every call, and the
member saw nothing. The default is now one constant, ``HELPER_MODEL_DEFAULT``
in ``workbench/control_plane/src/lib/helperModel.ts``: a tier name.

These tests prove the default on BOTH paths:

* Router on: ``_router_outbound`` names the Router's own tier, unchanged.
* Router off: ``_resolve_model`` maps the tier to the live tier model.

Mutations this file catches (R7):

* The constant becomes a bare model id again: three tests fail.
* A route stops reading the constant: one test fails.
* ``AgentChat`` stops showing the failure line: one test fails.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CP = ROOT / "workbench" / "control_plane" / "src"


def _read(rel: str) -> str:
    return (CP / rel).read_text(encoding="utf-8")


def _default() -> str:
    m = re.search(r'HELPER_MODEL_DEFAULT\s*=\s*"([^"]+)"', _read("lib/helperModel.ts"))
    assert m, "helperModel.ts must export HELPER_MODEL_DEFAULT"
    return m.group(1)


def test_the_default_is_a_tier_name_the_router_accepts() -> None:
    from acb_llm.client import _TIER_ALIAS_MAP
    from gateway.routes.v1_compat import _router_outbound

    name = _default()
    assert name in _TIER_ALIAS_MAP, f"{name!r} is not a slate tier name"
    outbound = _router_outbound({"model": name, "messages": [{"role": "user", "content": "x"}]})
    assert outbound["model"] == name, "the Router must get the tier name unchanged"


def test_the_default_resolves_to_a_model_off_the_router(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_llm import client
    from gateway.routes.v1_compat import _resolve_model

    monkeypatch.setitem(client._TIER_MODEL, "tier1", "deepseek/deepseek-v4-flash")
    assert _resolve_model(_default()) == "deepseek/deepseek-v4-flash"


def test_a_bare_model_id_is_not_a_tier() -> None:
    """The old default: the Router has no tier of that name, so it answers 400."""
    from acb_llm.client import _TIER_ALIAS_MAP

    assert "deepseek/deepseek-chat" not in _TIER_ALIAS_MAP
    assert not _default().startswith("deepseek/")


@pytest.mark.parametrize("rel", [
    "app/api/chat/suggestions/route.ts",
    "app/api/chat/sessions/[sessionId]/compact/route.ts",
])
def test_each_route_reads_the_one_constant(rel: str) -> None:
    src = _read(rel)
    assert "helperModel()" in src
    assert "deepseek/deepseek-chat" not in re.sub(r"//.*", "", src), (
        "a route names the bare model id again")


def test_a_failed_compaction_shows_one_line_to_the_member() -> None:
    src = _read("components/AgentChat.tsx")
    assert "COMPACT_FAILED_NOTICE" in src
    assert src.count("setComposerNotice(COMPACT_FAILED_NOTICE)") == 2, (
        "the 502 answer and the thrown fetch must each show the line")
