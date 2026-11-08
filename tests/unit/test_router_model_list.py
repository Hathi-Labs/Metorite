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
    assert 'runtime: "copilot"' not in text and 'runtime: "copilot" as' not in text
