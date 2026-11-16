"""The result card after a connect: the model (WS-54 IN-9, task 3172).

Spec: ``project-docs/specs/integrations_console.md`` §3.7 rule 1 and the row
IN-9. After any connect, one card says three things, in this order:

1. **What works now.** The label and the ``unlocks`` sentence of the provider,
   the apps that use the connection, and one sentence of what Mira can do.
2. **The templates** for the provider (at most :data:`MAX_TEMPLATES`).
3. **Build an app on it.** ``build_offer`` is true only where no built-in app
   shows the data of the provider: the row says ``built_in_app_shows_data`` is
   false. An MCP server (``mcp:<name>``) has no row. It gets the offer when no
   app uses it. A mailbox, a calendar or a CRM never gets one.

This module is PURE. It reads the connector list (IN-11), holds no secret,
opens no connection and never starts a build: the offer is data, and the
screen shows a link, never a modal. The caller passes the apps and the
templates, because their source (IN-10, the template catalog) is another
slice. The screen (IN-9b) and the chat line read the same card.
Fence: ``tests/unit/test_connector_result_card.py``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from acb_common import connector_list

MAX_TEMPLATES = 3
MAX_APPS = 5
MAX_NAME = 80
_MCP_RE = re.compile(r"mcp:[a-z0-9][a-z0-9_.-]{0,62}", re.ASCII)
_MIRA_LINE = "Mira can now use {label} when you ask."
_MIRA_LINE_MCP = "Mira can now call the tools of this server when you ask."


def _clean(names: Sequence[str], cap: int) -> list[str]:
    """Text names only, trimmed, each once, at most *cap*, each at most
    :data:`MAX_NAME` characters. Anything else is dropped, never echoed."""
    out: list[str] = []
    if isinstance(names, str) or names is None:
        return out
    for n in names:
        clean = n.strip()[:MAX_NAME] if isinstance(n, str) else ""
        if clean and clean not in out:
            out.append(clean)
        if len(out) == cap:
            break
    return out


def result_card(
    provider_id: str, *, apps: Sequence[str] = (), templates: Sequence[str] = ()
) -> dict[str, Any] | None:
    """The card of *provider_id*, or ``None`` for an id that is neither a row
    of the connector list nor an MCP server id (``mcp:<name>``).

    *apps* are the names of the apps and features that use the connection.
    *templates* are the names of the templates for the provider.
    """
    row = connector_list.connector(provider_id) if isinstance(provider_id, str) else None
    used = _clean(apps, MAX_APPS)
    if row is not None:
        label, unlocks = str(row["label"]), str(row["unlocks"])
        owner = str(row["owning_app"])
        offer = not bool(row["built_in_app_shows_data"])
        mira = _MIRA_LINE.format(label=label)
    elif isinstance(provider_id, str) and _MCP_RE.fullmatch(provider_id):
        label, unlocks, owner = provider_id.removeprefix("mcp:"), "", ""
        offer = not used
        mira = _MIRA_LINE_MCP
    else:
        return None
    return {
        "provider": provider_id,
        "label": label,
        "works_now": {"unlocks": unlocks, "owning_app": owner, "apps": used, "mira": mira},
        "templates": _clean(templates, MAX_TEMPLATES),
        "build_offer": offer,
    }


def chat_line(card: dict[str, Any]) -> str:
    """What Mira says after a connect: the same three parts, in plain
    sentences, with ONE next step that fits (CE-10d). A template wins over a
    build, because a template is the smaller step."""
    parts = [f"{card['label']} is connected."]
    works = card["works_now"]
    if works["unlocks"]:
        parts.append(works["unlocks"])
    if works["apps"]:
        parts.append("These use it now: " + ", ".join(works["apps"]) + ".")
    parts.append(works["mira"])
    if card["templates"]:
        parts.append(f"Next step: start from the template {card['templates'][0]}.")
    elif card["build_offer"]:
        parts.append(f"Next step: build an app on {card['label']} in Studio.")
    return " ".join(parts)
