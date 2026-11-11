"""Build the agent kit of one client as a zip (KIT-4a, ``orchestration_playbook.md`` §6a).

The zip holds the core (``AGENTS.md``), the skill folder, the files of the pack
of the client, the MCP address, a ``KIT_VERSION`` file, a ``SHA256SUMS`` file
and a short ``README.md``. It holds no token and no organization data. The
build is deterministic: the same input gives the same bytes.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

from acb_playbook.loader import CONTENT_DIR, parse_topic

CLIENTS = ("claude", "codex", "copilot", "gemini", "antigravity", "generic")

PACKS_DIR = CONTENT_DIR.parent / "packs"
#: The MCP address that the pack files hold. The build replaces this exact string.
DEFAULT_MCP_URL = "https://mcp.metorite.com/mcp"
_VERSION_LINE = re.compile(r"^kit_version: (\d+)$", re.M)
#: A fixed time, so the same input gives the same bytes.
_STAMP = (1980, 1, 1, 0, 0, 0)

#: ``{client: {zip path: file name in packs/<client>/}}``. README.md never ships as is.
PACK_PATHS: dict[str, dict[str, str]] = {
    "codex": {".codex/config.toml.example": "config.toml.example"},
    "copilot": {
        ".github/copilot-instructions.md": "copilot-instructions.md",
        ".vscode/mcp.json.example": "vscode-mcp.json.example",
        "coding-agent-mcp.json.example": "coding-agent-mcp.json.example",
    },
    "gemini": {"GEMINI.md": "GEMINI.md", ".gemini/settings.json.example": "settings.json.example"},
    "antigravity": {
        "GEMINI.md": "../gemini/GEMINI.md",
        ".agents/mcp_config.json.example": "mcp_config.json.example",
    },
}

#: The third line of the README: the one step that is special to the client.
_STEP = {
    "claude": "Run: claude mcp add --transport http metorite {url}. CLAUDE.md imports AGENTS.md.",
    "codex": "Add .codex/config.toml.example to .codex/config.toml, then run: codex mcp login metorite.",
    "copilot": "Rename .vscode/mcp.json.example to .vscode/mcp.json. For GitHub.com, use coding-agent-mcp.json.example.",
    "gemini": "Merge .gemini/settings.json.example into .gemini/settings.json, then run: /mcp auth metorite.",
    "antigravity": "Rename .agents/mcp_config.json.example to .agents/mcp_config.json, then restart Antigravity.",
    "generic": "Add mcp.json.example to the MCP settings of your client. The address is {url}.",
}


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _body(path: Path) -> str:
    """A topic file without its playbook header block."""
    return parse_topic(_text(path)).body.rstrip() + "\n"


def kit_version() -> int:
    """The ``kit_version`` of the core. The MCP reports the same value."""
    return int(_VERSION_LINE.findall(_text(CONTENT_DIR / "kit" / "agents-md.md"))[0])


def _readme(client: str, mcp_url: str, version: int) -> str:
    lines = [
        f"Metorite agent kit for {client}, kit_version {version}.",
        "Unzip the files into the root of your project.",
        _STEP[client].format(url=mcp_url),
        "Sign in with OAuth, or mint a box token. The kit holds no token.",
        "Then send the first message: Call my_work.",
    ]
    return "\n".join(lines) + "\n"


def build_files(client: str, mcp_url: str) -> dict[str, bytes]:
    """``{path: bytes}`` of the kit, with ``SHA256SUMS`` included."""
    if client not in CLIENTS:
        raise ValueError("unknown client")
    url = str(mcp_url or "").strip()
    if not url:
        raise ValueError("the MCP address is empty")
    version = kit_version()
    text: dict[str, str] = {"AGENTS.md": _body(CONTENT_DIR / "kit" / "agents-md.md")}
    for path in sorted((CONTENT_DIR / "kit" / "skill").glob("*.md")):
        text[f"kit/skill/{path.name}"] = _body(path)
    for dest, name in PACK_PATHS.get(client, {}).items():
        text[dest] = _text(PACKS_DIR / client / name)
    if client == "claude":
        text["CLAUDE.md"] = "@AGENTS.md\n\nThe Claude plugin pack comes in a later release.\n"
    if client == "generic":
        text["mcp.json.example"] = json.dumps({"mcpServers": {"metorite": {"url": url}}}, indent=2) + chr(10)
    files = {p: t.replace(DEFAULT_MCP_URL, url).encode("utf-8") for p, t in text.items()}
    files["README.md"] = _readme(client, url, version).encode("utf-8")
    files["KIT_VERSION"] = f"{version}\n".encode()
    sums = "".join(f"{hashlib.sha256(files[p]).hexdigest()}  {p}\n" for p in sorted(files))
    files["SHA256SUMS"] = sums.encode("utf-8")
    return files


def build_kit_zip(client: str, mcp_url: str) -> bytes:
    """The zip of one client. Deterministic: fixed times, sorted entries."""
    files = build_files(client, mcp_url)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, _STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            zf.writestr(info, files[path])
    return buf.getvalue()
