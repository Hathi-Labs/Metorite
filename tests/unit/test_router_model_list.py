"""WS-43o: the model list reads the Router, and gateway start never calls the Copilot SDK."""

from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]


def _gw():
    from gateway import main as gw

    return gw


def test_copilot_models_route_answers_the_router_slate() -> None:
    from acb_llm.routed import PICKER_WIRE_IDS

    data = asyncio.run(_gw().copilot_models())
    assert data["source"] == "router"
    assert {m["id"] for m in data["models"]} == set(PICKER_WIRE_IDS)


def test_copilot_models_route_never_loads_the_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    # A None entry makes `import copilot` raise ImportError.
    monkeypatch.setitem(sys.modules, "copilot", None)
    asyncio.run(_gw().copilot_models())


def test_runtime_checks_hold_no_sdk_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "copilot", None)
    checks = _gw()._runtime_checks()
    assert "copilot_sdk" not in checks
    assert "router" in checks


def test_gateway_start_does_not_call_the_sdk() -> None:
    gw = _gw()
    assert not hasattr(gw, "_list_copilot_models")
    assert not hasattr(gw, "_warmup_copilot_models")
    src = inspect.getsource(gw)
    assert "_list_copilot_models" not in src
    assert "from copilot" not in src
    assert "import copilot" not in src


def test_models_all_route_has_no_copilot_group() -> None:
    text = (_REPO / "workbench/control_plane/src/app/api/models/all/route.ts").read_text(
        encoding="utf-8"
    )
    assert '"GitHub Copilot SDK"' not in text
    assert "/copilot/models" not in text
    # "copilot" is the name of the agent-executor mode (the MAF tool path). The ONE
    # item on it is "Auto", the way back to the tool path after a tier pick.
    assert text.count('runtime: "copilot"') == 1
    assert '{ id: "auto", label: "Auto (the assistant picks)", runtime: "copilot"' in text


def test_no_control_plane_surface_shows_the_copilot_sdk_group() -> None:
    # Done-when 4 of WS-43o: the loading-state list of the chat holds no SDK group either.
    for rel in ("workbench/control_plane/src/components/AgentChat.tsx",
                "workbench/control_plane/src/app/api/models/all/route.ts"):
        text = (_REPO / rel).read_text(encoding="utf-8")
        assert '"GitHub Copilot SDK"' not in text, rel
        assert "SDK picks" not in text, rel


def test_the_router_check_fails_when_serving_is_on_and_nothing_is_wired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import acb_llm.routed as routed

    gw = _gw()
    monkeypatch.setattr(routed, "router_wired", lambda: False)

    class _S:
        router_serving_enabled = True

    monkeypatch.setattr(gw, "get_settings", lambda: _S())
    assert gw._runtime_checks()["router"]["ok"] is False
    _S.router_serving_enabled = False
    assert gw._runtime_checks()["router"]["ok"] is True
