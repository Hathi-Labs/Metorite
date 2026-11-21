"""The kit packs: one folder per client under packs/ (KIT-2b). R7: this file is the fence.

A pack is static files outside content/, so the loader never serves them as topics.
A later pack adds a pack by adding a folder and a row to CLIENT_FILES.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

import pytest
from acb_playbook import load_all
from acb_playbook.loader import CONTENT_DIR

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "tests/unit"))

from test_playbook_org_neutral import hits  # noqa: E402

PACKS_DIR = CONTENT_DIR.parent / "packs"
CORE = CONTENT_DIR / "kit" / "agents-md.md"
VERSION_LINE = re.compile(r"^kit_version: (\d+)$", re.M)
# The files each client pack must hold. Add a row for a new client.
CLIENT_FILES = {
    "codex": ["README.md", "config.toml.example"],
    "copilot": ["README.md", "copilot-instructions.md", "vscode-mcp.json.example", "coding-agent-mcp.json.example"],
    "gemini": ["README.md", "GEMINI.md", "settings.json.example"],
    "antigravity": ["README.md", "mcp_config.json.example"],
}
PACKS = sorted(p.name for p in PACKS_DIR.iterdir() if p.is_dir() and not p.name.startswith("_"))
PACK_FILES = [f for pack in PACKS for f in sorted((PACKS_DIR / pack).rglob("*")) if f.is_file() and f.suffix != ".pyc"]

SECRET_PATTERNS = [
    re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key)\b\s*[:=]\s*[\"']?[A-Za-z0-9_\-./+=]{6,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-./+=]{6,}"),
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
    re.compile(r"\bmbt_[A-Za-z0-9_-]{10,}"),
    # Vendor token prefixes: GitHub, Anthropic and OpenAI style keys, Slack.
    re.compile(r"\b(?:gh[pousr]_|github_pat_|sk-|xox[bapr]-)[A-Za-z0-9_-]{16,}"),
]


def secret_hits(text: str) -> list[str]:
    """The patterns that match the text. Comment-free: a comment can leak too."""
    return [p.pattern for p in SECRET_PATTERNS if p.search(text)]


def _core_version() -> int:
    return int(VERSION_LINE.findall(CORE.read_text(encoding="utf-8"))[0])


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_every_pack_is_known_and_has_its_files():
    assert PACKS, "no pack found"
    for pack in PACKS:
        assert pack in CLIENT_FILES, f"add {pack} to CLIENT_FILES"
        for name in CLIENT_FILES[pack]:
            assert (PACKS_DIR / pack / name).is_file(), f"{pack}/{name} missing"


@pytest.mark.parametrize("pack", PACKS)
def test_the_pack_readme_points_to_the_core_with_its_version(pack):
    text = _text(PACKS_DIR / pack / "README.md")
    assert "agents-md.md" in text
    assert "kit/skill" in text
    assert [int(v) for v in VERSION_LINE.findall(text)] == [_core_version()]
    assert len(text.splitlines()) <= 40, "a pack file is thin and holds no copy of the core"
    assert "## The protocol" not in text


@pytest.mark.parametrize("path", PACK_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_a_pack_file_holds_no_secret(path):
    assert secret_hits(_text(path)) == [], path.name


@pytest.mark.parametrize("path", PACK_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_a_pack_file_holds_no_org_term(path):
    assert hits(_text(path)) == [], path.name


@pytest.mark.parametrize(
    "planted",
    [
        'token = "abcd1234efgh5678"',
        "password: hunter2hunter2",
        "Authorization: Bearer abcdef123456",
        "A" * 44,
        "deadbeef" * 5,
        "mbt_" + "x" * 43,
        "ghp_" + "a1B2" * 9,
        "sk-ant-" + "q9" * 12,
    ],
)
def test_a_planted_secret_fails_the_scan(planted):
    assert secret_hits("clean text\n") == []
    assert secret_hits("clean text\n" + planted), planted


def test_the_pack_files_are_not_loaded_as_topics():
    slugs = {t.slug for t in load_all()}
    assert not any(s.startswith(("packs/", "codex")) for s in slugs)
    assert PACKS_DIR.resolve() not in CONTENT_DIR.resolve().parents
    assert not (CONTENT_DIR / "packs").exists()


# --- Codex ---


def test_codex_config_is_valid_toml_with_a_remote_metorite_server():
    data = tomllib.loads(_text(PACKS_DIR / "codex" / "config.toml.example"))
    server = data["mcp_servers"]["metorite"]
    assert server["url"].startswith("https://")
    assert set(server) == {"url"}


def test_codex_readme_has_the_install_steps_in_order():
    text = _text(PACKS_DIR / "codex" / "README.md")
    order = ["Copy `kit/agents-md.md`", "config.toml.example", "codex mcp login metorite", "Call my_work"]
    positions = [text.index(s) for s in order]
    assert positions == sorted(positions)
    assert "project_doc_max_bytes" in text and "~/.codex/AGENTS.md" in text


def test_codex_readme_sentences_are_short():
    text = _text(PACKS_DIR / "codex" / "README.md")
    for line in text.splitlines():
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            assert len(sentence.split()) <= 25, sentence


def test_the_core_stays_under_the_codex_limit():
    assert len(CORE.read_bytes()) < 32 * 1024


# --- Copilot, Gemini CLI, Antigravity ---


def _json(pack: str, name: str) -> dict:
    return json.loads(_text(PACKS_DIR / pack / name))


@pytest.mark.parametrize("path", [p for p in PACK_FILES if p.name.endswith(".json.example")], ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_json_example_parses_as_written(path):
    assert isinstance(json.loads(_text(path)), dict)


def test_vscode_mcp_uses_servers_with_http_and_no_inline_secret():
    data = _json("copilot", "vscode-mcp.json.example")
    server = data["servers"]["metorite"]
    assert server["type"] == "http" and server["url"].startswith("https://")
    assert "mcpServers" not in data
    assert not {"headers", "env"} & set(server)  # a secret would go through `inputs`


def test_coding_agent_mcp_has_type_tools_and_url():
    server = _json("copilot", "coding-agent-mcp.json.example")["mcpServers"]["metorite"]
    assert server["type"] == "http" and server["tools"] and server["url"].startswith("https://")


def test_gemini_settings_read_agents_md_and_use_httpurl():
    data = _json("gemini", "settings.json.example")
    assert "AGENTS.md" in data["context"]["fileName"]
    assert data["mcpServers"]["metorite"]["httpUrl"].startswith("https://")


def test_antigravity_uses_serverurl_and_not_httpurl():
    server = _json("antigravity", "mcp_config.json.example")["mcpServers"]["metorite"]
    assert server["serverUrl"].startswith("https://")
    assert "httpUrl" not in server and "url" not in server


def test_antigravity_ships_no_rule_file_and_reuses_the_gemini_file():
    assert [p.name for p in (PACKS_DIR / "antigravity").rglob("*.md")] == ["README.md"]
    assert "packs/gemini/GEMINI.md" in _text(PACKS_DIR / "antigravity" / "README.md")


@pytest.mark.parametrize("rel", ["copilot/copilot-instructions.md", "gemini/GEMINI.md"])
def test_a_pointer_file_names_the_core_and_its_version(rel):
    text = _text(PACKS_DIR / rel)
    assert "AGENTS.md" in text and "kit/agents-md.md" in text
    assert [int(v) for v in VERSION_LINE.findall(text)] == [_core_version()]
    assert len(text.splitlines()) <= 15
    assert "## The protocol" not in text


def test_pack_readme_sentences_are_short_for_every_pack():
    for pack in PACKS:
        for line in _text(PACKS_DIR / pack / "README.md").splitlines():
            for sentence in re.split(r"(?<=[.!?])\s+", line):
                assert len(sentence.split()) <= 25, (pack, sentence)
