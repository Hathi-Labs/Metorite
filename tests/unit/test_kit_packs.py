"""The kit packs: one folder per client under packs/ (KIT-2b). R7: this file is the fence.

A pack is static files outside content/, so the loader never serves them as topics.
KIT-2c and KIT-2d add a pack by adding a folder and a row to CLIENT_FILES.
"""

from __future__ import annotations

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
CLIENT_FILES = {"codex": ["README.md", "config.toml.example"]}
PACKS = sorted(p.name for p in PACKS_DIR.iterdir() if p.is_dir() and not p.name.startswith("_"))
PACK_FILES = [f for pack in PACKS for f in sorted((PACKS_DIR / pack).rglob("*")) if f.is_file() and f.suffix != ".pyc"]

SECRET_PATTERNS = [
    re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key)\b\s*[:=]\s*[\"']?[A-Za-z0-9_\-./+=]{6,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-./+=]{6,}"),
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
    re.compile(r"\bmbt_[A-Za-z0-9_-]{10,}"),
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
