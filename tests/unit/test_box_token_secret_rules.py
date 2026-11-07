"""A box token never reaches a repo, a log or a terminal (EA-19e).

``external_agent_access.md`` §10.9 question 3. A box token is ``mbt_`` and 43
URL-safe characters. Three controls look for that shape:

- the ``metorite-box-token`` rule of ``.gitleaks.toml`` (pr-check runs it),
- the structlog processor ``redact_box_tokens`` and the stdlib filter
  ``BoxTokenRedactFilter`` in ``acb_common/_log.py``,
- ``scripts/box_connect.sh``, which reads the token from a hidden prompt or
  stdin and never prints it.

Hermetic. The script runs with a temp ``HOME``, a temp ``CLAUDE_CONFIG_DIR``
and a fake ``claude`` on the path that records its argv. Each sample token is
made at run time from pieces, so the repo holds no token literal. The gitleaks
cases scan a temp folder outside ``tests/``, because the global allowlist of
``.gitleaks.toml`` skips every ``tests/`` path. They skip when gitleaks is not
installed. The rule-shape case does not need gitleaks.

Mutations that turn this file red (R7):

1. A redactor that skips nested values (only top-level strings):
   ``test_the_redactor_masks_nested_kwargs``.
2. A script that echoes the token (for example ``say "$box_token"``): the
   ``*_never_prints_*`` cases.
3. A ``box.env`` of mode 0644: ``test_box_env_is_0600_under_a_loose_umask``.
4. A literal token in the MCP entry (``Bearer $box_token``):
   ``test_the_entry_holds_the_variable_and_argv_holds_no_token``.
5. A gitleaks rule with the wrong length (``{42}`` or ``{44}``):
   ``test_the_gitleaks_rule_has_the_shape_of_the_redactor`` and the two
   gitleaks scan cases.
"""
from __future__ import annotations

import io
import json
import logging
import os
import pty
import re
import secrets
import select
import shutil
import stat
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest
import structlog
from acb_common import _log

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "box_connect.sh"
_GITLEAKS_TOML = _ROOT / ".gitleaks.toml"
_GITLEAKS = shutil.which("gitleaks") or (
    str(Path.home() / ".local/bin/gitleaks")
    if (Path.home() / ".local/bin/gitleaks").exists() else None)

_PREFIX = "mbt" + "_"


def _token() -> str:
    """A sample box token, built at run time (never a literal in the repo)."""
    return _PREFIX + secrets.token_urlsafe(32)


def _fragments(token: str) -> list[str]:
    """Each 8-character window of the secret part, so a partial print shows."""
    tail = token[len(_PREFIX):]
    return [tail[i:i + 8] for i in range(0, len(tail) - 7)]


def _assert_no_token(text: str, token: str) -> None:
    assert token not in text
    leaked = [f for f in _fragments(token) if f in text]
    assert not leaked, "a part of the token was printed"


# ── 1. The gitleaks rule ────────────────────────────────────────────────────

def _rule() -> dict:
    conf = tomllib.loads(_GITLEAKS_TOML.read_text(encoding="utf-8"))
    rules = [r for r in conf.get("rules", []) if r.get("id") == "metorite-box-token"]
    assert len(rules) == 1, "the metorite-box-token rule is missing from .gitleaks.toml"
    return rules[0]


def test_the_gitleaks_rule_has_the_shape_of_the_redactor() -> None:
    rule = _rule()
    assert rule["regex"] == r"mbt_[A-Za-z0-9_-]{43}"
    assert rule["regex"] == _log.BOX_TOKEN_RE.pattern
    assert rule["keywords"] == ["mbt_"]


def _gitleaks_scan(folder: Path, report: Path) -> list[str]:
    run = subprocess.run(
        [_GITLEAKS, "detect", "--no-git", "--source", str(folder),
         "-c", str(_GITLEAKS_TOML), "--redact", "--exit-code", "1",
         "--report-format", "json", "--report-path", str(report)],
        capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
    )
    assert run.returncode in (0, 1), run.stderr
    findings = json.loads(report.read_text(encoding="utf-8") or "[]")
    return [f["RuleID"] for f in findings]


@pytest.mark.skipif(_GITLEAKS is None, reason="gitleaks is not installed")
def test_the_gitleaks_rule_fires_on_a_sample_token(tmp_path: Path) -> None:
    folder = tmp_path / "scan"
    folder.mkdir()
    (folder / "notes.txt").write_text(f"the box uses {_token()} today\n", encoding="utf-8")
    assert _gitleaks_scan(folder, tmp_path / "r.json") == ["metorite-box-token"]


@pytest.mark.skipif(_GITLEAKS is None, reason="gitleaks is not installed")
def test_the_gitleaks_rule_does_not_fire_on_a_short_tail(tmp_path: Path) -> None:
    folder = tmp_path / "scan"
    folder.mkdir()
    short = _token()[:-1]  # 42 characters after the prefix
    (folder / "notes.txt").write_text(f"the box uses {short} today\n", encoding="utf-8")
    assert "metorite-box-token" not in _gitleaks_scan(folder, tmp_path / "r.json")


# ── 2. The log redactor ─────────────────────────────────────────────────────

def test_the_redactor_masks_the_event_message() -> None:
    tok = _token()
    out = _log.redact_box_tokens(None, "info", {"event": f"connect with {tok} now"})
    assert out["event"] == f"connect with {_log.BOX_TOKEN_MASK} now"


def test_the_redactor_masks_nested_kwargs() -> None:
    tok = _token()
    event = {
        "event": "x",
        "headers": {"Authorization": f"Bearer {tok}"},
        "items": [1, {"deep": [f"a {tok}", ("t", tok)]}],
        "pair": (tok, "ok"),
        "count": 3,
    }
    out = _log.redact_box_tokens(None, "info", dict(event))
    dumped = repr(out)
    assert tok not in dumped
    assert out["headers"]["Authorization"] == f"Bearer {_log.BOX_TOKEN_MASK}"
    assert out["items"][1]["deep"][1] == ("t", _log.BOX_TOKEN_MASK)
    assert out["pair"] == (_log.BOX_TOKEN_MASK, "ok")
    assert out["count"] == 3


def test_the_redactor_leaves_a_clean_event_as_it_is() -> None:
    nested = {"a": ["b", ("c",)]}
    event = {"event": "plain", "n": nested}
    out = _log.redact_box_tokens(None, "info", event)
    assert out["n"] is nested


def test_the_redactor_masks_exception_text() -> None:
    tok = _token()
    try:
        raise ValueError(f"bad token {tok}")
    except ValueError:
        event = structlog.processors.format_exc_info(None, "error", {"event": "e", "exc_info": True})
    assert tok in event["exception"]
    out = _log.redact_box_tokens(None, "error", event)
    assert tok not in out["exception"]
    assert _log.BOX_TOKEN_MASK in out["exception"]


def test_the_redactor_masks_an_exception_object_value() -> None:
    tok = _token()
    out = _log.redact_box_tokens(None, "error", {"event": "e", "error": RuntimeError(tok)})
    assert tok not in repr(out)


def test_the_stdlib_filter_masks_a_record() -> None:
    tok = _token()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("test_box_token_secret_rules.stdlib")
    logger.propagate = False
    logger.addHandler(handler)
    _log.install_box_token_filter(logger)
    _log.install_box_token_filter(logger)  # idempotent
    assert sum(isinstance(f, _log.BoxTokenRedactFilter) for f in handler.filters) == 1
    try:
        logger.warning("token %s seen", tok)
        try:
            raise KeyError(tok)
        except KeyError:
            logger.exception("failed")
    finally:
        logger.removeHandler(handler)
    text = stream.getvalue()
    assert tok not in text
    assert text.count(_log.BOX_TOKEN_MASK) >= 2


def test_configure_logging_places_the_redactor_and_the_filter(capsys) -> None:
    saved = structlog.get_config()
    root = logging.getLogger()
    before = {id(h): list(h.filters) for h in root.handlers}
    tok = _token()
    try:
        _log.configure_logging("INFO", json_logs=True)
        procs = structlog.get_config()["processors"]
        assert procs.index(_log.redact_box_tokens) == len(procs) - 2
        assert procs.index(structlog.processors.format_exc_info) < procs.index(
            _log.redact_box_tokens)
        for h in root.handlers:
            assert any(isinstance(f, _log.BoxTokenRedactFilter) for f in h.filters)
        structlog.get_logger("box").info(f"hello {tok}", nested={"k": [tok]})
        out = capsys.readouterr().out
        assert "hello" in out
        assert tok not in out
    finally:
        structlog.configure(**saved)
        for h in root.handlers:
            h.filters[:] = before.get(id(h), [
                f for f in h.filters if not isinstance(f, _log.BoxTokenRedactFilter)])


# ── 3. scripts/box_connect.sh ───────────────────────────────────────────────

# A fake `claude`. It records each call as one JSON line: the argv, and a
# sha256 of METORITE_BOX_TOKEN when it is set (never the value). It keeps the
# user-scope entries in $CLAUDE_CONFIG_DIR/.claude.json like the real one.
_FAKE_CLAUDE = r'''#!/usr/bin/env python3
import hashlib, json, os, sys
args = sys.argv[1:]
tok = os.environ.get("METORITE_BOX_TOKEN")
rec = {"argv": args, "env_token_sha": hashlib.sha256(tok.encode()).hexdigest() if tok else None}
with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(rec) + "\n")
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
cfg = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.environ["HOME"], ".claude.json")
def load():
    try:
        with open(cfg, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
def save(d):
    with open(cfg, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
if args[:2] == ["mcp", "add-json"]:
    if mode == "fail-add":
        print("boom", file=sys.stderr); sys.exit(1)
    name, body = args[-2], args[-1]
    d = load(); servers = d.setdefault("mcpServers", {})
    if name in servers:
        print(f"MCP server {name} already exists in user config"); sys.exit(1)
    servers[name] = json.loads(body); save(d)
    print(f"Added http MCP server {name} to user config"); sys.exit(0)
if args[:2] == ["mcp", "remove"]:
    d = load(); d.get("mcpServers", {}).pop(args[-1], None); save(d); sys.exit(0)
if args[:2] == ["mcp", "list"]:
    url = load().get("mcpServers", {}).get("metorite", {}).get("url", "?")
    print("Checking MCP server health...\n")
    if mode == "reject":
        print(f"metorite: {url} (HTTP) - ✘ Failed to connect — Server rejected the configured "
              "Authorization header (HTTP 401). OAuth fallback is disabled when headers.Authorization is set")
    elif mode == "echo":
        print(f"metorite: {url} (HTTP) - ✘ Failed to connect — debug {tok}")
    else:
        print(f"metorite: {url} (HTTP) - ✔ Connected")
    sys.exit(0)
sys.exit(0)
'''


class Box:
    def __init__(self, tmp: Path) -> None:
        self.home = tmp / "home"
        self.cfg = self.home / "cfg"
        self.cfg.mkdir(parents=True)
        self.bin = tmp / "bin"
        self.bin.mkdir()
        fake = self.bin / "claude"
        fake.write_text(_FAKE_CLAUDE, encoding="utf-8")
        fake.chmod(0o755)
        self.log = tmp / "claude_calls.jsonl"

    def env(self, mode: str = "ok") -> dict[str, str]:
        return {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.home),
            "LANG": "C.UTF-8",
            "FAKE_CLAUDE_LOG": str(self.log),
            "FAKE_CLAUDE_MODE": mode,
        }

    def run(self, stdin: str | None, *args: str, mode: str = "ok", xtrace: bool = False,
            umask: int | None = None) -> subprocess.CompletedProcess:
        cmd = ["bash"] + (["-x"] if xtrace else []) + [
            str(_SCRIPT), "--home", str(self.home), "--config-dir", str(self.cfg), *args]
        return subprocess.run(
            cmd, input=stdin, env=self.env(mode), cwd=self.home,
            capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
            preexec_fn=(lambda: os.umask(umask)) if umask is not None else None,
        )

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(ln) for ln in self.log.read_text(encoding="utf-8").splitlines()]

    @property
    def env_file(self) -> Path:
        return self.home / ".config" / "metorite" / "box.env"

    def entries(self) -> dict:
        p = self.cfg / ".claude.json"
        return json.loads(p.read_text(encoding="utf-8")).get("mcpServers", {}) if p.exists() else {}


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def _sha(tok: str) -> str:
    import hashlib
    return hashlib.sha256(tok.encode()).hexdigest()


def test_success_never_prints_the_token(box: Box) -> None:
    tok = _token()
    run = box.run(tok + "\n")
    out = run.stdout + run.stderr
    assert run.returncode == 0, out
    _assert_no_token(out, tok)
    assert "'metorite' is connected" in out
    assert "set -a; . ~/.config/metorite/box.env; set +a" in out


def test_xtrace_never_prints_the_token(box: Box) -> None:
    tok = _token()
    run = box.run(tok + "\n", xtrace=True)
    assert run.returncode == 0, run.stderr
    _assert_no_token(run.stdout + run.stderr, tok)


def test_the_token_is_read_from_stdin_and_reaches_the_verify_env(box: Box) -> None:
    tok = _token()
    assert box.run(tok + "\r\n").returncode == 0  # a CRLF paste reads the same
    assert box.env_file.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={tok}\n"
    lists = [c for c in box.calls() if c["argv"][:2] == ["mcp", "list"]]
    assert len(lists) == 1
    assert lists[0]["env_token_sha"] == _sha(tok)


def test_box_env_is_0600_under_a_loose_umask(box: Box) -> None:
    tok = _token()
    assert box.run(tok + "\n", umask=0o000).returncode == 0
    assert stat.S_IMODE(box.env_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(box.env_file.parent.stat().st_mode) == 0o700
    leftovers = [p.name for p in box.env_file.parent.iterdir() if p.name != "box.env"]
    assert leftovers == []


def test_the_entry_holds_the_variable_and_argv_holds_no_token(box: Box) -> None:
    tok = _token()
    assert box.run(tok + "\n").returncode == 0
    for call in box.calls():
        _assert_no_token(json.dumps(call["argv"]), tok)
    adds = [c["argv"] for c in box.calls() if c["argv"][:2] == ["mcp", "add-json"]]
    assert adds == [["mcp", "add-json", "-s", "user", "metorite", adds[0][-1]]]
    assert json.loads(adds[0][-1]) == {
        "type": "http", "url": "https://mcp.metorite.com/mcp",
        "headers": {"Authorization": "Bearer ${METORITE_BOX_TOKEN}"},
    }
    assert tok not in (box.cfg / ".claude.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("bad", [
    lambda: _token()[:-1],                 # 42 characters
    lambda: _token() + "x",                # 44 characters
    lambda: _token()[:-1] + "!",           # a character outside the set
    lambda: "mbx_" + _token()[4:],         # the wrong prefix
])
def test_a_bad_shape_never_prints_the_token_and_writes_nothing(box: Box, bad) -> None:
    tok = bad()
    run = box.run(tok + "\n")
    out = run.stdout + run.stderr
    assert run.returncode == 3, out
    _assert_no_token(out, tok)
    assert not box.env_file.exists()
    assert box.calls() == []


def test_an_empty_stdin_is_refused(box: Box) -> None:
    run = box.run("")
    assert run.returncode == 3
    assert not box.env_file.exists()


def test_a_token_on_argv_is_refused_and_not_echoed(box: Box) -> None:
    tok = _token()
    run = box.run("", tok)
    out = run.stdout + run.stderr
    assert run.returncode == 2
    _assert_no_token(out, tok)
    assert not box.env_file.exists()


def test_a_failed_claude_never_prints_the_token(box: Box) -> None:
    tok = _token()
    run = box.run(tok + "\n", mode="fail-add")
    out = run.stdout + run.stderr
    assert run.returncode == 6, out
    _assert_no_token(out, tok)


def test_a_rejected_token_never_prints_the_token(box: Box) -> None:
    tok = _token()
    run = box.run(tok + "\n", mode="reject")
    out = run.stdout + run.stderr
    assert run.returncode == 7, out
    _assert_no_token(out, tok)
    assert "OAuth fallback is disabled" in out
    assert "rejected the token" in out


def test_the_list_output_is_masked_when_claude_prints_a_token(box: Box) -> None:
    tok = _token()
    run = box.run(tok + "\n", mode="echo")
    out = run.stdout + run.stderr
    assert run.returncode == 7
    _assert_no_token(out, tok)
    assert _log.BOX_TOKEN_MASK in out


def test_an_existing_entry_is_refused_without_replace(box: Box) -> None:
    old = _token()
    (box.cfg / ".claude.json").write_text(json.dumps({"mcpServers": {"metorite": {
        "type": "http", "url": "https://old.example/mcp",
        "headers": {"Authorization": f"Bearer {old}"}}}}), encoding="utf-8")
    tok = _token()
    run = box.run(tok + "\n")
    out = run.stdout + run.stderr
    assert run.returncode == 5, out
    assert "https://old.example/mcp" in out  # it says what it would replace
    _assert_no_token(out, old)  # and never the old header value
    _assert_no_token(out, tok)
    assert not box.env_file.exists()
    assert box.entries()["metorite"]["url"] == "https://old.example/mcp"
    assert box.calls() == []


def test_replace_swaps_the_existing_entry(box: Box) -> None:
    (box.cfg / ".claude.json").write_text(json.dumps({"mcpServers": {"metorite": {
        "type": "http", "url": "https://mcp.metorite.com/mcp"}}}), encoding="utf-8")
    tok = _token()
    run = box.run(tok + "\n", "--replace")
    out = run.stdout + run.stderr
    assert run.returncode == 0, out
    assert "no-authorization-header(oauth)" in out
    verbs = [c["argv"][:2] for c in box.calls()]
    assert verbs == [["mcp", "remove"], ["mcp", "add-json"], ["mcp", "list"]]
    assert box.entries()["metorite"]["headers"] == {"Authorization": "Bearer ${METORITE_BOX_TOKEN}"}


def test_no_verify_skips_the_list(box: Box) -> None:
    assert box.run(_token() + "\n", "--no-verify").returncode == 0
    assert [c["argv"][:2] for c in box.calls()] == [["mcp", "add-json"]]


def test_a_plain_http_url_to_another_host_is_refused(box: Box) -> None:
    run = box.run(_token() + "\n", "--url", "http://mcp.metorite.com/mcp")
    assert run.returncode == 2
    assert box.calls() == []


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")
def test_the_hidden_prompt_does_not_echo_the_token(box: Box) -> None:
    tok = _token()
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        ["bash", str(_SCRIPT), "--home", str(box.home), "--config-dir", str(box.cfg), "--no-verify"],
        stdin=slave, stdout=slave, stderr=slave, env=box.env(), cwd=box.home, close_fds=True,
    )
    os.close(slave)
    seen = b""
    deadline = time.monotonic() + 30

    def pump(until: bytes | None) -> None:
        nonlocal seen
        while time.monotonic() < deadline:
            if until is not None and until in seen:
                return
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    return
                if not chunk:
                    return
                seen += chunk
            elif until is None and proc.poll() is not None:
                return

    try:
        pump(b"does not show): ")
        time.sleep(0.3)  # let `read -s` turn the echo off
        os.write(master, tok.encode() + b"\n")
        pump(None)
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(master)
    text = seen.decode("utf-8", "replace")
    assert proc.returncode == 0, text
    _assert_no_token(text, tok)
    assert box.env_file.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={tok}\n"


def test_the_repo_scripts_hold_no_token_literal() -> None:
    pattern = re.compile(_PREFIX + r"[A-Za-z0-9_-]{43}")
    for path in [_SCRIPT, _ROOT / "docs" / "box_connect.md", _GITLEAKS_TOML,
                 _ROOT / "packages/acb_common/acb_common/_log.py"]:
        assert not pattern.search(path.read_text(encoding="utf-8")), path
