"""WS-54 fix round 1, item 8 — a GitHub token from the chat's SETUP token.

Spec: ``project-docs/specs/github_integration.md`` §7.6. No database: the rule
is pure, and the two requests go to a fake client.

* ``GITHUB_TOKEN`` goes to ``PUT /integrations/github/token`` (the one writer),
  and every other key to ``POST /integrations/configure``, in its own request.
* A refusal adds one line to the answer. The line never holds the token.
* A live App is never replaced from the chat: no ``replace_app`` is sent, and
  409 ``app_connected`` gets its own line.
* Both callers (the executor and the chat BFF) use the shared helper.

R7, the mutations. Each one turns a test here red (run 2026-10-18):

* 8-M1 ``split_setup_vars`` keeps ``GITHUB_TOKEN`` with the other keys ->
  ``test_the_token_goes_to_its_one_writer_and_the_rest_to_configure``.
* 8-M2 ``github_setup_note`` answers ``""`` for a refusal ->
  ``test_a_refusal_adds_a_line_that_never_holds_the_token``.
* 8-M3 the executor posts every key to configure again ->
  ``test_both_callers_use_the_shared_rule``.
* 8-M4 the chat sends ``replace_app`` -> ``test_the_chat_never_replaces_the_app``.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from orchestrator import setup_tokens

ROOT = Path(__file__).resolve().parents[2]
#: A fake token, built at run time so no token-shaped literal sits in the
#: source for the secret scan to flag (WS-54 fix round 6).
TOKEN = "_".join(["ghp", "CHAT", "SENTINEL", "x" * 16])
PATTERN = re.compile(r"<<<SETUP:([^:]+):([A-Z0-9_]+)=([^>]+)>>>")


class _Resp:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        return self._body


class FakeClient:
    def __init__(self, token_status: int = 200, token_body: Any = None) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.token_status = token_status
        self.token_body = token_body or {"status": "connected", "login": "octo", "scopes": []}

    async def post(self, url: str, json: Any = None, headers: Any = None) -> _Resp:
        self.calls.append(("POST", url, json))
        return _Resp(200, {"written": [v["key"] for v in json["vars"]]})

    async def put(self, url: str, json: Any = None, headers: Any = None) -> _Resp:
        self.calls.append(("PUT", url, json))
        return _Resp(self.token_status, self.token_body)


def _matches(text: str) -> list[tuple[str, str, str]]:
    return PATTERN.findall(text)


def _save(client: FakeClient, text: str) -> str:
    return asyncio.run(setup_tokens.save_setup_vars(
        client, "http://gw", {"Authorization": "Bearer x"}, _matches(text)))


def test_the_token_goes_to_its_one_writer_and_the_rest_to_configure() -> None:
    client = FakeClient()
    note = _save(client, f"Done. <<<SETUP:github:GITHUB_TOKEN={TOKEN}>>> "
                         "<<<SETUP:serpapi:SERPAPI_API_KEY=abc123>>>")
    assert note == ""
    assert client.calls == [
        ("POST", "http://gw/integrations/configure",
         {"vars": [{"key": "SERPAPI_API_KEY", "value": "abc123"}]}),
        ("PUT", "http://gw/integrations/github/token", {"token": TOKEN}),
    ]


def test_a_refusal_adds_a_line_that_never_holds_the_token() -> None:
    client = FakeClient(422, {"detail": "GitHub refused this token."})
    note = _save(client, f"<<<SETUP:github:GITHUB_TOKEN={TOKEN}>>>")
    assert note == ("Metorite did not save the GitHub token: GitHub refused this token. "
                    "Paste it in Integrations, under Advanced.")
    assert TOKEN not in note
    # A detail that echoes the token is never repeated.
    echo = setup_tokens.github_setup_note(422, f"bad {TOKEN}", TOKEN)
    assert TOKEN not in echo


def test_a_github_refusal_keeps_the_other_keys() -> None:
    client = FakeClient(422, {"detail": "That is not a GitHub token."})
    _save(client, "<<<SETUP:github:GITHUB_TOKEN=bad>>> <<<SETUP:apollo:APOLLO_API_KEY=k1>>>")
    assert ("POST", "http://gw/integrations/configure",
            {"vars": [{"key": "APOLLO_API_KEY", "value": "k1"}]}) in client.calls


def test_the_chat_never_replaces_the_app() -> None:
    client = FakeClient(409, {"detail": "app_connected"})
    note = _save(client, f"<<<SETUP:github:GITHUB_TOKEN={TOKEN}>>>")
    assert note == setup_tokens.APP_CONNECTED_NOTE
    (put,) = [c for c in client.calls if c[0] == "PUT"]
    assert put[2] == {"token": TOKEN}, "no replace_app from model output"


def test_an_unreachable_server_says_so() -> None:
    class Down(FakeClient):
        async def put(self, url: str, json: Any = None, headers: Any = None) -> _Resp:
            raise OSError("down")

    assert _save(Down(), f"<<<SETUP:github:GITHUB_TOKEN={TOKEN}>>>") == setup_tokens.UNREACHABLE_NOTE


def test_empty_values_drop_out_and_no_key_sends_nothing() -> None:
    client = FakeClient()
    assert _save(client, "<<<SETUP:github:GITHUB_TOKEN=   >>>") == ""
    assert client.calls == []


def test_both_callers_use_the_shared_rule() -> None:
    executor = (ROOT / "apps/services/orchestrator/orchestrator/executor.py").read_text(
        encoding="utf-8")
    assert "from orchestrator.setup_tokens import save_setup_vars" in executor
    assert executor.count("/integrations/configure") == 0, "configure is called in setup_tokens only"
    route = (ROOT / "workbench/control_plane/src/app/api/agent/chat/route.ts").read_text(
        encoding="utf-8")
    assert "splitSetupVars(rawMatches)" in route and "githubSetupNote(" in route
    assert "/integrations/github/token" in route and "replace_app" not in route
    source = Path(setup_tokens.__file__).read_text(encoding="utf-8")
    assert "replace_app\":" not in source and "\"replace_app\"" not in source
