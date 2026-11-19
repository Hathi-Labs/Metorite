"""Chat reach of each connector (task 3440, owner ask 2026-11-16).

R7 fence: a member can add each row of ``connector_list.json``. Each row
needs a decision here: the path that makes it usable in a chat turn, or a
written reason with the task that closes the gap. A new connector with no
decision fails this test. A decision for a removed connector also fails.

The reach matrix is on task 3440. ``PATH`` values:

- ``mcp``: attached by ``_inject_mcp_servers`` (not a pack).
- ``pack``: a tool pack in ``tool_packs.PACKS`` holds its tools.
- ``retrieval``: ``search_company`` or ``read_page`` reads its data.
- ``specialist``: a specialist agent holds its tools.
- ``tool``: a platform tool is injected for the orchestrator (``_tool_injection.py``).
- ``gap``: no chat path. The entry names the task that closes it.
"""

from __future__ import annotations

import re

from acb_common import connector_list as cl
from acb_skills import tool_packs

PATHS = {"mcp", "pack", "retrieval", "specialist", "tool", "gap"}

#: id -> (path, evidence or reason). A ``pack`` entry names packs that must exist.
REACH: dict[str, tuple[str, str]] = {
    "mailbox": ("pack", "email,email-setup"),
    "whatsapp": ("retrieval", "search_company source whatsapp, and the whatsapp-assistant specialist"),
    "zoho": ("pack", "crm-records"),
    "github": ("tool", "github_search and github_repo_search, injected at _tool_injection.py"),
    # CE-15 (task 3463): my_tasks_list_events reads the synced events.
    "google_calendar": ("pack", "calendar-day"),
    "outlook_calendar": ("pack", "calendar-day"),
    "google_drive": ("retrieval", "search_company source knowledge, read_page"),
    "apollo": ("gap", "task 3440: a key feeds scripts only, no chat tool"),
    "google_maps": ("gap", "task 3440: a key feeds scripts only, no chat tool"),
    "instantly": ("gap", "task 3440: a key feeds scripts only, no chat tool"),
    "serpapi": ("tool", "web_search may use SerpAPI, injected at _tool_injection.py"),
    "apify": ("gap", "task 3440: a key feeds scripts only, no chat tool"),
    "anymailfinder": ("gap", "task 3440: a key feeds scripts only, no chat tool"),
    "google_sheets": ("gap", "task 3440: a key feeds scripts only, no chat tool"),
    "smtp": ("gap", "task 3440: a key feeds scripts only, no chat tool"),
    "mcp": ("mcp", "_inject_mcp_servers"),
    # API-5 lists the connector. The shared chat tools of api_connections.md §2.4 close the gap.
    "api:zoho_books": ("gap", "task 3790 (API-4): the api_read chat tools are not built yet"),
}


def _ids() -> set[str]:
    return {r["id"] for r in cl.connector_rows()}


def test_each_connector_has_a_chat_reach_decision() -> None:
    assert _ids() - set(REACH) == set(), "Add a REACH entry for the new connector"


def test_no_decision_for_a_removed_connector() -> None:
    assert set(REACH) - _ids() == set()


def test_each_decision_is_valid() -> None:
    for cid, (path, why) in REACH.items():
        assert path in PATHS, cid
        assert why.strip(), cid
        if path == "gap":
            assert re.search(r"task \d{3,5}", why), f"{cid}: a gap names its task"


def test_a_pack_decision_names_packs_that_exist() -> None:
    for cid, (path, why) in REACH.items():
        if path != "pack":
            continue
        for slug in why.split(","):
            assert slug in tool_packs.PACKS, f"{cid}: no pack {slug}"
