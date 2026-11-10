"""Shared fakes for the CRM assistant agent's two test files.

``test_crm_agent.py`` (registration, identity, the read tools, the verb and
path fences) and ``test_crm_agent_write.py`` (WS-26d-write) need the same three
things, and a second hand-typed copy of any of them is how two files start
disagreeing about what one agent does:

1. **The module, loaded by file path** — the way the Dynamic Agent Loader loads
   it, not as an installed package.
2. **A fake ``httpx.AsyncClient``, patched at the CLIENT** rather than at
   ``_get``/``_post``. The properties under test are the verb, the path, the
   headers and the body, and every one of those is produced by
   ``_request``/``_headers`` — which a helper-level stub would skip straight
   past, leaving the fences untested while the tests stayed green. It records
   the JSON body too: for a write tool, *what was sent* is half the assertion.
3. **The confirmation gate, stubbed both ways.** The tools import
   ``request_confirmation`` inside the call (copying the agent-email-assistant
   shape), so patching the attribute on ``acb_skills.ask_tools`` is what they
   actually see — there is no import-time binding to work around.

Deliberately free of ``pytest.importorskip``: this module is imported, not
collected, and a skip raised at import time takes the importing file with it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from tests.unit._card_words import assert_card_words

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "apps" / "agents" / "agent-crm"


def load_agent_module(name: str = "crm_assistant_agent") -> ModuleType:
    """The agent module, executed from its file — the loader's own path."""
    spec = importlib.util.spec_from_file_location(name, AGENT_DIR / "agents.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def agent_source() -> str:
    """The module's text, for the structural fences that read it as source."""
    return (AGENT_DIR / "agents.py").read_text(encoding="utf-8")


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ""
        self.content = b"{}"

    def json(self) -> Any:
        return self._payload


class FakeClient:
    """Stands in for ``httpx.AsyncClient`` inside the agent module."""

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
            # The write half's half of the assertion: a tool that confirms and
            # then posts the wrong body is not a working tool.
            "json": kwargs.get("json"),
        }
        self._calls.append(call)
        payload = (
            self._responder(call) if callable(self._responder) else self._responder
        )
        return FakeResponse(payload)


def fake_gateway(
    module: ModuleType,
    monkeypatch: Any,
    responder: Any,
    *,
    user: str | None = "sales@fracktal.in",
    client_class: type[FakeClient] = FakeClient,
) -> list[dict]:
    """Patch the module's httpx + acting user. Returns the recorded call list."""
    calls: list[dict] = []
    monkeypatch.setattr(
        module, "httpx",
        SimpleNamespace(AsyncClient=lambda **_kw: client_class(calls, responder)),
    )
    if user is not None:
        monkeypatch.setattr(module, "_current_user_email", lambda: user)
    return calls


def writes(calls: list[dict]) -> list[dict]:
    """Every call that was not a GET — i.e. everything that changed the CRM.

    The invariant the write tools are held to is *no mutation before consent*,
    not *no traffic before consent*: resolving a stage name and naming the deal
    on the confirmation card are reads, and a card that cannot say which deal
    is moving where buys a signature for an act nobody was shown.
    """
    return [c for c in calls if str(c.get("method", "")).upper() != "GET"]


# ── The confirmation gate, stubbed both ways ────────────────────────────────

def _set_confirmation(monkeypatch: Any, answer: bool) -> list[dict]:
    """Replace ``request_confirmation`` with a recorder returning ``answer``."""
    import acb_skills.ask_tools as ask_tools

    asked: list[dict] = []

    async def _stub(**kwargs: Any) -> bool:
        # Every card a test draws is checked for the member's words.
        assert_card_words(kwargs)
        asked.append(dict(kwargs))
        return answer

    monkeypatch.setattr(ask_tools, "request_confirmation", _stub)
    return asked


def approve(monkeypatch: Any) -> list[dict]:
    """The user approves. Returns the list of cards they were shown."""
    return _set_confirmation(monkeypatch, True)


def deny(monkeypatch: Any) -> list[dict]:
    """The user rejects. Returns the list of cards they were shown."""
    return _set_confirmation(monkeypatch, False)
