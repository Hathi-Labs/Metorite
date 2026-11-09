"""The client proof of a box token, run against the real Claude Code CLI.

EA-19e, ``external_agent_access.md`` §10.9 ("The client proof"). A box token
works only because Claude Code sends a static ``Authorization`` header from a
user-scope MCP entry, and starts no OAuth when the server refuses it. This
file repeats that proof on every run, so a Claude Code release that changes
the behaviour turns it red.

The test starts a throwaway MCP server on ``127.0.0.1`` (a free port, in a
thread). It answers 401 for every bearer but one, and its 401 names an OAuth
resource document, to tempt a client into discovery. Claude Code runs with a
temp ``HOME`` and a temp ``CLAUDE_CONFIG_DIR``. The real home, the live
``metorite`` entry and ``~/.claude/.credentials.json`` are never touched.

- The right token: ``claude mcp list`` says Connected. The server saw
  ``Authorization: Bearer <token>`` on ``initialize``, and no request to
  ``/.well-known/*``, ``/register``, ``/authorize`` or ``/token``.
- A wrong token: "Failed to connect", with "OAuth fallback is disabled".
- ``claude mcp get`` and ``.claude.json`` hold ``${G17_BOX_TOKEN}``, never
  the value.
- ``scripts/box_connect.sh`` against the same server, end to end.
- A project ``.mcp.json`` that names the same server with no header, and
  approved in the settings, wins over the user entry. ``claude mcp get``
  then names the Project scope, and the script stops with exit 7. With the
  approval removed, the user entry wins.

The file skips when ``claude`` is not on the path. The sample tokens are made
at run time, so no token literal is in the repo (``.gitleaks.toml``).

Mutations that turn this file red (R7): a ``box_connect.sh`` that writes the
literal token into the MCP entry (the ``.claude.json`` checks), a Claude
Code that falls back to OAuth on a 401 (the OAuth path checks), and a verify
step that ignores the scope of ``claude mcp get`` (the project case). The mutation
list of the secret rules is in ``test_box_token_secret_rules.py``.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "box_connect.sh"
_CLAUDE = shutil.which("claude")

pytestmark = pytest.mark.skipif(_CLAUDE is None, reason="claude is not on the PATH")

_OAUTH_PATHS = ("/.well-known", "/register", "/authorize", "/token")


def _sample_token() -> str:
    # Built from pieces at run time: the repo holds no 47-character literal.
    return "mbt" + "_" + secrets.token_urlsafe(32)


class _Handler(BaseHTTPRequestHandler):
    """The request handler. ``self.server.state`` is the :class:`_Server`."""

    @property
    def outer(self) -> _Server:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, *_a) -> None:  # keep the test output clean
        return

    def _record(self, body: dict | None) -> None:
        self.outer.requests.append({
            "verb": self.command,
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "method": (body or {}).get("method"),
        })

    def _send(self, code: int, payload: dict | None = None,
              extra: dict | None = None) -> None:
        data = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(code)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _unauthorized(self) -> None:
        port = self.outer.port
        self._send(401, {"error": "invalid_token"}, {
            "WWW-Authenticate": (
                'Bearer error="invalid_token", resource_metadata='
                f'"http://127.0.0.1:{port}/.well-known/oauth-protected-resource"'),
        })

    def do_GET(self) -> None:
        self._record(None)
        if self.path.startswith(_OAUTH_PATHS):
            self._send(404, {"error": "not_found"})
            return
        if self.headers.get("Authorization") != f"Bearer {self.outer.good}":
            self._unauthorized()
            return
        self._send(405, None, {"Allow": "POST"})

    def do_DELETE(self) -> None:
        self._record(None)
        self._send(405, None, {"Allow": "POST"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        if isinstance(body, list):  # a batch: record the first item
            body = body[0] if body else {}
        self._record(body)
        if self.path.startswith(_OAUTH_PATHS):
            self._send(404, {"error": "not_found"})
            return
        if self.headers.get("Authorization") != f"Bearer {self.outer.good}":
            self._unauthorized()
            return
        method = body.get("method")
        if "id" not in body:  # a notification
            self._send(202)
            return
        if method == "initialize":
            version = (body.get("params") or {}).get("protocolVersion", "2025-06-18")
            result = {"protocolVersion": version,
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "throwaway", "version": "0"}}
        elif method == "tools/list":
            result = {"tools": []}
        else:
            result = {}
        self._send(200, {"jsonrpc": "2.0", "id": body["id"], "result": result})


class _Server:
    """A throwaway MCP server that takes one bearer and refuses all others."""

    def __init__(self, good_token: str) -> None:
        self.good = good_token
        self.requests: list[dict] = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.state = self  # type: ignore[attr-defined]
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def __enter__(self) -> _Server:
        self.thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=10)


def _env(home: Path, **extra: str) -> dict[str, str]:
    """A small env: the temp HOME and config dir, and nothing of the real home."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(home / "cfg"),
        "LANG": "C.UTF-8",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    env.update(extra)
    return env


def _claude(home: Path, *args: str, timeout: int = 90, cwd: Path | None = None,
            **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["timeout", "60", str(_CLAUDE), *args],
        cwd=cwd or home, env=_env(home, **extra), capture_output=True, text=True,
        encoding="utf-8", timeout=timeout, check=False,
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    (h / "cfg").mkdir(parents=True)
    real = Path(os.path.expanduser("~")).resolve()
    assert real not in h.resolve().parents and h.resolve() != real
    return h


def _line(out: str, name: str) -> str:
    return next((ln for ln in out.splitlines() if ln.startswith(f"{name}:")), "")


def _oauth_requests(server: _Server) -> list[dict]:
    return [r for r in server.requests if r["path"].startswith(_OAUTH_PATHS)]


def test_the_right_token_connects_with_the_bearer_and_no_oauth(home: Path) -> None:
    good = _sample_token()
    with _Server(good) as server:
        entry = {"type": "http", "url": server.url,
                 "headers": {"Authorization": "Bearer ${G17_BOX_TOKEN}"}}
        added = _claude(home, "mcp", "add-json", "-s", "user", "throwaway", json.dumps(entry))
        assert added.returncode == 0, added.stdout + added.stderr

        listed = _claude(home, "mcp", "list", G17_BOX_TOKEN=good)
        line = _line(listed.stdout, "throwaway")
        assert "Connected" in line and "Failed" not in line, listed.stdout + listed.stderr

        inits = [r for r in server.requests if r["method"] == "initialize"]
        assert inits, server.requests
        assert all(r["auth"] == f"Bearer {good}" for r in inits)
        assert any(r["method"] == "tools/list" for r in server.requests), server.requests
        assert _oauth_requests(server) == []

    # The config holds the ${...} form, and never the value.
    stored = (home / "cfg" / ".claude.json").read_text(encoding="utf-8")
    assert "Bearer ${G17_BOX_TOKEN}" in stored
    assert good not in stored


def test_a_wrong_token_fails_loud_and_starts_no_oauth(home: Path) -> None:
    good = _sample_token()
    wrong = _sample_token()
    with _Server(good) as server:
        entry = {"type": "http", "url": server.url,
                 "headers": {"Authorization": "Bearer ${G17_BOX_TOKEN}"}}
        assert _claude(home, "mcp", "add-json", "-s", "user", "throwaway",
                       json.dumps(entry)).returncode == 0

        listed = _claude(home, "mcp", "list", G17_BOX_TOKEN=wrong)
        line = _line(listed.stdout, "throwaway")
        assert "Failed to connect" in line, listed.stdout + listed.stderr
        assert "OAuth fallback is disabled" in line, line
        assert wrong not in listed.stdout + listed.stderr
        assert server.requests, "the server saw no request"
        assert all(r["auth"] in (f"Bearer {wrong}", None) for r in server.requests)
        assert _oauth_requests(server) == []


def test_mcp_get_shows_the_variable_and_not_the_value(home: Path) -> None:
    good = _sample_token()
    with _Server(good) as server:
        entry = {"type": "http", "url": server.url,
                 "headers": {"Authorization": "Bearer ${G17_BOX_TOKEN}"}}
        assert _claude(home, "mcp", "add-json", "-s", "user", "throwaway",
                       json.dumps(entry)).returncode == 0
        got = _claude(home, "mcp", "get", "throwaway", G17_BOX_TOKEN=good)
    assert "${G17_BOX_TOKEN}" in got.stdout, got.stdout + got.stderr
    assert good not in got.stdout + got.stderr


def _run_script(home: Path, token: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_SCRIPT), "--home", str(home), "--config-dir", str(home / "cfg"), *args],
        input=token + "\n", cwd=home, env=_env(home), capture_output=True,
        text=True, encoding="utf-8", timeout=120, check=False,
    )


def test_box_connect_script_end_to_end_with_the_real_client(home: Path) -> None:
    good = _sample_token()
    with _Server(good) as server:
        run = _run_script(home, good, "--url", server.url)
        out = run.stdout + run.stderr
        assert run.returncode == 0, out
        assert good not in out
        assert "'metorite' is connected" in out
        inits = [r for r in server.requests if r["method"] == "initialize"]
        assert inits and all(r["auth"] == f"Bearer {good}" for r in inits)
        assert _oauth_requests(server) == []

    stored = (home / "cfg" / ".claude.json").read_text(encoding="utf-8")
    assert good not in stored
    servers = json.loads(stored)["mcpServers"]
    assert servers["metorite"] == {
        "type": "http", "url": server.url,
        "headers": {"Authorization": "Bearer ${METORITE_BOX_TOKEN}"},
    }
    env_file = home / ".config" / "metorite" / "box.env"
    assert env_file.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={good}\n"
    assert (env_file.stat().st_mode & 0o777) == 0o600


def test_box_connect_script_reports_a_revoked_token(home: Path) -> None:
    good = _sample_token()
    revoked = _sample_token()
    with _Server(good) as server:
        run = _run_script(home, revoked, "--url", server.url)
        out = run.stdout + run.stderr
        assert run.returncode == 7, out
        assert revoked not in out
        assert "OAuth fallback is disabled" in out
        assert "rejected the token" in out
        assert _oauth_requests(server) == []


def _scope(out: str) -> str:
    return next((ln.strip() for ln in out.splitlines() if ln.strip().startswith("Scope:")), "")


def test_an_approved_project_entry_wins_and_the_script_stops(home: Path, tmp_path: Path) -> None:
    good = _sample_token()
    proj = tmp_path / "checkout"
    proj.mkdir()
    settings = home / "cfg" / "settings.json"
    with _Server(good) as server:
        # Like the repo's .mcp.json: the same name, the same address, no header.
        (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"metorite": {
            "type": "http", "url": server.url}}}), encoding="utf-8")
        settings.write_text(json.dumps({"enabledMcpjsonServers": ["metorite"]}),
                            encoding="utf-8")

        run = _run_script(home, good, "--url", server.url, "--project", str(proj))
        out = run.stdout + run.stderr
        assert run.returncode == 7, out
        assert good not in out
        assert "Scope: Project config" in out
        assert "Connected" not in out and "is connected" not in out
        assert "disabledMcpjsonServers" in out

        # The real client agrees: in the checkout the project entry wins.
        got = _claude(home, "mcp", "get", "metorite", cwd=proj, METORITE_BOX_TOKEN=good)
        assert _scope(got.stdout).startswith("Scope: Project config"), got.stdout + got.stderr
        assert "${METORITE_BOX_TOKEN}" not in got.stdout

        # With the approval removed, the user entry with the header wins.
        settings.write_text("{}", encoding="utf-8")
        got = _claude(home, "mcp", "get", "metorite", cwd=proj, METORITE_BOX_TOKEN=good)
        assert _scope(got.stdout).startswith("Scope: User config"), got.stdout + got.stderr
        assert "Authorization: Bearer ${METORITE_BOX_TOKEN}" in got.stdout

        run = _run_script(home, good, "--url", server.url, "--project", str(proj), "--replace")
        out = run.stdout + run.stderr
        assert run.returncode == 0, out
        assert "'metorite' is connected" in out
        assert good not in out
