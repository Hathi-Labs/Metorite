"""The sign-in alarm (MCP-G17g, task 4033). Hermetic: fake runner, fake gh.

Fences: each state parses, the first DOWN notifies once, a repeat does not,
an error neither notifies nor resets, recovery writes "back" and resets, the
gh channel refuses with no user, and the timer runs every 10 minutes.
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

import pytest

_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts/home_server"

OK = "metorite: https://mcp.metorite.com/mcp (HTTP) - ✔ Connected\n"
AUTH = "supabase: x - ! Needs authentication\nmetorite: https://mcp.metorite.com/mcp (HTTP) - ! Needs authentication\n"
NONE = "codegraph: codegraph serve --mcp - ✔ Connected\n"


@pytest.fixture()
def al(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("mcp_signin_alarm", _DIR / "mcp_signin_alarm.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mcp_signin_alarm"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(mod, "NOTE_LOG", tmp_path / "q.log")
    monkeypatch.setenv("METORITE_ALARM_HOST", "box1")
    monkeypatch.delenv("METORITE_ALARM_CHANNEL", raising=False)
    monkeypatch.delenv("METORITE_ALARM_GH_USER", raising=False)
    return mod


def notes(al):
    p = al.NOTE_LOG
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def test_classify(al):
    assert al.classify(OK) == "ok"
    assert al.classify(AUTH) == "auth"
    assert al.classify(NONE) == "missing"
    assert al.classify("boom", 1) == "error"
    assert al.classify("metorite: x - ✘ Failed to connect") == "error"


def test_timeout_is_error(al):
    def boom(cwd):
        return None
    assert al.check(pathlib.Path("."), boom) == "error"


def test_first_down_notes_once_repeat_silent(al):
    al.step("auth")
    al.step("auth")
    al.step("missing")
    n = notes(al)
    assert len(n) == 1
    assert n[0].startswith("NOTE\tsignin-alarm\t") and "box1: metorite auth since" in n[0]
    assert "docs/home_box.md" in n[0]


def test_error_neither_notifies_nor_resets(al):
    al.step("auth")
    al.step("error")
    assert al.load_state()["state"] == "auth"
    al.step("auth")
    assert len(notes(al)) == 1
    al.step("ok")
    al.step("error")
    assert al.load_state()["state"] == "ok"
    assert len(notes(al)) == 2


def test_recovery_writes_back_and_resets(al):
    al.step("auth")
    al.step("ok")
    assert "back" in notes(al)[-1]
    assert al.load_state()["state"] == "ok"
    al.step("auth")
    assert len(notes(al)) == 3


def test_gh_channel_refuses_without_user(al, monkeypatch):
    monkeypatch.setenv("METORITE_ALARM_CHANNEL", "gh")
    calls = []
    al.step("auth", gh=lambda a: calls.append(a))
    assert calls == []


def test_gh_channel_opens_and_closes(al, monkeypatch):
    monkeypatch.setenv("METORITE_ALARM_CHANNEL", "gh")
    monkeypatch.setenv("METORITE_ALARM_GH_USER", "@owner")
    calls = []

    def gh(a):
        calls.append(a)
        return subprocess.CompletedProcess(a, 0, "https://github.com/o/r/issues/42\n", "")
    al.step("auth", gh=gh)
    al.step("auth", gh=gh)
    assert len(calls) == 1 and calls[0][:2] == ["issue", "create"]
    assert "Metorite sign-in down on box1" in calls[0]
    assert "@owner" in calls[0][-1]
    al.step("ok", gh=gh)
    assert [c[1] for c in calls[1:]] == ["comment", "close"] and calls[1][2] == "42"


def test_units():
    timer = (_DIR / "metorite-signin-alarm.timer").read_text(encoding="utf-8")
    service = (_DIR / "metorite-signin-alarm.service").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=10min" in timer and "OnBootSec=3min" in timer
    assert "EnvironmentFile=-%h/.config/metorite/alarm.env" in service
    sh = (_DIR / "mcp_signin_alarm.sh").read_text(encoding="utf-8")
    assert "install" in sh and "enable" in sh


@pytest.mark.parametrize("raw", ["[]", "null", "123", '"x"', "{bad"])
def test_damaged_state_reads_unknown(al, raw):
    al.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    al.STATE_PATH.write_text(raw, encoding="utf-8")
    assert al.load_state()["state"] == "unknown"


def _creds(al, tmp_path, monkeypatch, entry):
    import json as _j
    p = tmp_path / "creds.json"
    p.write_text(_j.dumps({"mcpOAuth": {"metorite|x": entry}}), encoding="utf-8")
    monkeypatch.setattr(al, "CREDS_PATH", p)


def test_gate_never_lets_the_check_refresh(al, tmp_path, monkeypatch):
    now = 1_800_000_000.0
    base = {"serverName": "metorite", "refreshToken": "r", "accessToken": "a"}
    _creds(al, tmp_path, monkeypatch, {**base, "expiresAt": (now + 3000) * 1000})
    assert al.token_gate(now) == "check"
    _creds(al, tmp_path, monkeypatch, {**base, "expiresAt": (now + 100) * 1000})
    assert al.token_gate(now) == "skip"
    _creds(al, tmp_path, monkeypatch, {**base, "expiresAt": (now - 600) * 1000})
    assert al.token_gate(now) == "skip"
    _creds(al, tmp_path, monkeypatch, {**base, "expiresAt": (now - 3 * 3600) * 1000})
    assert al.token_gate(now) == "stale"
    _creds(al, tmp_path, monkeypatch, {"serverName": "metorite", "accessToken": "a"})
    assert al.token_gate(now) == "check"  # no refresh token: a box token, or none
    monkeypatch.setattr(al, "CREDS_PATH", tmp_path / "absent.json")
    assert al.token_gate(now) == "check"


def test_stale_is_down(al):
    assert "stale" in al.DOWN


def test_failed_gh_send_writes_one_note(al, monkeypatch):
    import subprocess as sp
    monkeypatch.setenv("METORITE_ALARM_CHANNEL", "gh")
    monkeypatch.setenv("METORITE_ALARM_GH_USER", "owner")

    def failing(args):
        return sp.CompletedProcess(args, 1, "", "not a git repository")

    al.step("auth", failing)
    al.step("auth", failing)
    assert al.NOTE_LOG.read_text(encoding="utf-8").count("\n") == 1
    assert al.load_state()["notified"] is True


def test_gh_passes_repo(al, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        import subprocess as sp
        seen["cmd"] = cmd
        return sp.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(al.subprocess, "run", fake_run)
    al._gh(["issue", "list"])
    assert seen["cmd"][-2:] == ["--repo", al.GH_REPO]


def test_gh_missing_binary_does_not_raise(al, monkeypatch):
    def raising(cmd, **kw):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(al.subprocess, "run", raising)
    assert al._gh(["issue", "list"]).returncode == 1
