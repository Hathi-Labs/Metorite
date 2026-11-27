"""CIV-SMOKE-TURN (task 4061) — the release smoke runs one real model turn.

Spec: ``project-docs/specs/projects_ai_chat.md`` §21.10.

``scripts/smoke_chat_turn.py`` is DARK. This file is the fence (R7) for its
claims, with a fake stream, a fake journal and a fake spend read:

  1. Off by default: no turn, no spend read, no model call.
  2. On with no cap: REFUSED, exit 2, and nothing is written.
  3. A spend at or over the cap, or a spend that cannot be read: a WARNING
     line, no turn, and exit 0.
  4. A stream with no ``done``, an empty saved answer, or a Traceback in the
     log window: red (exit 1), and no PASS line.
  5. The cookie never reaches the argv or the environment of journalctl, and
     journalctl reads ``/dev/null``.
  6. The sub-bounds of the turn fit inside ``SMOKE_TURN_S``.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
COOKIE = "__Secure-authjs.session-token=JWE-turn-0815"
NOW = datetime(2026, 11, 27, 6, 0, tzinfo=UTC)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # a dataclass reads its module from sys.modules
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def turn():
    return _load("smoke_chat_turn")


def _rollup(*costs: tuple[str, float]) -> dict:
    return {"days": [{"date": d, "cost": c} for d, c in costs], "totals": {}}


class Fake:
    """The BFF, the stream and the journal of one run."""

    def __init__(self, *, spend=(200, _rollup(("2026-10-31", 50.0), ("2026-11-26", 1.25))),
                 frames=None, status=200, answer="There are 3 integrations.",
                 journal=(0, "INFO run.start\nINFO run.finished\n")):
        self.spend = spend
        self.frames = frames if frames is not None else [
            b'data: {"type":"tool_start","id":"t1","name":"list_integrations"}\n',
            b"\n",
            b'data: {"type":"delta","content":"There are 3."}\n',
            b'data: {"type":"done","run_id":"r1"}\n',
        ]
        self.status = status
        self.answer = answer
        self.journal_out = journal
        self.calls: list[tuple[str, str]] = []
        self.posted: dict | None = None
        self.windows: list[tuple[float, float]] = []

    def call(self, base, cookie, method, path, body=None):
        assert cookie == COOKIE
        self.calls.append((method, path))
        if path.startswith("/api/observability/cost"):
            return self.spend
        if path.endswith("/messages") and method == "GET":
            aid = self.posted["assistantMessageId"] if self.posted else None
            return 200, [{"id": "u1", "role": "user", "content": "hi"},
                         {"id": aid, "role": "assistant", "content": self.answer}]
        return 200, {}

    def stream(self, base, cookie, body):
        self.posted = body
        if self.status != 200:
            return self.status, "upstream refused"
        return 200, iter(self.frames)

    def journal(self, since, until):
        self.windows.append((since, until))
        return self.journal_out


def _run(turn, fake: Fake, cfg) -> int:
    return turn.run("https://app.example", COOKIE, "sid-1", cfg, fake.call,
                    stream=fake.stream, journal=fake.journal, now=lambda: NOW,
                    nap=lambda _s: None)


def _on(turn, cap="5"):
    return turn.read_config({"SMOKE_CHAT_TURN": "1", "SMOKE_CHAT_TURN_CAP_USD": cap})


# ── 1. The switch and the cap ───────────────────────────────────────────────


@pytest.mark.parametrize("value", ["", "0", "off", "false", "no", "2", "enabled"])
def test_the_turn_is_off_unless_the_switch_says_on(turn, value) -> None:
    cfg = turn.read_config({"SMOKE_CHAT_TURN": value, "SMOKE_CHAT_TURN_CAP_USD": "5"})
    assert cfg.on is False and cfg.refusal is None


def test_off_by_default_runs_no_turn(turn, capsys) -> None:
    fake = Fake()
    assert _run(turn, fake, turn.read_config({})) == 0
    assert fake.calls == [] and fake.posted is None and fake.windows == []
    assert "skip T0 turn: SMOKE_CHAT_TURN is off" in capsys.readouterr().out


@pytest.mark.parametrize("cap", ["", "  ", "0", "-1", "nan", "inf", "five"])
def test_on_with_no_valid_cap_is_a_refusal(turn, cap) -> None:
    cfg = turn.read_config({"SMOKE_CHAT_TURN": "1", "SMOKE_CHAT_TURN_CAP_USD": cap})
    assert cfg.on is True and cfg.refusal
    assert "SMOKE_CHAT_TURN_CAP_USD" in cfg.refusal


def test_a_valid_cap_is_read(turn) -> None:
    cfg = _on(turn, "2.50")
    assert cfg.refusal is None and cfg.cap_usd == 2.5


# ── 2. The spend ────────────────────────────────────────────────────────────


def test_the_spend_is_this_utc_month_only(turn) -> None:
    body = _rollup(("2026-10-31", 99.0), ("2026-11-01", 1.0), ("2026-11-27", 0.5))
    assert turn.month_spend(200, body, NOW) == 1.5


@pytest.mark.parametrize(("status", "body"), [
    (200, {"days": [], "by_model": {}, "totals": {"cost": 0}}),  # the BFF's EMPTY
    (403, {"detail": "no"}),
    (200, None),
    (200, {"days": [{"date": "2026-11-27", "cost": "x"}]}),
])
def test_a_spend_that_cannot_be_read_is_unknown_and_never_zero(turn, status, body) -> None:
    assert turn.month_spend(status, body, NOW) is None


@pytest.mark.parametrize(("spent", "cap"), [(5.0, "5"), (7.5, "5")])
def test_a_spend_at_or_over_the_cap_skips_the_turn(turn, capsys, spent, cap) -> None:
    fake = Fake(spend=(200, _rollup(("2026-11-02", spent))))
    assert _run(turn, fake, _on(turn, cap)) == 0
    out = capsys.readouterr().out
    assert "WARNING T1 turn skipped" in out and "at or over the cap" in out
    assert fake.posted is None, "no model call over the cap"


def test_an_unread_spend_skips_the_turn(turn, capsys) -> None:
    fake = Fake(spend=(200, {"days": [], "totals": {}}))
    assert _run(turn, fake, _on(turn)) == 0
    assert "could not be read" in capsys.readouterr().out
    assert fake.posted is None


# ── 3. The turn ─────────────────────────────────────────────────────────────


def test_a_good_turn_passes_all_four_checks(turn, capsys) -> None:
    fake = Fake()
    assert _run(turn, fake, _on(turn)) == 0
    out = capsys.readouterr().out
    for line in ("ok   T1 spend $1.25 of $5.00", "ok   T2 stream done (tools: list_integrations)",
                 "ok   T3 saved answer", "ok   T4 no Traceback"):
        assert line in out, out
    body = fake.posted
    assert body["agentName"] == "orchestrator" and body["mode"] == "copilot"
    assert body["threadId"] == "sid-1" and "list_integrations" in body["message"]
    since, until = fake.windows[0]
    assert since <= until


def test_a_stream_without_done_is_red(turn, capsys) -> None:
    fake = Fake(frames=[b'data: {"type":"delta","content":"half"}\n'])
    assert _run(turn, fake, _on(turn)) == 1
    out = capsys.readouterr().out
    assert "FAIL T2 stream: the stream ended with no done event" in out
    assert fake.windows, "the log is still read after a failed stream"


def test_an_error_event_is_red(turn, capsys) -> None:
    fake = Fake(frames=[b'data: {"type":"error","content":"Gateway error: 500"}\n',
                        b'data: {"type":"done"}\n'])
    assert _run(turn, fake, _on(turn)) == 1
    assert "FAIL T2 stream: an error event: Gateway error: 500" in capsys.readouterr().out


def test_a_refused_stream_is_red(turn, capsys) -> None:
    fake = Fake(status=429)
    assert _run(turn, fake, _on(turn)) == 1
    assert "FAIL T2 stream: HTTP 429" in capsys.readouterr().out


def test_a_stream_past_its_deadline_is_red(turn) -> None:
    ticks = iter([0.0, 500.0])
    got = turn.read_stream(iter([b'data: {"type":"delta"}\n', b'data: {"type":"done"}\n']),
                           deadline=100.0, clock=lambda: next(ticks))
    assert got.late and not got.done


@pytest.mark.parametrize("answer", ["", "   ", None])
def test_an_empty_saved_answer_is_red(turn, capsys, answer) -> None:
    fake = Fake(answer=answer)
    assert _run(turn, fake, _on(turn)) == 1
    out = capsys.readouterr().out
    assert "FAIL T3 saved answer" in out
    reads = [p for m, p in fake.calls if p.endswith("/messages")]
    assert len(reads) == turn.READ_TRIES


def test_a_traceback_in_the_log_window_is_red(turn, capsys) -> None:
    log = ("INFO run.start\nTraceback (most recent call last):\n"
           '  File "x.py", line 1\nValueError: boom\n')
    fake = Fake(journal=(0, log))
    assert _run(turn, fake, _on(turn)) == 1
    out = capsys.readouterr().out
    assert "FAIL T4 gateway log: 1 Traceback(s)" in out and "ValueError: boom" in out


@pytest.mark.parametrize(("rc", "text", "why"), [
    (1, "", "exited 1"),
    (0, "", "no line"),
])
def test_an_unread_log_is_red_and_never_a_pass(turn, capsys, rc, text, why) -> None:
    fake = Fake(journal=(rc, text))
    assert _run(turn, fake, _on(turn)) == 1
    assert why in capsys.readouterr().out


# ── 4. The journal child: no cookie, no stdin, a time window ────────────────


def test_journalctl_gets_no_cookie_and_reads_dev_null(turn, monkeypatch) -> None:
    seen: dict = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return subprocess.CompletedProcess(argv, 0, "INFO ok\n", "")

    monkeypatch.setenv("SMOKE_COOKIE", COOKIE)
    monkeypatch.setattr(turn.subprocess, "run", fake_run)
    assert turn.read_journal(1_800_000_000.4, 1_800_000_090.9) == (0, "INFO ok\n")
    argv, kw = seen["argv"], seen["kw"]
    assert COOKIE not in " ".join(argv)
    assert "SMOKE_COOKIE" not in kw["env"] and COOKIE not in kw["env"].values()
    assert kw["stdin"] is subprocess.DEVNULL
    assert kw["timeout"] == turn.JOURNAL_S
    assert argv[:3] == ["journalctl", "-u", "acb-gateway"]
    assert argv[argv.index("--since") + 1] == "@1800000000"
    assert argv[argv.index("--until") + 1] == "@1800000091"


def test_a_journal_that_hangs_is_an_unread_log(turn, monkeypatch) -> None:
    def hang(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(turn.subprocess, "run", hang)
    rc, _ = turn.read_journal(0, 1)
    assert rc != 0 and turn.judge_journal(rc, "") is not None


def test_the_module_writes_no_file_and_names_no_cookie_in_argv() -> None:
    """The source holds one subprocess call, and no open() for writing."""
    text = (SCRIPTS / "smoke_chat_turn.py").read_text(encoding="utf-8")
    assert text.count("subprocess.run(") == 1
    assert not re.search(r"open\([^)]*['\"][wa]", text)
    assert "SMOKE_COOKIE" in text  # only to strip it from the child's env


# ── 5. The persist smoke runs the turn, and no PASS follows a red turn ──────


@pytest.fixture()
def persist(monkeypatch):
    mod = _load("smoke_chat_persist")
    for k, v in {"SMOKE_BASE_URL": "https://app.example", "SMOKE_COOKIE": COOKIE,
                 "SMOKE_MEMBER_EMAIL": "smoke@x.invalid", "SMOKE_ORG_SLUG": "smoke-chat"}.items():
        monkeypatch.setenv(k, v)
    for k in ("SMOKE_CHAT_TURN", "SMOKE_CHAT_TURN_CAP_USD", "SMOKE_TURN_ONLY"):
        monkeypatch.delenv(k, raising=False)
    calls: list[tuple[str, str]] = []
    saved: dict = {}

    def fake_call(base, cookie, method, path, body=None):
        calls.append((method, path))
        if path == "/api/auth/me":
            return 200, {"email": "smoke@x.invalid", "organization": {"slug": "smoke-chat"}}
        if path == "/api/chat/sessions" and method == "GET":
            return 200, []
        if path.endswith("/messages") and method == "POST":
            saved["row"] = body[0]
            return 200, {"saved": 1}
        if path.endswith("/messages") and method == "GET":
            return 200, [{"id": saved["row"]["id"], "content": saved["row"]["content"]}]
        return 200, {}

    monkeypatch.setattr(mod, "_call", fake_call)
    turn_runs: list = []
    real = mod._turn_module

    def with_turn(rc: int):
        def loader():
            t = real()
            t.run = lambda *a, **k: (turn_runs.append(a), rc)[1]
            return t
        monkeypatch.setattr(mod, "_turn_module", loader)

    mod.calls, mod.turn_runs, mod.with_turn = calls, turn_runs, with_turn
    return mod


def test_persist_off_prints_pass_and_the_skip_line(persist, capsys) -> None:
    assert persist.main() == 0
    out = capsys.readouterr().out
    assert "skip T0 turn" in out and "PASS chat persistence" in out
    assert out.index("ok   3 read it back") < out.index("skip T0") < out.index("ok   4 delete")


def test_persist_on_with_no_cap_refuses_and_writes_nothing(persist, monkeypatch,
                                                            capsys) -> None:
    monkeypatch.setenv("SMOKE_CHAT_TURN", "1")
    assert persist.main() == 2
    out = capsys.readouterr().out
    assert out.startswith("REFUSED T0 turn:") and "PASS" not in out
    assert persist.calls == [], "a refusal writes nothing and calls nothing"


def test_a_red_turn_gives_exit_1_and_no_pass_line(persist, monkeypatch, capsys) -> None:
    monkeypatch.setenv("SMOKE_CHAT_TURN", "1")
    monkeypatch.setenv("SMOKE_CHAT_TURN_CAP_USD", "5")
    persist.with_turn(1)
    assert persist.main() == 1
    out = capsys.readouterr().out
    assert "PASS" not in out and "ok   4 delete session" in out
    assert len(persist.turn_runs) == 1


def test_turn_only_skips_the_save_steps(persist, monkeypatch, capsys) -> None:
    monkeypatch.setenv("SMOKE_CHAT_TURN", "1")
    monkeypatch.setenv("SMOKE_CHAT_TURN_CAP_USD", "5")
    monkeypatch.setenv("SMOKE_TURN_ONLY", "1")
    persist.with_turn(0)
    assert persist.main() == 0
    out = capsys.readouterr().out
    assert "PASS chat turn on" in out and "PASS chat persistence" not in out
    assert "2 save one row" not in out and "swept" not in out
    sid = persist.turn_runs[0][2]
    assert ("DELETE", f"/api/chat/sessions/{sid}") in persist.calls


def test_turn_only_with_the_switch_off_writes_nothing(persist, monkeypatch, capsys) -> None:
    monkeypatch.setenv("SMOKE_TURN_ONLY", "1")
    assert persist.main() == 0
    assert "skip T0 turn" in capsys.readouterr().out
    assert persist.calls == [("GET", "/api/auth/me")]


# ── 6. The time bound ───────────────────────────────────────────────────────


def test_the_turn_fits_inside_its_bound(turn) -> None:
    worst = (turn.CALL_S                                  # T1
             + turn.STREAM_S + turn.STREAM_READ_S         # T2
             + turn.READ_TRIES * turn.CALL_S + (turn.READ_TRIES - 1) * turn.READ_NAP_S  # T3
             + turn.JOURNAL_S)                            # T4
    doc = turn.__doc__ or ""
    m = re.search(r"sum\s+(\d+) s", doc)
    assert m and int(m.group(1)) == worst, (worst, m and m.group(1))
    sh = (ROOT / "deploy" / "smoke_chat.sh").read_text(encoding="utf-8")
    turn_s = int(re.search(r'^SMOKE_TURN_S="\$\{SMOKE_TURN_S:-(\d+)\}"', sh, re.M).group(1))
    assert turn_s == turn.TURN_S
    assert worst < turn_s, "the module must report before the shell kills it"
