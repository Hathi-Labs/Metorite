"""The ``<<<SETUP:service:KEY=value>>>`` tokens of a model answer (WS-54).

Spec: ``project-docs/specs/github_integration.md`` §7.6. Fix round 1, item 8.

A chat agent that helps a member set up an integration writes a token such as
``<<<SETUP:serpapi:SERPAPI_API_KEY=...>>>``. The executor strips it from the
answer and saves the value. Before GH-7 every key went to ``POST
/integrations/configure``. Since GH-7 that route refuses ``GITHUB_TOKEN`` with
422 ("Connect GitHub in Integrations."), so a GitHub token from the chat was
dropped, the other keys of the batch went with it, and the agent still said
that it saved the key.

The rule now:

* ``GITHUB_TOKEN`` goes to ``PUT /integrations/github/token``, the ONE writer
  of a pasted token. That route keeps its own gates: ``integrations:manage``,
  layer A of the env guard, and the ``GET /user`` check.
* ⚠️ **It never replaces a live App.** The chat sends no ``replace_app``, so an
  org on the Metorite App gets 409 ``app_connected``. Model output is untrusted
  text (a prompt injection can write a token), and the card asks the admin
  first (item 3).
* Every other key goes to ``POST /integrations/configure``, as before, in its
  own request. So a GitHub refusal never drops the other keys.
* A refusal adds one plain line to the answer, so the agent cannot claim a
  save that did not happen. The line never holds the token.

The chat BFF (``workbench/control_plane/src/lib/setupTokens.ts``) holds the
same rule. Fence: ``tests/unit/test_github_chat_setup.py``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable
from typing import Any

GITHUB_TOKEN_KEY = "GITHUB_TOKEN"

#: What the answer says when the org is on the App, and the chat may not
#: replace it.
APP_CONNECTED_NOTE = (
    "Metorite did not save the GitHub token, because the Metorite GitHub App is "
    "connected and a token replaces it. To use a token, paste it in Integrations, "
    "under Advanced."
)

#: What the answer says when the server did not answer.
UNREACHABLE_NOTE = (
    "Metorite could not reach its server to save the GitHub token. Paste it in "
    "Integrations, under Advanced."
)

#: The longest refusal detail that the note repeats. The gateway's words are short.
MAX_DETAIL = 120


def split_setup_vars(
    matches: Iterable[tuple[str, str, str]],
) -> tuple[str | None, list[dict[str, str]]]:
    """``(github_token, other_vars)`` from the ``(service, key, value)`` matches.

    A value is trimmed, and an empty value is dropped. The last
    ``GITHUB_TOKEN`` wins.
    """
    token: str | None = None
    rest: list[dict[str, str]] = []
    for _service, key, value in matches:
        clean = value.strip()
        if not clean:
            continue
        if key.strip().upper() == GITHUB_TOKEN_KEY:
            token = clean
            continue
        rest.append({"key": key, "value": clean})
    return token, rest


def github_setup_note(status: int, detail: Any, token: str) -> str:
    """The line to add to the answer for the token save, or ``""`` for a save."""
    if status == 200:
        return ""
    if status == 409 and detail == "app_connected":
        return APP_CONNECTED_NOTE
    words = "GitHub refused this token."
    if isinstance(detail, str) and detail and len(detail) <= MAX_DETAIL and token not in detail:
        words = detail if detail.endswith(".") else f"{detail}."
    return f"Metorite did not save the GitHub token: {words} Paste it in Integrations, under Advanced."


async def save_setup_vars(
    client: Any, base_url: str, headers: dict[str, str],
    matches: Iterable[tuple[str, str, str]],
) -> str:
    """Save the keys of one answer. Returns the note for the answer, or ``""``.

    *client* is an ``httpx.AsyncClient``. Never raises, and never logs a value.
    """
    token, rest = split_setup_vars(matches)
    if rest:
        # The configure keeps its own refusals (layers A, B and C).
        with contextlib.suppress(Exception):
            await client.post(f"{base_url}/integrations/configure", json={"vars": rest},
                              headers=headers)
    if not token:
        return ""
    try:
        resp = await client.put(f"{base_url}/integrations/github/token",
                                json={"token": token}, headers=headers)
    except Exception:
        return UNREACHABLE_NOTE
    try:
        body = resp.json()
    except Exception:
        body = None
    detail = body.get("detail") if isinstance(body, dict) else None
    return github_setup_note(resp.status_code, detail, token)


__all__ = [
    "APP_CONNECTED_NOTE",
    "GITHUB_TOKEN_KEY",
    "UNREACHABLE_NOTE",
    "github_setup_note",
    "save_setup_vars",
    "split_setup_vars",
]
