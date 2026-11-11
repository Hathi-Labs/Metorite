"""The kit zip builder (KIT-4a). R7: this file is the fence of ``acb_playbook.kit_zip``."""

from __future__ import annotations

import hashlib
import re
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from acb_playbook import CLIENTS, build_kit_zip
from acb_playbook.kit_zip import DEFAULT_MCP_URL, PACKS_DIR

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_kit_packs import secret_hits
from test_playbook_org_neutral import hits

OTHER_URL = "https://mcp.example.test/mcp"
KIT_VERSION_LINE = re.compile(r"^kit_version: (\d+)$", re.M)


def _entries(client: str, url: str = DEFAULT_MCP_URL) -> dict[str, bytes]:
    with zipfile.ZipFile(BytesIO(build_kit_zip(client, url))) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


@pytest.mark.parametrize("client", CLIENTS)
def test_the_zip_is_deterministic(client):
    assert build_kit_zip(client, DEFAULT_MCP_URL) == build_kit_zip(client, DEFAULT_MCP_URL)


@pytest.mark.parametrize("client", CLIENTS)
def test_agents_md_has_one_version_line_equal_to_kit_version(client):
    files = _entries(client)
    text = files["AGENTS.md"].decode("utf-8")
    assert not text.startswith("---")
    found = KIT_VERSION_LINE.findall(text)
    assert len(found) == 1
    assert found[0] == files["KIT_VERSION"].decode().strip()


@pytest.mark.parametrize("client", CLIENTS)
def test_sha256sums_verify(client):
    files = _entries(client)
    lines = files["SHA256SUMS"].decode("utf-8").splitlines()
    listed = {}
    for line in lines:
        digest, _, path = line.partition("  ")
        listed[path] = digest
    assert [ln.partition("  ")[2] for ln in lines] == sorted(listed)
    assert set(listed) == set(files) - {"SHA256SUMS"}
    for path, digest in listed.items():
        assert hashlib.sha256(files[path]).hexdigest() == digest


@pytest.mark.parametrize("client", CLIENTS)
def test_no_path_escapes(client):
    for path in _entries(client):
        assert not path.startswith(("/", "\\"))
        assert ".." not in path.split("/")
        assert ":" not in path and "\\" not in path


@pytest.mark.parametrize("client", CLIENTS)
def test_no_secret_and_no_org_term(client):
    for path, data in _entries(client).items():
        if path == "SHA256SUMS":
            continue  # hex digests are not secrets
        text = data.decode("utf-8")
        assert not secret_hits(text), path
        assert not hits(text), path


@pytest.mark.parametrize("client", CLIENTS)
def test_the_mcp_address_is_substituted(client):
    files = _entries(client, OTHER_URL)
    joined = "\n".join(d.decode("utf-8") for p, d in files.items() if p != "SHA256SUMS")
    assert OTHER_URL in joined
    assert DEFAULT_MCP_URL not in joined


@pytest.mark.parametrize("client", [c for c in CLIENTS if (PACKS_DIR / c).is_dir()])
def test_every_pack_file_is_in_the_zip(client):
    zipped = list(_entries(client).values())
    for f in (PACKS_DIR / client).iterdir():
        if f.name == "README.md" or not f.is_file():
            continue
        body = f.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert body.encode("utf-8") in zipped, f.name


def test_the_skill_folder_is_in_every_zip():
    for client in CLIENTS:
        paths = _entries(client)
        assert "kit/skill/SKILL.md" in paths
        assert all(not d.decode().startswith("---") for p, d in paths.items() if p.startswith("kit/skill/"))


def test_per_client_install_paths():
    expect = {
        "codex": {".codex/config.toml.example"},
        "copilot": {".github/copilot-instructions.md", ".vscode/mcp.json.example", "coding-agent-mcp.json.example"},
        "gemini": {"GEMINI.md", ".gemini/settings.json.example"},
        "antigravity": {"GEMINI.md", ".agents/mcp_config.json.example"},
        "claude": {"CLAUDE.md"},
        "generic": {"mcp.json.example"},
    }
    for client, paths in expect.items():
        assert paths <= set(_entries(client)), client
    assert _entries("claude")["CLAUDE.md"].decode().splitlines()[0] == "@AGENTS.md"


def test_unknown_client_raises():
    with pytest.raises(ValueError):
        build_kit_zip("cursor", DEFAULT_MCP_URL)
    with pytest.raises(ValueError):
        build_kit_zip("../x", DEFAULT_MCP_URL)


@pytest.mark.parametrize("client", CLIENTS)
def test_readme_has_at_most_five_lines_and_no_semicolon(client):
    text = _entries(client)["README.md"].decode("utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert 1 <= len(lines) <= 5
    assert ";" not in text
    for ln in lines:
        for sentence in re.split(r"(?<=[.:])\s+", ln):
            assert len(sentence.split()) <= 20, sentence
