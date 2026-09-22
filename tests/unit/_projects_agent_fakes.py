"""Shared fakes for the Projects assistant's test files.

The same three things ``_crm_agent_fakes.py`` provides, for the same reason:
a second hand-typed copy of the fake client is how two files start
disagreeing about what one agent does. The client is patched at the CLIENT
(``skill_projects.client.httpx``) rather than at ``get``/``post``, so the real
``headers()`` and the real manifest check run under test.

Deliberately free of ``pytest.importorskip``: this module is imported, not
collected, and a skip raised at import time takes the importing file with it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "apps" / "agents" / "agent-projects"
SKILL_DIR = REPO_ROOT / "apps" / "skills" / "skill-projects" / "skill_projects"


def load_agent_module(name: str = "projects_assistant_agent") -> ModuleType:
    """The agent module, executed from its file — the loader's own path."""
    spec = importlib.util.spec_from_file_location(name, AGENT_DIR / "agents.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ""
        self.content = b"{}"

    def json(self) -> Any:
        return self._payload


class FakeClient:
    """Stands in for ``httpx.AsyncClient`` inside ``skill_projects.client``."""

    def __init__(self, calls: list[dict], responder: Any) -> None:
        self._calls = calls
        self._responder = responder

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        path = url.split("/", 3)[-1]
        call = {
            "method": method,
            "url": url,
            "path": "/" + path if not path.startswith("/") else path,
            "headers": kwargs.get("headers") or {},
            "params": kwargs.get("params") or {},
            "json": kwargs.get("json"),
        }
        self._calls.append(call)
        payload = self._responder(call) if callable(self._responder) else self._responder
        return FakeResponse(payload)


def fake_gateway(
    monkeypatch: Any,
    responder: Any,
    *,
    user: str | None = "pm@fracktal.in",
) -> list[dict]:
    """Patch the client's httpx and acting user. Returns the recorded calls."""
    import skill_projects.client as client

    calls: list[dict] = []
    monkeypatch.setattr(
        client,
        "httpx",
        SimpleNamespace(AsyncClient=lambda **_kw: FakeClient(calls, responder)),
    )
    if user is not None:
        monkeypatch.setattr(client, "current_user_email", lambda: user)
    else:
        monkeypatch.setattr(client, "current_user_email", lambda: "")
    return calls


def writes(calls: list[dict]) -> list[dict]:
    """Every call that was not a GET."""
    return [c for c in calls if str(c.get("method", "")).upper() != "GET"]


def empty_list(_call: dict) -> dict:
    """A responder that answers every read with an empty collection."""
    return {"rows": [], "total": 0, "reports": [], "people": [], "agents": []}
