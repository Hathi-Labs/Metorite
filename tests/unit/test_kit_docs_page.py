"""The docs page "Use Metorite from your coding agent" and its llms.txt (KIT-8). R7: advisory fence.

The page points to the kit and the MCP and copies neither. These tests pin the
headings against the file map, the address, the tool names, the link hosts, and
the rule of MCP-Gc: the setup text of the Copy button equals the kit file.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest
from acb_playbook.loader import CONTENT_DIR

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "tests/unit"))
sys.path.insert(0, str(_ROOT / "apps/services/agent_edge"))

from agent_edge.playbook_prompts import PROMPTS  # noqa: E402
from agent_edge.tools import TOOLS  # noqa: E402
from test_kit_packs import PACK_FILES, secret_hits  # noqa: E402
from test_playbook_org_neutral import hits  # noqa: E402

PAGE = _ROOT / "docs" / "use_metorite_from_your_coding_agent.md"
LLMS = _ROOT / "docs" / "llms.txt"
PASTE_IN = CONTENT_DIR / "kit" / "paste-in.md"
SETUP_TS = _ROOT / "workbench/control_plane/src/app/integrations/lib/agentSetupText.ts"
CONNECT = _ROOT / "docs" / "external_agents_connect.md"
SPEC = _ROOT / "project-docs/specs/orchestration_playbook.md"

MCP_ADDRESS = "https://mcp.metorite.com/mcp"
CLIENTS = [
    "Claude Code",
    "OpenAI Codex",
    "GitHub Copilot",
    "Google Antigravity",
    "Gemini CLI",
    "Any other MCP client",
]
SUBSECTIONS = ["Connect", "Get the kit", "Check it works"]
OWN_HOSTS = {"mcp.metorite.com", "app.metorite.com", "github.com"}
# Banned in a product doc: a decision number, a standing rule number, the org name.
BANNED = [re.compile(r"\bD\d{2,3}(\.\d+)?\b"), re.compile(r"\bR[1-9]\b"), re.compile("HathiLabs")]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _vendor_hosts() -> set[str]:
    """The vendor hosts that the file map of spec section 6a cites."""
    spec = _read(SPEC)
    start = spec.index("## 6a. The agent kit")
    end = spec.index("**Three layers.**", start)
    return {urlparse(u).hostname for u in re.findall(r"https?://[^\s`)]+", spec[start:end])}


def _sections(text: str) -> dict[str, str]:
    parts = re.split(r"^## (.+)$", text, flags=re.M)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def _urls(text: str) -> list[str]:
    return [u.rstrip(".,;:") for u in re.findall(r"https?://[^\s`)\]>\"']+", text)]


def _kit_block() -> str:
    blocks = re.findall(r"```text\n([\s\S]*?)\n```", _read(PASTE_IN))
    assert len(blocks) == 1
    return blocks[0] + "\n"


def test_the_page_has_the_six_client_sections_in_file_map_order():
    headings = list(_sections(_read(PAGE)))
    positions = [headings.index(c) for c in CLIENTS]
    assert positions == sorted(positions)


@pytest.mark.parametrize("client", CLIENTS)
def test_each_client_section_has_connect_kit_and_check(client):
    body = _read(PAGE)
    section = re.split(r"^## ", body, flags=re.M)
    mine = next(s for s in section if s.startswith(client + "\n"))
    subs = re.findall(r"^### (.+)$", mine, flags=re.M)
    assert subs == SUBSECTIONS
    assert "Download the agent kit" in mine
    assert "Call my_work" in mine
    assert re.search(r"^1\. ", mine, flags=re.M)


def test_the_status_line_says_no_live_run():
    head = _read(PAGE).split("\n## ", 1)[0]
    assert "No live run on a real client has been done" in head


def test_the_mcp_address_matches_the_packs_and_the_connect_guide():
    assert MCP_ADDRESS in _read(CONNECT)
    assert MCP_ADDRESS in _read(PAGE)
    assert MCP_ADDRESS in _read(LLMS)
    for path in PACK_FILES:
        text = _read(path)
        for url in _urls(text):
            if "metorite" in url and "mcp" in url:
                assert url == MCP_ADDRESS, path.name


def test_every_link_host_is_on_the_allowlist():
    allowed = OWN_HOSTS | _vendor_hosts()
    assert "code.claude.com" in allowed  # the derivation is not vacuous
    for path in (PAGE, LLMS):
        for url in _urls(_read(path)):
            assert urlparse(url).hostname in allowed, (path.name, url)


def test_an_unknown_host_would_fail_the_allowlist():
    allowed = OWN_HOSTS | _vendor_hosts()
    assert urlparse("https://evil.example.com/x").hostname not in allowed


@pytest.mark.parametrize("path", [PAGE, LLMS], ids=lambda p: p.name)
def test_no_secret_and_no_org_term(path):
    text = _read(path)
    assert secret_hits(text) == []
    for pattern in BANNED:
        assert not pattern.search(text), (path.name, pattern.pattern)


@pytest.mark.parametrize("planted", ["Bearer abcdef123456", "mbt_" + "x" * 43, "see D186 for why", "rule R5 binds", "HathiLabs"])
def test_a_planted_secret_or_banned_token_fails_the_scan(planted):
    clean = _read(PAGE)
    assert secret_hits(clean) == []
    assert not any(p.search(clean) for p in BANNED)
    dirty = clean + "\n" + planted
    assert secret_hits(dirty) or any(p.search(dirty) for p in BANNED), planted


def test_the_page_passes_the_playbook_term_scan_except_for_known_product_words():
    # The fixture also lists words such as "premerge". The page needs none of them.
    assert hits(_read(PAGE)) == []
    assert hits(_read(LLMS)) == []


def test_llms_txt_is_short_and_every_path_it_names_exists():
    text = _read(LLMS)
    assert len(text.splitlines()) <= 40
    assert text.startswith("# ")
    paths = re.findall(r"\(((?:docs|packages|project-docs|apps)/[^)\s]+)\)", text)
    assert paths, "llms.txt must link to repository files"
    for rel in paths:
        assert (_ROOT / rel).is_file(), rel


def test_llms_txt_links_the_page_and_the_guides():
    text = _read(LLMS)
    for rel in ("docs/use_metorite_from_your_coding_agent.md", "docs/external_agents_connect.md", "docs/box_connect.md"):
        assert rel in text


def test_the_page_quotes_at_most_the_heading_line_of_the_paste_in_block():
    page = _read(PAGE)
    block_lines = [ln for ln in _kit_block().splitlines() if len(ln) > 20]
    quoted = [ln for ln in block_lines if ln in page]
    assert quoted == [block_lines[0]]
    assert "```text" not in page


def test_the_page_would_fail_if_it_copied_a_second_line_of_the_block():
    page = _read(PAGE) + "\n" + _kit_block().splitlines()[1] + "\n"
    block_lines = [ln for ln in _kit_block().splitlines() if len(ln) > 20]
    assert [ln for ln in block_lines if ln in page] != [block_lines[0]]


def test_the_copy_button_text_equals_the_kit_block():
    ts = _read(SETUP_TS)
    match = re.search(r"AGENT_SETUP_TEXT = `([\s\S]*?)`;", ts)
    assert match
    assert match.group(1) == _kit_block()


def test_a_drifted_copy_button_text_would_fail():
    drifted = _kit_block().replace("one git worktree", "one folder")
    assert drifted != _kit_block()


def test_tool_and_prompt_names_on_the_page_exist():
    text = _read(PAGE) + _read(LLMS)
    for tool in ("my_work", "claim_task", "read_playbook"):
        assert tool in TOOLS, tool
        assert tool in text
    for prompt in ("set_up_agent_team", "plan_slices", "write_brief", "review_my_fleet", "run_retro"):
        assert prompt in PROMPTS, prompt
        assert prompt in text
