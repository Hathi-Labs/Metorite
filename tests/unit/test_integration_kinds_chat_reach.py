"""Chat reach of each kind on the Integrations screen (task 3561).

R7 fence, second half of ``test_connector_chat_reach.py`` (task 3440). That
test covers each id of ``connector_list.json``. This test covers the kinds
that the list does not name:

- each view of the screen (``INTEGRATION_VIEWS`` in ``lib/views.ts``);
- each connection kind (``ConnectionKind`` in ``lib/connections.ts``).

A new view or kind with no decision here fails the test. A decision for a
removed view or kind fails too. A ``gap`` names the task that closes it. A
``by_design`` entry says in words why no chat path exists.

The test also pins the claims that the reach audit left unproven: the
WhatsApp specialist and the Drive knowledge source.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CP = ROOT / "workbench/control_plane/src/app/integrations/lib"
GATEWAY = ROOT / "apps/services/gateway/gateway"

PATHS = {"covered_below", "pack", "retrieval", "mcp", "tool", "by_design", "gap"}

#: view id -> (path, evidence or reason).
VIEWS: dict[str, tuple[str, str]] = {
    "connections": ("covered_below", "each kind of a connection has a row of KINDS"),
    "keys": ("gap", "task 3562: a stored key feeds scripts only, no chat tool"),
    "plugins": ("gap", "task 3562: no reader of a plugin in the orchestrator"),
    "skills": ("tool", "the built-in skills are the SKILL_FAMILIES of acb_skills"),
    "agents": (
        "by_design",
        "outside agents act as the member through the platform MCP, not through the chat",
    ),
}

#: connection kind -> (path, evidence or reason).
KINDS: dict[str, tuple[str, str]] = {
    "oauth": ("covered_below", "each provider is a row of test_connector_chat_reach.REACH"),
    "mailbox": ("pack", "email,email-setup"),
    "whatsapp": ("retrieval", "search_company source whatsapp"),
    "mcp": ("mcp", "_inject_mcp_servers in orchestrator/_tool_injection.py"),
    "api_key": ("gap", "task 3562: a stored key feeds scripts only, no chat tool"),
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _view_ids() -> set[str]:
    src = _read(CP / "views.ts")
    block = src.split("export const INTEGRATION_VIEWS", 1)[1].split("];", 1)[0]
    return set(re.findall(r'\{\s*id:\s*"([a-z_]+)"', block))


def _kinds() -> set[str]:
    src = _read(CP / "connections.ts")
    line = re.search(r"export type ConnectionKind\s*=\s*([^;]+);", src)
    assert line, "ConnectionKind moved: update this test"
    return set(re.findall(r'"([a-z_]+)"', line.group(1)))


def _check(table: dict[str, tuple[str, str]], name: str) -> None:
    for key, (path, why) in table.items():
        assert path in PATHS, f"{name} {key}"
        assert why.strip(), f"{name} {key}"
        if path == "gap":
            assert re.search(r"task \d{3,5}", why), f"{name} {key}: a gap names its task"


def test_the_source_of_the_ids_is_not_empty() -> None:
    assert len(_view_ids()) >= 5
    assert len(_kinds()) >= 5


def test_each_view_has_a_chat_reach_decision() -> None:
    assert _view_ids() - set(VIEWS) == set(), "Add a VIEWS entry for the new view"
    assert set(VIEWS) - _view_ids() == set(), "A VIEWS entry names a removed view"


def test_each_connection_kind_has_a_chat_reach_decision() -> None:
    assert _kinds() - set(KINDS) == set(), "Add a KINDS entry for the new kind"
    assert set(KINDS) - _kinds() == set(), "A KINDS entry names a removed kind"


def test_each_decision_is_valid() -> None:
    _check(VIEWS, "view")
    _check(KINDS, "kind")


def test_a_pack_decision_names_packs_that_exist() -> None:
    from acb_skills import tool_packs

    for key, (path, why) in KINDS.items():
        if path != "pack":
            continue
        for slug in why.split(","):
            assert slug in tool_packs.PACKS, f"{key}: no pack {slug}"


def test_the_whatsapp_specialist_and_source_exist() -> None:
    sources = _read(GATEWAY / "retrieval/sources.py")
    assert 'Source("whatsapp"' in sources
    agent = _read(GATEWAY / "routes/agent.py")
    assert '"name": "whatsapp-assistant"' in agent
    assert (ROOT / "apps/agents/agent-whatsapp-assistant").is_dir()


def test_the_drive_knowledge_source_exists() -> None:
    from gateway.kb_sources.registry import SOURCES

    assert "google_drive" in SOURCES
    sources = _read(GATEWAY / "retrieval/sources.py")
    assert 'Source("knowledge"' in sources


def test_the_mcp_path_exists() -> None:
    src = _read(ROOT / "apps/services/orchestrator/orchestrator/_tool_injection.py")
    assert "async def _inject_mcp_servers" in src
