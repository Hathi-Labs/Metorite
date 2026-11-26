"""A box token never reaches a repo, a log or a terminal (EA-19e).

``external_agent_access.md`` §10.9 question 3. A box token is ``mbt_`` and 43
URL-safe characters. Three controls look for that shape:

- the ``metorite-box-token`` rule of ``.gitleaks.toml`` (pr-check runs it),
- the structlog processor ``redact_box_tokens`` and the stdlib filter
  ``BoxTokenRedactFilter`` in ``acb_common/_log.py``,
- ``scripts/box_connect.sh``, which reads the token from a hidden prompt or
  stdin and never prints it,
- ``scripts/metorite_box_headers.sh``, the ``headersHelper`` of the entry
  (task 1819, EA-19g). It prints the header from a 0600 ``box.env`` and
  refuses every other file without a word of the token.

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
6. A configure_logging that does not wrap the renderer
   (``BoxTokenMaskingRenderer`` removed): the ``*_rendered_*`` cases. An
   object whose ``repr`` holds a token reaches the output.
7. A verify step that ignores the scope of ``claude mcp get``:
   ``test_the_verify_step_needs_the_user_scope`` and
   ``test_an_approved_project_entry_stops_the_script``. They print Connected.
8. ``mv -f`` in place of ``mv -fT``:
   ``test_a_box_env_folder_is_refused_and_nothing_moves_into_it``.
9. A URL check that lets a control character through:
   ``test_a_url_with_a_control_character_is_refused``.
10. ``claude mcp remove`` before the JSON check of the entry:
    ``test_an_invalid_entry_stops_before_any_remove``.
11. A header helper with no mode check (the ``[ "$mode" = "600" ]`` line
    removed): ``test_the_helper_refuses_a_loose_mode``.
11b. A refusal that prints no header, or exits nonzero: the
    ``*_helper_refuses_*`` cases (``_assert_refused``), and
    ``test_a_refused_helper_starts_no_oauth`` in the client file.
11c. A verify step that trusts the exit code of the helper:
    ``test_a_helper_refusal_at_install_stops_the_verify``.
12. A helper that names the token in a refusal: the ``*_helper_refuses_*``
    cases.
13. An entry with a static ``Authorization`` header next to the helper:
    ``test_the_entry_names_the_helper_and_holds_no_header`` and
    ``test_a_static_header_in_the_entry_stops_the_verify``.
14. A helper path inside the checkout:
    ``test_the_helper_lives_outside_the_checkout_and_survives_its_removal``.
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
import termios
import time
import tomllib
from pathlib import Path

import pytest
import structlog
from acb_common import _log

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "box_connect.sh"
_HELPER = _ROOT / "scripts" / "metorite_box_headers.sh"
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


def test_the_redactor_masks_bytes_and_sets() -> None:
    tok = _token()
    event = {
        "event": "x",
        "raw": f"Bearer {tok}".encode(),
        "buf": bytearray(f"k={tok}".encode()),
        "tags": {tok, "plain"},
        "frozen": frozenset({("pair", tok)}),
    }
    out = _log.redact_box_tokens(None, "info", dict(event))
    assert out["raw"] == f"Bearer {_log.BOX_TOKEN_MASK}".encode()
    assert isinstance(out["buf"], bytearray)
    assert tok.encode() not in bytes(out["buf"])
    assert out["tags"] == {_log.BOX_TOKEN_MASK, "plain"}
    assert isinstance(out["frozen"], frozenset)
    assert out["frozen"] == frozenset({("pair", _log.BOX_TOKEN_MASK)})
    # The check decodes only to search, so a partial print shows too.
    _assert_no_token(repr(out), tok)


class _HeadersLike:
    """Like a starlette ``Headers``: not a dict, and its repr holds the value."""

    def __init__(self, auth: str) -> None:
        self._raw = [(b"authorization", auth.encode())]

    def __repr__(self) -> str:
        return f"Headers(raw={self._raw!r})"

    __str__ = __repr__


@pytest.mark.parametrize("json_logs", [True, False])
def test_the_rendered_output_is_masked_for_an_unknown_object(json_logs: bool) -> None:
    tok = _token()
    renderer = (structlog.processors.JSONRenderer() if json_logs
                else structlog.dev.ConsoleRenderer(colors=False))
    event = _log.redact_box_tokens(None, "info", {
        "event": "req", "headers": _HeadersLike(f"Bearer {tok}")})
    # The dict walk does not know the type, so the plain renderer leaks.
    assert tok in renderer(None, "info", dict(event))
    out = _log.BoxTokenMaskingRenderer(renderer)(None, "info", dict(event))
    _assert_no_token(out, tok)
    assert _log.BOX_TOKEN_MASK in out


def test_the_rendered_bytes_output_is_masked() -> None:
    tok = _token()
    out = _log.BoxTokenMaskingRenderer(lambda *_a: f"line {tok}".encode())(None, "info", {})
    assert out == f"line {_log.BOX_TOKEN_MASK}".encode()


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


def test_configure_logging_masks_the_rendered_line_and_places_the_filter(capsys) -> None:
    saved = structlog.get_config()
    root = logging.getLogger()
    before = {id(h): list(h.filters) for h in root.handlers}
    tok = _token()
    try:
        _log.configure_logging("INFO", json_logs=True)
        procs = structlog.get_config()["processors"]
        assert procs.index(_log.redact_box_tokens) == len(procs) - 2
        assert isinstance(procs[-1], _log.BoxTokenMaskingRenderer)
        assert procs.index(structlog.processors.format_exc_info) < procs.index(
            _log.redact_box_tokens)
        for h in root.handlers:
            assert any(isinstance(f, _log.BoxTokenRedactFilter) for f in h.filters)
        structlog.get_logger("box").info(
            f"hello {tok}", nested={"k": [tok]}, headers=_HeadersLike(tok))
        out = capsys.readouterr().out
        assert "hello" in out
        assert "Headers(raw=" in out
        _assert_no_token(out, tok)
    finally:
        structlog.configure(**saved)
        for h in root.handlers:
            h.filters[:] = before.get(id(h), [
                f for f in h.filters if not isinstance(f, _log.BoxTokenRedactFilter)])


# ── 3. scripts/box_connect.sh ───────────────────────────────────────────────

# A fake `claude`. It records each call as one JSON line: the argv, the
# folder, and a sha256 of METORITE_BOX_TOKEN when it is set (never the value).
# It keeps the user-scope entries in $CLAUDE_CONFIG_DIR/.claude.json, and the
# local-scope entries under `projects[<folder>]`, like the real one.
# `mcp get` picks the entry as Claude Code 2.1.319 does: a local entry, then
# an approved `.mcp.json` entry, then the user entry. FAKE_CLAUDE_GET_SCOPE
# forces the scope that `mcp get` prints.
_FAKE_CLAUDE = r'''#!/usr/bin/env python3
import hashlib, json, os, sys
args = sys.argv[1:]
tok = os.environ.get("METORITE_BOX_TOKEN")
rec = {"argv": args, "cwd": os.getcwd(),
       "env_token_sha": hashlib.sha256(tok.encode()).hexdigest() if tok else None}
with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(rec) + "\n")
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.environ["HOME"]
cfg = os.path.join(cfg_dir, ".claude.json")
def load(path=cfg):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
def save(d):
    with open(cfg, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
def scope_of(argv):
    return argv[argv.index("-s") + 1] if "-s" in argv else "local"
if args[:2] == ["mcp", "add-json"]:
    if mode == "fail-add":
        print("boom", file=sys.stderr); sys.exit(1)
    name, body = args[-2], args[-1]
    entry = json.loads(body)
    if mode == "static-header":
        entry["headers"] = {"Authorization": "Bearer ${METORITE_BOX_TOKEN}"}
    if mode == "loosen-env":
        os.chmod(os.path.join(os.environ["HOME"], ".config", "metorite", "box.env"), 0o644)
    d = load(); servers = d.setdefault("mcpServers", {})
    if name in servers:
        print(f"MCP server {name} already exists in user config"); sys.exit(1)
    servers[name] = entry; save(d)
    print(f"Added http MCP server {name} to user config"); sys.exit(0)
if args[:2] == ["mcp", "remove"]:
    d = load()
    if scope_of(args) == "local":
        d.get("projects", {}).get(os.getcwd(), {}).get("mcpServers", {}).pop(args[-1], None)
    else:
        d.get("mcpServers", {}).pop(args[-1], None)
    save(d); sys.exit(0)
if args[:2] == ["mcp", "get"]:
    name = args[2]; d = load(); cwd = os.getcwd()
    local = d.get("projects", {}).get(cwd, {}).get("mcpServers", {}).get(name)
    project = load(os.path.join(cwd, ".mcp.json")).get("mcpServers", {}).get(name)
    approved = name in load(os.path.join(cfg_dir, "settings.json")).get("enabledMcpjsonServers", [])
    user = d.get("mcpServers", {}).get(name)
    scopes = {"Local": "Local config (private to you in this project)",
              "Project": "Project config (shared via .mcp.json)",
              "User": "User config (available in all your projects)"}
    if local:
        scope, entry = "Local", local
    elif project and approved:
        scope, entry = "Project", project
    elif user:
        scope, entry = "User", user
    else:
        print(f'No MCP server named "{name}" found.'); sys.exit(1)
    scope = os.environ.get("FAKE_CLAUDE_GET_SCOPE", scope)
    print(f"{name}:")
    print(f"  Scope: {scopes[scope]}")
    print("  Status: ✔ Connected")
    print(f"  Type: {entry.get('type')}")
    print(f"  URL: {entry.get('url')}")
    if entry.get("headers"):
        print("  Headers:")
        for k, v in entry["headers"].items():
            print(f"    {k}: {v}")
    sys.exit(0)
if args[:2] == ["mcp", "list"]:
    url = load().get("mcpServers", {}).get("metorite", {}).get("url", "?")
    print("Checking MCP server health...\n")
    if mode == "reject":
        print(f"metorite: {url} (HTTP) - ✘ Failed to connect — Server rejected the configured "
              "Authorization header (HTTP 401). OAuth fallback is disabled when headers.Authorization is set")
    elif mode == "echo":
        # Like a client that prints the header that the helper gave it.
        with open(os.path.join(os.environ["HOME"], ".config", "metorite", "box.env"),
                  encoding="utf-8") as fh:
            minted = fh.read().strip().split("=", 1)[1]
        print(f"metorite: {url} (HTTP) - ✘ Failed to connect — debug {minted}")
    else:
        print(f"metorite: {url} (HTTP) - ✔ Connected")
    sys.exit(0)
sys.exit(0)
'''

# A python3 shim for one case. It fails the JSON check of the entry, and it
# passes every other call to the real interpreter.
_FAKE_PYTHON = '''#!/bin/sh
case "$3" in
  *"json.loads(sys.argv[1])"*) exit 1 ;;
esac
exec "{python}" "$@"
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

    def env(self, mode: str = "ok", **extra: str) -> dict[str, str]:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.home),
            "LANG": "C.UTF-8",
            "FAKE_CLAUDE_LOG": str(self.log),
            "FAKE_CLAUDE_MODE": mode,
        }
        env.update(extra)
        return env

    def run(self, stdin: str | None, *args: str, mode: str = "ok", xtrace: bool = False,
            umask: int | None = None, cwd: Path | None = None,
            **extra: str) -> subprocess.CompletedProcess:
        cmd = ["bash"] + (["-x"] if xtrace else []) + [
            str(_SCRIPT), "--home", str(self.home), "--config-dir", str(self.cfg), *args]
        return subprocess.run(
            cmd, input=stdin, env=self.env(mode, **extra), cwd=cwd or self.home,
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

    @property
    def helper(self) -> Path:
        return self.home / ".local" / "bin" / "metorite-box-headers"

    @property
    def helper_cmd(self) -> str:
        return f"{self.helper} {self.env_file}"

    def config(self) -> dict:
        p = self.cfg / ".claude.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def entries(self) -> dict:
        return self.config().get("mcpServers", {})

    def project(self, *, approved: bool) -> Path:
        """A checkout like this repo: `.mcp.json` names `metorite` with no header."""
        proj = self.home.parent / "checkout"
        proj.mkdir(exist_ok=True)
        (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"metorite": {
            "type": "http", "url": "https://mcp.metorite.com/mcp"}}}), encoding="utf-8")
        settings = {"enabledMcpjsonServers": ["metorite"]} if approved else {}
        (self.cfg / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        return proj


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
    assert "a lane does not load box.env" in out
    assert "set -a" not in out


def test_xtrace_never_prints_the_token(box: Box) -> None:
    tok = _token()
    run = box.run(tok + "\n", xtrace=True)
    assert run.returncode == 0, run.stderr
    _assert_no_token(run.stdout + run.stderr, tok)


def test_the_token_is_read_from_stdin_and_stays_out_of_the_verify_env(box: Box) -> None:
    tok = _token()
    # A CRLF paste reads the same. The caller's own METORITE_BOX_TOKEN does
    # not reach `claude`: the verify proves that a lane needs no env var.
    run = box.run(tok + "\r\n", METORITE_BOX_TOKEN=_token())
    assert run.returncode == 0, run.stdout + run.stderr
    assert box.env_file.read_text(encoding="utf-8") == f"METORITE_BOX_TOKEN={tok}\n"
    lists = [c for c in box.calls() if c["argv"][:2] == ["mcp", "list"]]
    assert len(lists) == 1
    assert all(c["env_token_sha"] is None for c in box.calls())


def test_box_env_is_0600_under_a_loose_umask(box: Box) -> None:
    tok = _token()
    assert box.run(tok + "\n", umask=0o000).returncode == 0
    assert stat.S_IMODE(box.env_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(box.env_file.parent.stat().st_mode) == 0o700
    leftovers = [p.name for p in box.env_file.parent.iterdir() if p.name != "box.env"]
    assert leftovers == []


def test_the_entry_names_the_helper_and_holds_no_header(box: Box) -> None:
    tok = _token()
    assert box.run(tok + "\n").returncode == 0
    for call in box.calls():
        _assert_no_token(json.dumps(call["argv"]), tok)
    adds = [c["argv"] for c in box.calls() if c["argv"][:2] == ["mcp", "add-json"]]
    assert adds == [["mcp", "add-json", "-s", "user", "metorite", adds[0][-1]]]
    assert json.loads(adds[0][-1]) == {
        "type": "http", "url": "https://mcp.metorite.com/mcp",
        "headersHelper": box.helper_cmd,
    }
    stored = (box.cfg / ".claude.json").read_text(encoding="utf-8")
    _assert_no_token(stored, tok)
    assert "Authorization" not in stored
    assert "headers" not in json.loads(stored)["mcpServers"]["metorite"]


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
    assert verbs == [["mcp", "remove"], ["mcp", "add-json"], ["mcp", "get"], ["mcp", "list"]]
    assert box.entries()["metorite"]["headersHelper"] == box.helper_cmd
    assert "headers" not in box.entries()["metorite"]


def test_no_verify_skips_the_list(box: Box) -> None:
    assert box.run(_token() + "\n", "--no-verify").returncode == 0
    assert [c["argv"][:2] for c in box.calls()] == [["mcp", "add-json"]]


def test_a_plain_http_url_to_another_host_is_refused(box: Box) -> None:
    run = box.run(_token() + "\n", "--url", "http://mcp.metorite.com/mcp")
    assert run.returncode == 2
    assert box.calls() == []


# ── 3b. The effective entry: user scope, or exit 7 ─────────────────────────

@pytest.mark.parametrize(("scope", "rc"), [("User", 0), ("Project", 7), ("Local", 7)])
def test_the_verify_step_needs_the_user_scope(box: Box, scope: str, rc: int) -> None:
    tok = _token()
    run = box.run(tok + "\n", FAKE_CLAUDE_GET_SCOPE=scope)
    out = run.stdout + run.stderr
    assert run.returncode == rc, out
    _assert_no_token(out, tok)
    if rc == 0:
        assert "'metorite' is connected" in out
        return
    # The fake `mcp get` says Connected. The script must never pass it on.
    assert "Connected" not in out and "is connected" not in out and " OK" not in out
    assert f"Scope: {scope} config" in out
    assert "The token does nothing here" in out
    if scope == "Project":
        assert "disabledMcpjsonServers" in out
    else:
        assert "claude mcp remove -s local metorite" in out
    assert [c["argv"][:2] for c in box.calls()][-1] == ["mcp", "get"]  # no list


def test_an_approved_project_entry_stops_the_script(box: Box) -> None:
    proj = box.project(approved=True)
    tok = _token()
    run = box.run(tok + "\n", cwd=proj)  # the default --project is the checkout
    out = run.stdout + run.stderr
    assert run.returncode == 7, out
    assert f"project folder: {proj}" in out
    assert "Scope: Project config" in out
    assert "An owner must close the project route" in out
    assert "Connected" not in out and "is connected" not in out
    gets = [c for c in box.calls() if c["argv"][:2] == ["mcp", "get"]]
    assert gets and gets[0]["cwd"] == str(proj)
    # The script edits no settings and no .mcp.json.
    assert json.loads((box.cfg / "settings.json").read_text(encoding="utf-8")) == {
        "enabledMcpjsonServers": ["metorite"]}
    assert "headers" not in json.loads(
        (proj / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["metorite"]


def test_a_project_entry_with_no_approval_lets_the_user_entry_win(box: Box) -> None:
    proj = box.project(approved=False)
    run = box.run(_token() + "\n", "--project", str(proj))
    assert run.returncode == 0, run.stdout + run.stderr
    assert all(c["cwd"] == str(proj) for c in box.calls()
               if c["argv"][:2] in (["mcp", "get"], ["mcp", "list"]))


def test_no_mcp_json_means_the_verify_runs_from_home(box: Box) -> None:
    assert box.run(_token() + "\n").returncode == 0
    gets = [c for c in box.calls() if c["argv"][:2] == ["mcp", "get"]]
    assert gets[0]["cwd"] == str(box.home)


def test_a_missing_project_folder_is_refused(box: Box) -> None:
    run = box.run(_token() + "\n", "--project", str(box.home / "nope"))
    assert run.returncode == 2
    assert box.calls() == []


def _local_entry(box: Box, folder: Path, entry: dict) -> None:
    conf = box.config()
    conf.setdefault("projects", {})[str(folder)] = {"mcpServers": {"metorite": entry}}
    (box.cfg / ".claude.json").write_text(json.dumps(conf), encoding="utf-8")


def test_a_local_entry_is_refused_without_replace(box: Box) -> None:
    proj = box.project(approved=False)
    old = _token()
    _local_entry(box, proj, {"type": "http", "url": f"https://old.example/{old}/mcp",
                             "headers": {"Authorization": f"Bearer {old}"}})
    tok = _token()
    run = box.run(tok + "\n", "--project", str(proj))
    out = run.stdout + run.stderr
    assert run.returncode == 5, out
    assert "a local-scope 'metorite' entry exists" in out
    assert "https://old.example/mbt_[redacted]/mcp" in out
    assert "another-authorization-header(value-not-shown)" in out
    _assert_no_token(out, old)
    _assert_no_token(out, tok)
    assert not box.env_file.exists()
    assert box.calls() == []


def test_replace_removes_the_local_entry_and_the_user_entry_wins(box: Box) -> None:
    proj = box.project(approved=False)
    _local_entry(box, proj, {"type": "http", "url": "https://mcp.metorite.com/mcp"})
    run = box.run(_token() + "\n", "--project", str(proj), "--replace")
    out = run.stdout + run.stderr
    assert run.returncode == 0, out
    removes = [c for c in box.calls() if c["argv"][:2] == ["mcp", "remove"]]
    assert [c["argv"] for c in removes] == [["mcp", "remove", "-s", "local", "metorite"]]
    assert removes[0]["cwd"] == str(proj)
    assert box.config()["projects"][str(proj)]["mcpServers"] == {}


def test_the_old_url_is_masked_and_never_shows_a_header_value(box: Box) -> None:
    old = _token()
    (box.cfg / ".claude.json").write_text(json.dumps({"mcpServers": {"metorite": {
        "type": "http", "url": f"https://old.example/mcp?k={old}\u001b[2J",
        "headers": {"Authorization": f"Bearer {old}"}}}}), encoding="utf-8")
    run = box.run(_token() + "\n")
    out = run.stdout + run.stderr
    assert run.returncode == 5, out
    assert "https://old.example/mcp?k=mbt_[redacted][2J" in out
    assert "\x1b" not in out
    _assert_no_token(out, old)


def test_a_failed_add_after_remove_names_the_old_entry(box: Box) -> None:
    old = _token()
    (box.cfg / ".claude.json").write_text(json.dumps({"mcpServers": {"metorite": {
        "type": "http", "url": "https://old.example/mcp",
        "headers": {"Authorization": f"Bearer {old}"}}}}), encoding="utf-8")
    run = box.run(_token() + "\n", "--replace", mode="fail-add")
    out = run.stdout + run.stderr
    assert run.returncode == 6, out
    assert ("the old user-scope entry was: http https://old.example/mcp "
            "another-authorization-header(value-not-shown)") in out
    _assert_no_token(out, old)


def test_an_invalid_entry_stops_before_any_remove(box: Box) -> None:
    (box.cfg / ".claude.json").write_text(json.dumps({"mcpServers": {"metorite": {
        "type": "http", "url": "https://old.example/mcp"}}}), encoding="utf-8")
    shim = box.bin / "python3"
    shim.write_text(_FAKE_PYTHON.replace("{python}", sys.executable), encoding="utf-8")
    shim.chmod(0o755)
    run = box.run(_token() + "\n", "--replace")
    out = run.stdout + run.stderr
    assert run.returncode == 2, out
    assert "not valid JSON" in out
    assert box.calls() == []
    assert not box.env_file.exists()
    assert box.entries()["metorite"]["url"] == "https://old.example/mcp"


@pytest.mark.parametrize("bad", [
    "https://mcp.metorite.com/mcp\x01",
    "https://mcp.metorite.com/\x1b[2Jmcp",
    "https://mcp.metorite.com/mcp\x7f",
    "https://mcp.metorite.com/m\u00e9cp",
    "https://mcp.metorite.com/mcp\t",
    'https://mcp.metorite.com/mcp"',
    "https://mcp.metorite.com/mcp\\",
])
def test_a_url_with_a_control_character_is_refused(box: Box, bad: str) -> None:
    (box.cfg / ".claude.json").write_text(json.dumps({"mcpServers": {"metorite": {
        "type": "http", "url": "https://old.example/mcp"}}}), encoding="utf-8")
    run = box.run(_token() + "\n", "--replace", "--url", bad)
    out = run.stdout + run.stderr
    assert run.returncode == 2, out
    assert "--url must be printable ASCII" in out
    assert box.calls() == []
    assert box.entries()["metorite"]["url"] == "https://old.example/mcp"


def test_a_box_env_folder_is_refused_and_nothing_moves_into_it(box: Box) -> None:
    box.env_file.mkdir(parents=True)
    run = box.run(_token() + "\n")
    out = run.stdout + run.stderr
    assert run.returncode == 6, out
    assert list(box.env_file.iterdir()) == []
    leftovers = [p.name for p in box.env_file.parent.iterdir() if p.name != "box.env"]
    assert leftovers == []
    assert [c for c in box.calls() if c["argv"][:2] == ["mcp", "add-json"]] == []


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

    def echo_is_off() -> bool:
        # Linux: the master reads the termios of the pty, which `read -s` sets.
        return not (termios.tcgetattr(master)[3] & termios.ECHO)

    try:
        pump(b"does not show): ")
        off_by = time.monotonic() + 10
        while not echo_is_off():
            assert time.monotonic() < off_by, "read -s did not turn the echo off in 10 s"
            time.sleep(0.02)
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
    for path in [_SCRIPT, _HELPER, _ROOT / "docs" / "box_connect.md", _GITLEAKS_TOML,
                 _ROOT / "packages/acb_common/acb_common/_log.py"]:
        assert not pattern.search(path.read_text(encoding="utf-8")), path


# ── 4. The header helper: scripts/metorite_box_headers.sh (EA-19g) ─────────

def _helper(box_env: Path | None, home: Path, *, helper: Path = _HELPER,
            **extra: str) -> subprocess.CompletedProcess:
    args = [str(helper)] + ([str(box_env)] if box_env is not None else [])
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8", **extra}
    return subprocess.run(args, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=30, check=False)


def _token_file(folder: Path, line: str, mode: int = 0o600) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "box.env"
    path.write_text(line, encoding="utf-8")
    path.chmod(mode)
    return path


# A refusal keeps an Authorization header: with none, Claude Code starts OAuth
# discovery and Dynamic Client Registration (review of 2026-11-26).
_REFUSED = {"Authorization": "Bearer box-token-helper-refused"}


def _assert_refused(run: subprocess.CompletedProcess, tok: str | None, reason: str) -> None:
    assert run.returncode == 0, run.stderr  # a nonzero exit makes the client ignore stdout
    assert json.loads(run.stdout) == _REFUSED
    assert run.stdout.count("{") == 1
    assert reason in run.stderr, run.stderr
    assert len(run.stderr.splitlines()) == 1, run.stderr
    if tok:
        _assert_no_token(run.stderr, tok)


def test_the_helper_prints_the_header_for_a_good_file(tmp_path: Path) -> None:
    tok = _token()
    path = _token_file(tmp_path / "cfg", f"METORITE_BOX_TOKEN={tok}\n")
    run = _helper(path, tmp_path)
    assert run.returncode == 0, run.stderr
    assert run.stderr == ""
    assert json.loads(run.stdout) == {"Authorization": f"Bearer {tok}"}
    assert run.stdout.count("{") == 1  # exactly one JSON object


def test_the_helper_reads_the_default_path_under_home(tmp_path: Path) -> None:
    tok = _token()
    _token_file(tmp_path / ".config" / "metorite", f"METORITE_BOX_TOKEN={tok}\r\n")
    run = _helper(None, tmp_path)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {"Authorization": f"Bearer {tok}"}


def test_the_helper_takes_no_path_from_the_environment(tmp_path: Path) -> None:
    """A project settings file can set an env var. No env var moves the path."""
    other = _token_file(tmp_path / "other", f"METORITE_BOX_TOKEN={_token()}\n")
    run = _helper(None, tmp_path, METORITE_BOX_ENV=str(other))
    _assert_refused(run, None, "is missing")


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o700, 0o400])
def test_the_helper_refuses_a_loose_mode(tmp_path: Path, mode: int) -> None:
    tok = _token()
    path = _token_file(tmp_path / "cfg", f"METORITE_BOX_TOKEN={tok}\n", mode)
    _assert_refused(_helper(path, tmp_path), tok, "must have mode 0600")


def test_the_helper_refuses_a_missing_file(tmp_path: Path) -> None:
    _assert_refused(_helper(tmp_path / "nope" / "box.env", tmp_path), None, "is missing")


def test_the_helper_refuses_a_symlink_and_a_folder(tmp_path: Path) -> None:
    tok = _token()
    real = _token_file(tmp_path / "cfg", f"METORITE_BOX_TOKEN={tok}\n")
    link = tmp_path / "link.env"
    link.symlink_to(real)
    _assert_refused(_helper(link, tmp_path), tok, "is a symlink")
    folder = tmp_path / "folder.env"
    folder.mkdir()
    _assert_refused(_helper(folder, tmp_path), None, "not a regular file")


@pytest.mark.parametrize("bad", [
    lambda: _token()[:-1],                 # 42 characters
    lambda: _token() + "x",                # 44 characters
    lambda: _token()[:-1] + '"',           # a quote would break the JSON
    lambda: "mbx_" + _token()[4:],         # the wrong prefix
    lambda: " " + _token(),                # a leading space
])
def test_the_helper_refuses_a_bad_shape_without_a_word_of_it(tmp_path: Path, bad) -> None:
    tok = bad()
    path = _token_file(tmp_path / "cfg", f"METORITE_BOX_TOKEN={tok}\n")
    _assert_refused(_helper(path, tmp_path), tok, "does not have the shape")


def test_the_helper_refuses_a_file_with_no_token_line(tmp_path: Path) -> None:
    tok = _token()
    path = _token_file(tmp_path / "cfg", f"OTHER={tok}\n")
    _assert_refused(_helper(path, tmp_path), tok, "holds no METORITE_BOX_TOKEN line")


def test_the_helper_never_runs_the_file(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    path = _token_file(tmp_path / "cfg", f"touch {marker}\nMETORITE_BOX_TOKEN=$(touch {marker})\n")
    _assert_refused(_helper(path, tmp_path), None, "does not have the shape")
    assert not marker.exists()


def test_the_helper_xtrace_never_prints_the_token(tmp_path: Path) -> None:
    tok = _token()
    path = _token_file(tmp_path / "cfg", f"METORITE_BOX_TOKEN={tok}\n")
    run = subprocess.run(["bash", "-x", str(_HELPER), str(path)],
                         env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
                         capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
    assert run.returncode == 0
    _assert_no_token(run.stderr, tok)


def test_the_token_shape_has_one_definition() -> None:
    helper = _HELPER.read_text(encoding="utf-8")
    script = _SCRIPT.read_text(encoding="utf-8")
    assert helper.count("TOKEN_RE='^mbt_[A-Za-z0-9_-]{43}$'") == 1
    assert "TOKEN_RE='" not in script
    assert '. "$helper_src"' in script


def test_the_helper_lives_outside_the_checkout_and_survives_its_removal(box: Box, tmp_path: Path) -> None:
    """A worktree removal must not break the lanes."""
    checkout = tmp_path / "worktree"
    (checkout / "scripts").mkdir(parents=True)
    for src in (_SCRIPT, _HELPER):
        shutil.copy2(src, checkout / "scripts" / src.name)
    tok = _token()
    run = subprocess.run(
        ["bash", str(checkout / "scripts" / "box_connect.sh"), "--home", str(box.home),
         "--config-dir", str(box.cfg)],
        input=tok + "\n", env=box.env(), cwd=box.home, capture_output=True, text=True,
        encoding="utf-8", timeout=60, check=False)
    assert run.returncode == 0, run.stdout + run.stderr
    command = box.entries()["metorite"]["headersHelper"]
    helper_path = Path(command.split()[0])
    assert helper_path == box.helper
    assert _ROOT not in helper_path.parents and checkout not in helper_path.parents
    assert stat.S_IMODE(helper_path.stat().st_mode) == 0o700
    assert helper_path.read_bytes() == _HELPER.read_bytes()
    shutil.rmtree(checkout)
    again = subprocess.run(command, shell=True, env={"PATH": "/usr/bin:/bin"},
                           capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
    assert again.returncode == 0, again.stderr
    assert json.loads(again.stdout) == {"Authorization": f"Bearer {tok}"}


def test_a_static_header_in_the_entry_stops_the_verify(box: Box) -> None:
    tok = _token()
    run = box.run(tok + "\n", mode="static-header")
    out = run.stdout + run.stderr
    assert run.returncode == 7, out
    assert "holds a static header" in out
    _assert_no_token(out, tok)
    assert [c["argv"][:2] for c in box.calls()][-1] == ["mcp", "get"]  # no list


def test_a_home_that_needs_quoting_is_refused(tmp_path: Path) -> None:
    b = Box(tmp_path)
    odd = tmp_path / "a b;c"
    odd.mkdir()
    run = subprocess.run(
        ["bash", str(_SCRIPT), "--home", str(odd), "--config-dir", str(b.cfg)],
        input=_token() + "\n", env=b.env(), cwd=tmp_path, capture_output=True, text=True,
        encoding="utf-8", timeout=60, check=False)
    assert run.returncode == 2, run.stdout + run.stderr
    assert b.calls() == []


def test_a_helper_refusal_at_install_stops_the_verify(box: Box) -> None:
    """A refusal exits 0, so the verify step must read the sentinel."""
    tok = _token()
    run = box.run(tok + "\n", mode="loosen-env")
    out = run.stdout + run.stderr
    assert run.returncode == 7, out
    assert "refused" in out and "must have mode 0600" in out
    assert "box-token-helper-refused" not in out  # the helper stdout is not printed
    _assert_no_token(out, tok)
    assert [c["argv"][:2] for c in box.calls()][-1] == ["mcp", "get"]  # no list


def test_help_works_from_a_partial_copy(tmp_path: Path) -> None:
    lone = tmp_path / "box_connect.sh"
    shutil.copy2(_SCRIPT, lone)
    run = subprocess.run(["bash", str(lone), "--help"], capture_output=True, text=True,
                         encoding="utf-8", timeout=30, check=False)
    assert run.returncode == 0, run.stderr
    assert "--no-verify" in run.stdout


def test_the_helper_refuses_a_hard_link(tmp_path: Path) -> None:
    tok = _token()
    path = _token_file(tmp_path / "cfg", f"METORITE_BOX_TOKEN={tok}\n")
    os.link(path, tmp_path / "second-name")
    _assert_refused(_helper(path, tmp_path), tok, "has 2 hard links")


def test_the_helper_refuses_a_fifo_without_a_wait(tmp_path: Path) -> None:
    fifo = tmp_path / "box.env"
    os.mkfifo(fifo, 0o600)
    t0 = time.monotonic()
    _assert_refused(_helper(fifo, tmp_path), None, "not a regular file")
    assert time.monotonic() - t0 < 10  # inside the 10 s limit of the client


def test_the_helper_checks_and_reads_one_open_descriptor() -> None:
    """A name swap after the checks must not change what is read."""
    body = _HELPER.read_text(encoding="utf-8")
    opened = body.index('{ exec {fd}<"$box_env"; }')
    after = body[opened:]
    assert '"/dev/fd/$fd"' in after and 'done <&"$fd"' in after
    assert '<"$box_env"' not in after.replace('{ exec {fd}<"$box_env"; }', "")
    assert "stat -c" not in body.replace('stat -L -c', "")
