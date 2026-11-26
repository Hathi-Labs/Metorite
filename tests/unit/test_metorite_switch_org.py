"""The org switch of a machine: ``scripts/metorite_switch_org.sh`` (EA-19h).

MCP-G17h, Metorite task 4034. Owner directive 2026-11-26: a machine stays
signed in, unless an owner switches its organization. The switch swaps the
token in ``box.env``, and the header helper of the user entry (EA-19g) reads
it on the next connect.

Hermetic. Each case runs with a temp ``HOME``, a temp ``CLAUDE_CONFIG_DIR``,
a fake ``claude`` on the path (``agents --json`` and ``stop``, with a log of
each argv), and a throwaway MCP server on ``127.0.0.1`` that answers
``initialize`` and ``tools/call whoami`` for two known tokens. The real home,
the real ``.claude.json`` and the real lanes are never touched. The sample
tokens are made at run time, so the repo holds no token literal.

Mutations that turn this file red (R7):

1. No restore of ``box.env.prev`` after a failed whoami (the
   ``copy_atomic "$prev_file" "$env_file"`` line removed):
   ``test_a_failed_whoami_puts_the_old_box_env_back``.
2. No refusal inside a Claude session (the ``CLAUDECODE`` check removed):
   ``test_the_switch_refuses_inside_a_claude_session``.
3. A switch that ignores live sessions: ``test_live_sessions_stop_the_switch``.
4. A token on stdout or in a log (for example ``say "$tok"``): the
   ``_assert_no_token`` checks of the success case.
5. No helper check of the org file: ``test_a_loose_org_file_is_refused``.
6. No signal trap: ``test_a_sigterm_during_the_proof_puts_the_old_box_env_back``.
7. No ``flock``: ``test_a_second_run_exits_at_once_while_the_lock_is_held``.
8. No ``S_ISREG`` check of ``box.env.prev``: ``test_a_symlink_is_refused_before_any_stop``.
9. No host rule, or no user-name rule: ``test_a_foreign_address_is_refused_and_gets_no_request``.
10. A proof that follows a redirect:
    ``test_a_redirect_from_initialize_fails_and_the_target_gets_nothing``.
11. No wait loop: ``test_the_wait_after_a_stop_polls_until_the_session_ends``. No
    check after it: ``test_a_session_that_never_ends_stops_the_switch``.
12. No read of the sessions after the proof:
    ``test_a_session_that_starts_during_the_switch_is_named``.
13. No lost-token check: ``test_a_token_that_no_org_file_holds_is_refused``.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "metorite_switch_org.sh"
_HELPER = _ROOT / "scripts" / "metorite_box_headers.sh"
_PREFIX = "mbt" + "_"
_REFUSED = "box-token-helper-refused"


def _token() -> str:
    return _PREFIX + secrets.token_urlsafe(32)


def _fragments(token: str) -> list[str]:
    tail = token[len(_PREFIX):]
    return [tail[i:i + 8] for i in range(0, len(tail) - 7)]


def _assert_no_token(text: str, *tokens: str) -> None:
    for tok in tokens:
        assert tok not in text
        assert not [f for f in _fragments(tok) if f in text], "a part of a token was printed"


# ── The throwaway MCP server ────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a) -> None:
        return

    def _send(self, code: int, payload: dict | None) -> None:
        data = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def do_POST(self) -> None:
        srv = self.server.state  # type: ignore[attr-defined]
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.loads(raw or b"{}")
        auth = self.headers.get("Authorization") or ""
        srv.requests.append({"auth": auth, "method": body.get("method")})
        tok = auth.removeprefix("Bearer ")
        who = srv.known.get(tok)
        if who is None:
            self._send(401, {"error": "invalid_token"})
            return
        if tok in srv.redirect and body.get("method") == "initialize":
            self.send_response(302)
            self.send_header("Location", srv.redirect[tok])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if body.get("method") == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "throwaway", "version": "0"}}
        elif body.get("method") == "tools/call" and body["params"]["name"] == "whoami":
            if tok in srv.delay:
                srv.delayed.set()
                srv.release.wait(timeout=30)
            if tok in srv.on_whoami:
                srv.on_whoami[tok]()
            who_out = {"email": who[0], "organization_id": who[1]}
            result = {"structuredContent": who_out,
                      "content": [{"type": "text", "text": json.dumps(who_out)}]}
        else:
            result = {}
        self._send(200, {"jsonrpc": "2.0", "id": body.get("id"), "result": result})


class _Server:
    def __init__(self, known: dict[str, tuple[str, str]], host: str = "127.0.0.1") -> None:
        self.known = known
        self.host = host
        self.requests: list[dict] = []
        self.delay: set[str] = set()  # tokens whose whoami waits for release
        self.delayed = threading.Event()
        self.release = threading.Event()
        self.redirect: dict[str, str] = {}  # token -> Location of a 302 on initialize
        self.on_whoami: dict[str, object] = {}  # token -> a callable run at whoami
        self.httpd = ThreadingHTTPServer((host, 0), _Handler)
        self.httpd.state = self  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.httpd.server_address[1]}/mcp"

    def __enter__(self) -> _Server:
        self.thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=10)


# ── The fake claude ─────────────────────────────────────────────────────────

_FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\n")
path = os.environ["FAKE_AGENTS"]
agents = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
if args[:2] == ["agents", "--json"]:
    # A stopped session stays live for FAKE_STOP_POLLS reads (-1: for ever).
    for a in agents:
        left = a.get("polls_left")
        if left == 0:
            a.pop("pid", None)
            a.pop("polls_left", None)
        elif isinstance(left, int) and left > 0:
            a["polls_left"] = left - 1
    json.dump(agents, open(path, "w", encoding="utf-8"))
    print(json.dumps(agents))
    sys.exit(0)
if args[:1] == ["stop"] and len(args) == 2:
    polls = int(os.environ.get("FAKE_STOP_POLLS", "0"))
    for a in agents:
        if a.get("id") == args[1]:
            a["state"] = "stopped"
            if polls == 0:
                a.pop("pid", None)
            else:
                a["polls_left"] = polls
    json.dump(agents, open(path, "w", encoding="utf-8"))
    sys.exit(0)
sys.exit(3)
'''


class Box:
    """A temp machine: HOME, the helper, the user entry, box.env, a fake claude."""

    def __init__(self, tmp: Path, url: str) -> None:
        self.home = tmp / "home"
        self.cfg = self.home / "cfg"
        self.cfg.mkdir(parents=True)
        real = Path(os.path.expanduser("~")).resolve()
        assert real not in self.home.resolve().parents and self.home.resolve() != real
        self.bin = tmp / "bin"
        self.bin.mkdir()
        fake = self.bin / "claude"
        fake.write_text(_FAKE_CLAUDE, encoding="utf-8")
        fake.chmod(0o755)
        self.log = tmp / "claude_calls.jsonl"
        self.agents = tmp / "agents.json"
        self.agents.write_text("[]", encoding="utf-8")
        # The installed helper and the user entry, as box_connect.sh writes them.
        self.helper = self.home / ".local" / "bin" / "metorite-box-headers"
        self.helper.parent.mkdir(parents=True)
        shutil.copyfile(_HELPER, self.helper)
        self.helper.chmod(0o700)
        self.dir = self.home / ".config" / "metorite"
        self.dir.mkdir(parents=True, mode=0o700)
        self.env_file = self.dir / "box.env"
        self.entry({"type": "http", "url": url,
                    "headersHelper": f"{self.helper} {self.env_file}"})

    def entry(self, entry: dict | None) -> None:
        data = {"mcpServers": {"metorite": entry}} if entry else {"mcpServers": {}}
        (self.cfg / ".claude.json").write_text(json.dumps(data), encoding="utf-8")

    def box_env(self, tok: str) -> None:
        self.env_file.write_text(f"METORITE_BOX_TOKEN={tok}\n", encoding="utf-8")
        self.env_file.chmod(0o600)

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.home),
            "CLAUDE_CONFIG_DIR": str(self.cfg),
            "LANG": "C.UTF-8",
            "FAKE_CLAUDE_LOG": str(self.log),
            "FAKE_AGENTS": str(self.agents),
        }
        env.update(extra)
        return env

    def org_file(self, label: str, tok: str) -> Path:
        orgs = self.dir / "orgs"
        orgs.mkdir(mode=0o700, exist_ok=True)
        f = orgs / f"{label}.env"
        f.write_text(f"METORITE_BOX_TOKEN={tok}\n", encoding="utf-8")
        f.chmod(0o600)
        return f

    def cmd(self, *args: str, xtrace: bool = False) -> list[str]:
        # The fake must be the claude that the script finds: never a real lane.
        assert shutil.which("claude", path=self.env()["PATH"]) == str(self.bin / "claude")
        return ["bash"] + (["-x"] if xtrace else []) + [str(_SCRIPT), *args]

    def run(self, *args: str, stdin: str | None = None, xtrace: bool = False,
            **extra: str) -> subprocess.CompletedProcess:
        cmd = self.cmd(*args, xtrace=xtrace)
        return subprocess.run(cmd, input=stdin if stdin is not None else "",
                              env=self.env(**extra), cwd=self.home, capture_output=True,
                              text=True, encoding="utf-8", timeout=120, check=False)

    def add(self, label: str, tok: str) -> None:
        run = self.run("add", label, stdin=tok + "\n")
        assert run.returncode == 0, run.stdout + run.stderr

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(ln) for ln in self.log.read_text(encoding="utf-8").splitlines()]

    def live(self, *sessions: tuple[str, str]) -> None:
        self.agents.write_text(json.dumps([
            {"id": sid, "name": name, "pid": 1000 + i, "sessionId": f"{sid}-full",
             "kind": "background", "status": "busy"}
            for i, (sid, name) in enumerate(sessions)
        ] + [{"id": "deadbeef", "name": "old-lane", "sessionId": "x", "state": "done"}]),
            encoding="utf-8")

    def add_live(self, sid: str, name: str) -> None:
        agents = json.loads(self.agents.read_text(encoding="utf-8"))
        agents.append({"id": sid, "name": name, "pid": 4242, "sessionId": f"{sid}-full"})
        self.agents.write_text(json.dumps(agents), encoding="utf-8")

    def agent_reads(self) -> int:
        return sum(1 for c in self.calls() if c[:2] == ["agents", "--json"])

    @property
    def active(self) -> Path:
        return self.dir / "active-org"

    @property
    def state_log(self) -> Path:
        return self.home / ".local" / "state" / "metorite" / "org-switch.log"

    @property
    def note_log(self) -> Path:
        return self.home / "lanes" / "merge-queue.log"


@pytest.fixture
def tokens() -> dict[str, str]:
    return {"a": _token(), "b": _token()}


@pytest.fixture
def server(tokens):
    known = {tokens["a"]: ("a@example.com", "org-a-id"),
             tokens["b"]: ("b@example.com", "org-b-id")}
    with _Server(known) as srv:
        yield srv


@pytest.fixture
def box(tmp_path: Path, server: _Server, tokens) -> Box:
    b = Box(tmp_path, server.url)
    b.box_env(tokens["a"])
    b.org_file("org-a", tokens["a"])  # the token in box.env is held by an org file
    return b


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


# ── add and list ────────────────────────────────────────────────────────────

def test_add_writes_a_0600_org_file_from_stdin(box: Box) -> None:
    tok = _token()
    run = box.run("add", "acme", stdin=tok + "\n")
    assert run.returncode == 0, run.stderr
    _assert_no_token(run.stdout + run.stderr, tok)
    f = box.dir / "orgs" / "acme.env"
    assert f.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={tok}\n"
    assert _mode(f) == 0o600
    assert _mode(box.dir / "orgs") == 0o700


def test_add_refuses_a_token_on_argv_and_does_not_echo_it(box: Box) -> None:
    tok = _token()
    run = box.run("add", "acme", tok, stdin="")
    assert run.returncode == 2
    _assert_no_token(run.stdout + run.stderr, tok)
    assert not (box.dir / "orgs" / "acme.env").exists()


@pytest.mark.parametrize("bad", [
    lambda: _token()[:-1], lambda: _token() + "x", lambda: "mbx_" + _token()[4:], lambda: "",
])
def test_add_refuses_a_bad_shape_and_writes_nothing(box: Box, bad) -> None:
    tok = bad()
    run = box.run("add", "acme", stdin=tok + "\n")
    assert run.returncode == 3, run.stderr
    if tok:
        _assert_no_token(run.stdout + run.stderr, tok)
    assert not (box.dir / "orgs" / "acme.env").exists()


def test_add_refuses_to_overwrite_without_replace(box: Box) -> None:
    first, second = _token(), _token()
    box.add("acme", first)
    run = box.run("add", "acme", stdin=second + "\n")
    assert run.returncode == 5, run.stderr
    f = box.dir / "orgs" / "acme.env"
    assert f.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={first}\n"
    run = box.run("add", "acme", "--replace", stdin=second + "\n")
    assert run.returncode == 0, run.stderr
    assert f.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={second}\n"


@pytest.mark.parametrize("label", ["Acme", "-acme", "a" * 33, "../x", "a_b", ""])
def test_a_bad_label_is_refused(box: Box, label: str) -> None:
    run = box.run("add", label, stdin=_token() + "\n")
    assert run.returncode == 2, run.stderr


def test_list_marks_the_active_org_and_prints_no_token(box: Box, tokens) -> None:
    box.add("org-b", tokens["b"])
    box.active.write_text("org-a\n", encoding="utf-8")
    run = box.run("list")
    assert run.returncode == 0, run.stderr
    assert "* org-a (active)" in run.stdout
    assert "  org-b" in run.stdout
    _assert_no_token(run.stdout + run.stderr, tokens["a"], tokens["b"])


# ── to: the refusals ────────────────────────────────────────────────────────

def test_an_unknown_label_is_refused(box: Box, tokens) -> None:
    run = box.run("to", "nope")
    assert run.returncode == 8, run.stderr
    assert box.env_file.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={tokens['a']}\n"


def test_a_loose_org_file_is_refused(box: Box, tokens) -> None:
    box.add("org-b", tokens["b"])
    (box.dir / "orgs" / "org-b.env").chmod(0o644)
    run = box.run("to", "org-b")
    assert run.returncode == 8, run.stdout + run.stderr
    assert "must have mode 0600" in run.stderr
    _assert_no_token(run.stdout + run.stderr, tokens["a"], tokens["b"])
    assert box.env_file.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={tokens['a']}\n"
    assert not box.active.exists()


@pytest.mark.parametrize("entry", [
    None,
    {"type": "http", "url": "http://127.0.0.1:1/mcp"},
    {"type": "http", "url": "http://127.0.0.1:1/mcp", "headersHelper": "/other/helper /x/box.env"},
])
def test_no_headers_helper_entry_is_refused(box: Box, tokens, entry) -> None:
    box.add("org-b", tokens["b"])
    box.entry(entry)
    run = box.run("to", "org-b")
    assert run.returncode == 7, run.stdout + run.stderr
    assert "box_connect.sh" in run.stderr
    assert box.env_file.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={tokens['a']}\n"


def test_live_sessions_stop_the_switch(box: Box, tokens) -> None:
    box.add("org-b", tokens["b"])
    box.live(("aaaa1111", "lane-x"))
    run = box.run("to", "org-b")
    assert run.returncode == 9, run.stdout + run.stderr
    assert "--stop-lanes" in run.stderr
    assert "lane-x" in run.stdout
    assert not [c for c in box.calls() if c[:1] == ["stop"]]
    assert box.env_file.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={tokens['a']}\n"
    assert not box.active.exists()


@pytest.mark.parametrize("var", ["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID"])
def test_the_switch_refuses_inside_a_claude_session(box: Box, tokens, var: str) -> None:
    box.add("org-b", tokens["b"])
    box.live(("aaaa1111", "lane-x"))
    run = box.run("to", "org-b", "--stop-lanes", **{var: "1"})
    assert run.returncode == 9, run.stdout + run.stderr
    assert "inside a Claude session" in run.stderr
    assert not [c for c in box.calls() if c[:1] == ["stop"]]
    assert box.env_file.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={tokens['a']}\n"


def test_a_failed_whoami_puts_the_old_box_env_back(box: Box, tokens, server: _Server) -> None:
    unknown = _token()  # the right shape, but the server does not know it
    box.add("org-c", unknown)
    box.active.write_text("org-a\n", encoding="utf-8")
    run = box.run("to", "org-c")
    out = run.stdout + run.stderr
    assert run.returncode == 10, out
    assert "put back" in run.stderr
    _assert_no_token(out, tokens["a"], unknown)
    assert box.env_file.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={tokens['a']}\n"
    assert _mode(box.env_file) == 0o600
    assert box.active.read_text(encoding="utf-8") == "org-a\n"
    assert not box.state_log.exists()
    assert any(r["auth"] == f"Bearer {unknown}" for r in server.requests)


# ── to: the switch ──────────────────────────────────────────────────────────

def test_a_switch_writes_box_env_active_org_and_one_line_each(box: Box, tokens, server) -> None:
    box.add("org-b", tokens["b"])
    box.active.write_text("org-a\n", encoding="utf-8")
    box.note_log.parent.mkdir(parents=True)
    box.note_log.write_text("NOTE\tother\tolder line\n", encoding="utf-8")
    run = box.run("to", "org-b")
    out = run.stdout + run.stderr
    assert run.returncode == 0, out
    assert "b@example.com" in run.stdout and "org-b-id" in run.stdout

    assert box.env_file.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={tokens['b']}\n"
    prev = box.dir / "box.env.prev"
    assert prev.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={tokens['a']}\n"
    assert _mode(box.env_file) == 0o600 and _mode(prev) == 0o600
    assert box.active.read_text(encoding="utf-8") == "org-b\n"

    notes = box.note_log.read_text(encoding="utf-8").splitlines()
    assert len(notes) == 2
    assert re.fullmatch(
        r"NOTE\torg-switch\t\d\d:\d\d UTC: [A-Za-z0-9_.-]+: Metorite org org-a/org-a-id"
        r" -> org-b/org-b-id as b@example\.com\. Lanes stopped: 0\.", notes[1]), notes[1]
    logs = box.state_log.read_text(encoding="utf-8").splitlines()
    assert len(logs) == 1
    assert "org-a/org-a-id\torg-b/org-b-id\tb@example.com\tstopped=0" in logs[0]

    for text in (out, box.note_log.read_text(encoding="utf-8"),
                 box.state_log.read_text(encoding="utf-8"), box.log.read_text(encoding="utf-8")):
        _assert_no_token(text, tokens["a"], tokens["b"])
    # The new identity was proved with the new bearer, and no claude mcp command ran.
    whoami = [r for r in server.requests if r["method"] == "tools/call"]
    assert whoami[-1]["auth"] == f"Bearer {tokens['b']}"
    assert not [c for c in box.calls() if c[:1] == ["mcp"]]


def test_no_note_line_when_the_queue_log_is_absent(box: Box, tokens) -> None:
    box.add("org-b", tokens["b"])
    run = box.run("to", "org-b")
    assert run.returncode == 0, run.stdout + run.stderr
    assert not box.note_log.exists()
    assert len(box.state_log.read_text(encoding="utf-8").splitlines()) == 1


def test_stop_lanes_stops_each_live_session(box: Box, tokens) -> None:
    box.add("org-b", tokens["b"])
    box.live(("aaaa1111", "lane-x"), ("bbbb2222", "lane-y"))
    box.note_log.parent.mkdir(parents=True)
    box.note_log.write_text("", encoding="utf-8")
    run = box.run("to", "org-b", "--stop-lanes")
    out = run.stdout + run.stderr
    assert run.returncode == 0, out
    stops = [c for c in box.calls() if c[:1] == ["stop"]]
    assert stops == [["stop", "aaaa1111"], ["stop", "bbbb2222"]]  # never the ended session
    assert "box.sh start lane-x" in run.stdout and "box.sh start lane-y" in run.stdout
    assert box.note_log.read_text(encoding="utf-8").rstrip().endswith("Lanes stopped: 2.")
    assert "stopped=2\tlane-x lane-y" in box.state_log.read_text(encoding="utf-8")
    _assert_no_token(out, tokens["a"], tokens["b"])


def test_dry_run_changes_nothing(box: Box, tokens, server: _Server) -> None:
    box.add("org-b", tokens["b"])
    box.live(("aaaa1111", "lane-x"))
    run = box.run("to", "org-b", "--dry-run", "--stop-lanes")
    out = run.stdout + run.stderr
    assert run.returncode == 0, out
    assert "dry run" in run.stdout and "live sessions: 1" in run.stdout
    assert not [c for c in box.calls() if c[:1] == ["stop"]]
    assert box.env_file.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={tokens['a']}\n"
    assert not box.active.exists() and not box.state_log.exists()
    assert server.requests == []
    _assert_no_token(out, tokens["a"], tokens["b"])


def test_xtrace_never_prints_a_token(box: Box, tokens) -> None:
    run = box.run("add", "org-b", stdin=tokens["b"] + "\n", xtrace=True)
    assert run.returncode == 0, run.stderr
    run = box.run("to", "org-b", xtrace=True)
    assert run.returncode == 0, run.stderr
    _assert_no_token(run.stdout + run.stderr, tokens["a"], tokens["b"])


def test_the_script_holds_no_token_literal_and_one_token_shape() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    assert not re.search(_PREFIX + r"[A-Za-z0-9_-]{43}", text)
    assert "TOKEN_RE='" not in text
    assert '. "$helper_src"' in text
    assert "claude mcp" not in text.replace("No `claude mcp` command runs", "")


# ── Fix round 1 (the verifier and the review of 2026-11-26) ────────────────

def _assert_box_env_is(box: Box, tok: str) -> None:
    assert box.env_file.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={tok}\n"
    assert _mode(box.env_file) == 0o600


def test_a_sigterm_during_the_proof_puts_the_old_box_env_back(box: Box, tokens, server) -> None:
    box.add("org-b", tokens["b"])
    box.active.write_text("org-a\n", encoding="utf-8")
    server.delay.add(tokens["b"])
    proc = subprocess.Popen(box.cmd("to", "org-b"), env=box.env(), cwd=box.home,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        assert server.delayed.wait(timeout=60), "the proof never reached whoami"
        # The new token is in box.env now, and not proved.
        _assert_no_token(box.env_file.read_text(encoding="utf-8"), tokens["a"])
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=30)
    finally:
        server.release.set()
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 10, out + err
    assert "a signal stopped the switch" in err
    _assert_box_env_is(box, tokens["a"])
    assert box.active.read_text(encoding="utf-8") == "org-a\n"
    assert not box.state_log.exists()
    _assert_no_token(out + err, tokens["a"], tokens["b"])


def test_a_sigterm_with_no_old_box_env_removes_it(box: Box, tokens, server) -> None:
    box.env_file.unlink()
    box.add("org-b", tokens["b"])
    server.delay.add(tokens["b"])
    proc = subprocess.Popen(box.cmd("to", "org-b"), env=box.env(), cwd=box.home,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        assert server.delayed.wait(timeout=60)
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=30)
    finally:
        server.release.set()
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 10, out + err
    assert not box.env_file.exists()
    assert not box.active.exists()


@pytest.mark.parametrize("command", [("to", "org-b"), ("add", "org-c")])
def test_a_second_run_exits_at_once_while_the_lock_is_held(box: Box, tokens, command) -> None:
    box.add("org-b", tokens["b"])
    lock = box.dir / ".switch-org.lock"
    with open(lock, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        start = time.monotonic()
        run = box.run(*command, stdin=_token() + "\n")
        assert time.monotonic() - start < 20
    assert run.returncode == 12, run.stdout + run.stderr
    assert "another org switch runs" in run.stderr
    _assert_box_env_is(box, tokens["a"])
    assert not (box.dir / "orgs" / "org-c.env").exists()
    assert not box.active.exists()


@pytest.mark.parametrize("which", ["box.env", "box.env.prev"])
def test_a_symlink_is_refused_before_any_stop(box: Box, tokens, tmp_path: Path, which: str) -> None:
    box.add("org-b", tokens["b"])
    box.live(("aaaa1111", "lane-x"))
    elsewhere = tmp_path / "elsewhere.env"
    elsewhere.write_text(f"METORITE_BOX_TOKEN={tokens['a']}\n", encoding="utf-8")
    elsewhere.chmod(0o600)
    target = box.dir / which
    if target.exists():
        target.unlink()
    target.symlink_to(elsewhere)
    run = box.run("to", "org-b", "--stop-lanes")
    assert run.returncode == 11, run.stdout + run.stderr
    assert not [c for c in box.calls() if c[:1] == ["stop"]]
    assert box.agent_reads() == 0  # refused before the sessions were even read
    assert elsewhere.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={tokens['a']}\n"
    assert not box.active.exists()


def test_a_loose_box_env_is_refused_before_any_stop(box: Box, tokens) -> None:
    box.add("org-b", tokens["b"])
    box.live(("aaaa1111", "lane-x"))
    box.env_file.chmod(0o644)
    run = box.run("to", "org-b", "--stop-lanes")
    assert run.returncode == 11, run.stdout + run.stderr
    assert not [c for c in box.calls() if c[:1] == ["stop"]]


@pytest.mark.parametrize("make_url", [
    lambda port: f"https://evil.example:{port}/mcp",
    lambda port: f"http://mcp.metorite.com:{port}/mcp",   # not https
    lambda port: f"http://user@127.0.0.1:{port}/mcp",      # userinfo
    lambda port: f"http://127.0.0.2:{port}/mcp",           # loopback, but not the named host
])
def test_a_foreign_address_is_refused_and_gets_no_request(box: Box, tokens, make_url) -> None:
    box.add("org-b", tokens["b"])
    known = {tokens["a"]: ("a@example.com", "org-a-id"), tokens["b"]: ("b@example.com", "org-b-id")}
    with _Server(known, host="127.0.0.2") as foreign:
        url = make_url(foreign.httpd.server_address[1])
        box.entry({"type": "http", "url": url,
                   "headersHelper": f"{box.helper} {box.env_file}"})
        run = box.run("to", "org-b")
        assert run.returncode == 2, run.stdout + run.stderr
        assert foreign.requests == []
    _assert_box_env_is(box, tokens["a"])


def test_a_redirect_from_initialize_fails_and_the_target_gets_nothing(box: Box, tokens, server) -> None:
    box.add("org-b", tokens["b"])
    with _Server({tokens["b"]: ("b@example.com", "org-b-id")}) as target:
        server.redirect[tokens["b"]] = target.url
        run = box.run("to", "org-b")
        assert run.returncode == 10, run.stdout + run.stderr
        assert "HTTP 302" in run.stderr
        assert target.requests == []
    _assert_box_env_is(box, tokens["a"])
    assert not box.active.exists()


def test_the_wait_after_a_stop_polls_until_the_session_ends(box: Box, tokens) -> None:
    box.add("org-b", tokens["b"])
    box.live(("aaaa1111", "lane-x"))
    run = box.run("to", "org-b", "--stop-lanes", FAKE_STOP_POLLS="3")
    assert run.returncode == 0, run.stdout + run.stderr
    # One read before the stop, three reads that still show it live, one that
    # shows it ended, and one read after the proof.
    assert box.agent_reads() == 6
    _assert_box_env_is(box, tokens["b"])


def test_a_session_that_never_ends_stops_the_switch(box: Box, tokens) -> None:
    box.add("org-b", tokens["b"])
    box.live(("aaaa1111", "lane-x"))
    run = box.run("to", "org-b", "--stop-lanes", FAKE_STOP_POLLS="-1",
                  METORITE_SWITCH_STOP_WAIT_S="2")
    assert run.returncode == 9, run.stdout + run.stderr
    assert "still live after the stop" in run.stderr
    assert "box.sh start lane-x" in run.stdout
    _assert_box_env_is(box, tokens["a"])
    assert not (box.dir / "box.env.prev").exists()
    assert not box.active.exists()


def test_a_session_that_starts_during_the_switch_is_named(box: Box, tokens, server) -> None:
    box.add("org-b", tokens["b"])
    box.note_log.parent.mkdir(parents=True)
    box.note_log.write_text("", encoding="utf-8")
    server.on_whoami[tokens["b"]] = lambda: box.add_live("cccc3333", "lane-z")
    run = box.run("to", "org-b")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "started during the switch: lane-z. They hold the old org until a restart." in run.stdout
    note = box.note_log.read_text(encoding="utf-8")
    assert note.rstrip().endswith(
        "Lanes stopped: 0. Started during the switch: lane-z. They hold the old org until a restart.")
    assert "late=lane-z" in box.state_log.read_text(encoding="utf-8")


def test_a_token_that_no_org_file_holds_is_refused(box: Box, tokens) -> None:
    lost = _token()
    box.box_env(lost)
    box.add("org-b", tokens["b"])
    box.live(("aaaa1111", "lane-x"))
    run = box.run("to", "org-b", "--stop-lanes")
    out = run.stdout + run.stderr
    assert run.returncode == 11, out
    assert "add <label>" in run.stderr
    _assert_no_token(out, lost, tokens["b"])
    _assert_box_env_is(box, lost)
    assert not [c for c in box.calls() if c[:1] == ["stop"]]


def test_a_failed_proof_prints_the_restart_hints(box: Box, tokens) -> None:
    box.add("org-c", _token())  # unknown to the server
    box.live(("aaaa1111", "lane-x"))
    run = box.run("to", "org-c", "--stop-lanes")
    assert run.returncode == 10, run.stdout + run.stderr
    assert "box.sh start lane-x" in run.stdout
    _assert_box_env_is(box, tokens["a"])


def test_a_failed_state_log_only_warns(box: Box, tokens) -> None:
    box.add("org-b", tokens["b"])
    state = box.home / ".local" / "state"
    state.mkdir(parents=True)
    (state / "metorite").write_text("not a folder", encoding="utf-8")
    run = box.run("to", "org-b")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "WARNING" in run.stdout and "The switch is complete" in run.stdout
    _assert_box_env_is(box, tokens["b"])
    assert box.active.read_text(encoding="utf-8") == "org-b\n"
