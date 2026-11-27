"""LBB-1a: the "done means live" report (task 4044).

Spec: project-docs/specs/engineering_practice.md section 7b.6. Each test names
the Done-when item it holds. No systemd and no network: the clone is a temp git
repo, the logs are temp files, and /version is a command that prints JSON.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is missing")

HOME_SERVER = Path(__file__).resolve().parents[2] / "scripts" / "home_server"
SCRIPT = HOME_SERVER / "live_check.py"


def epoch(text: str) -> int:
    return int(dt.datetime.fromisoformat(text).replace(tzinfo=dt.UTC).timestamp())


class Rig:
    def __init__(self, tmp: Path) -> None:
        self.lanes = tmp / "lanes"
        self.lanes.mkdir()
        self.src = tmp / "src"
        self.src.mkdir()
        self.env_base = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.n = 0

    def git(self, *args: str, when: str | None = None) -> str:
        env = dict(self.env_base)
        if when:
            stamp = f"{epoch(when)} +0000"
            env["GIT_COMMITTER_DATE"] = stamp
            env["GIT_AUTHOR_DATE"] = stamp
        out = subprocess.run(["git", "-C", str(self.src), *args], capture_output=True,
                             text=True, check=True, env=env)
        return out.stdout.strip()

    def commit(self, when: str) -> str:
        self.n += 1
        (self.src / "f.txt").write_text(str(self.n), encoding="utf-8")
        self.git("add", "f.txt")
        self.git("commit", "-q", "-m", f"c{self.n}", when=when)
        return self.git("rev-parse", "HEAD")

    def bot_log(self, *rows: tuple[str, str]) -> None:
        text = "".join(f"DONE {pr} {sha}\nBACK 9 nope\n" for pr, sha in rows)
        (self.lanes / "merge-bot.log").write_text(text, encoding="utf-8")

    def queue_log(self, *rows: tuple[str, str, str]) -> None:
        text = "".join(f"DONE\t{pr}\t{sha}\t{note}\nNOTE\tx\ty\n" for pr, sha, note in rows)
        (self.lanes / "merge-queue.log").write_text(text, encoding="utf-8")

    def version(self, body: object) -> str:
        path = self.lanes.parent / "version.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        return f'"{sys.executable}" -c "import sys;print(open(sys.argv[1]).read())" "{path}"'

    def run(self, now: str, version_cmd: str | None = None, mode: str | None = "dry",
            **extra: str) -> tuple[subprocess.CompletedProcess, list[list[str]]]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("LIVE_CHECK_")}
        env.update(LIVE_CHECK_LANES=str(self.lanes), LIVE_CHECK_SRC=str(self.src),
                   LIVE_CHECK_NOW=str(epoch(now)))
        if mode is not None:
            env["LIVE_CHECK_MODE"] = mode
        if version_cmd is not None:
            env["LIVE_CHECK_VERSION_CMD"] = version_cmd
        env.update(extra)
        done = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                              env=env, check=False)
        report = self.lanes / "live-check.tsv"
        rows = []
        if report.exists():
            rows = [ln.split("\t") for ln in report.read_text(encoding="utf-8").splitlines()]
        return done, rows


@pytest.fixture()
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def test_done1_off_missing_or_other_mode_reads_and_writes_nothing(rig: Rig) -> None:
    sha = rig.commit("2026-11-27T01:00:00")
    rig.bot_log(("1", sha))
    # a version command that would leave a mark if it ran
    mark = rig.lanes.parent / "called"
    cmd = f'"{sys.executable}" -c "open(r\'{mark}\',\'w\').write(\'x\')"'
    for mode in (None, "off", "bogus"):
        done, rows = rig.run("2026-11-27T06:00:00", version_cmd=cmd, mode=mode)
        assert done.returncode == 0
        assert rows == [] and not mark.exists()
    assert "bogus" in done.stderr


def test_done2_dry_one_line_per_pr_sha_both_shapes_and_window(rig: Rig) -> None:
    old = rig.commit("2026-11-20T01:00:00")  # older than 72 hours
    a = rig.commit("2026-11-26T01:00:00")
    b = rig.commit("2026-11-26T02:00:00")
    c = rig.commit("2026-11-26T03:00:00")
    rig.bot_log(("10", a), ("11", old), ("12", c))
    # b only in the queue, c in both logs (a duplicate), a with a task
    rig.queue_log(("20", b, "merged (task 4044) ok"), ("12", c, "merged, no task"),
                  ("10", a, "merged (task 77)"))
    cmd = rig.version({"sha": "x", "applied_sha": c})
    done, rows = rig.run("2026-11-27T06:00:00", cmd)
    assert done.returncode == 0
    by_key = {(r[1], r[2]): r for r in rows}
    assert len(rows) == 3 and set(by_key) == {("10", a), ("20", b), ("12", c)}
    assert by_key[("20", b)][3] == "4044"
    assert by_key[("10", a)][3] == "77"  # the queue shape supplies the task
    assert by_key[("12", c)][3] == "-"
    assert all(len(r) == 6 and r[0] == "2026-11-27T06:00:00Z" for r in rows)


def test_done3_live_when_ancestor_of_deployed(rig: Rig) -> None:
    a = rig.commit("2026-11-26T01:00:00")
    b = rig.commit("2026-11-26T02:00:00")
    rig.bot_log(("1", a))
    _, rows = rig.run("2026-11-27T06:00:00", rig.version({"applied_sha": b}))
    assert rows[0][4] == "LIVE" and rows[0][5] == b


def test_done3_waiting_with_one_slot_instant(rig: Rig) -> None:
    a = rig.commit("2026-11-27T04:00:00")
    b = rig.commit("2026-11-27T04:30:00")
    rig.bot_log(("1", b))
    _, rows = rig.run("2026-11-27T06:00:00", rig.version({"applied_sha": a}))
    assert rows[0][4] == "WAITING"  # only 05:30 lies in (04:30, 06:00]


def test_done3_stuck_crosses_2130_then_0530(rig: Rig) -> None:
    a = rig.commit("2026-11-26T20:00:00")
    b = rig.commit("2026-11-26T21:00:00")
    rig.bot_log(("1", b))
    _, rows = rig.run("2026-11-27T06:00:00", rig.version({"applied_sha": a}))
    assert rows[0][4] == "STUCK"  # 21:30 and 05:30 across midnight


def test_slot_instant_at_merge_time_is_excluded_and_at_now_is_included(rig: Rig) -> None:
    a = rig.commit("2026-11-26T21:30:00")
    b = rig.commit("2026-11-26T21:30:00")
    rig.bot_log(("1", b))
    # interval (21:30, 05:30] holds one slot at 05:30 only
    _, rows = rig.run("2026-11-27T05:30:00", rig.version({"applied_sha": a}))
    assert rows[0][4] == "WAITING"


def test_done3_unknown_when_merge_sha_cannot_be_resolved(rig: Rig) -> None:
    a = rig.commit("2026-11-26T01:00:00")
    ghost = "f" * 40
    rig.bot_log(("1", ghost))
    _, rows = rig.run("2026-11-27T06:00:00", rig.version({"applied_sha": a}))
    assert len(rows) == 1 and rows[0][4] == "UNKNOWN" and rows[0][2] == ghost


def test_short_applied_sha_is_resolved_with_rev_parse(rig: Rig) -> None:
    a = rig.commit("2026-11-26T01:00:00")
    b = rig.commit("2026-11-26T02:00:00")
    rig.bot_log(("1", a))
    _, rows = rig.run("2026-11-27T06:00:00", rig.version({"applied_sha": b[:9]}))
    assert rows[0][4] == "LIVE" and rows[0][5] == b


def test_unknown_when_deployed_sha_cannot_be_resolved(rig: Rig) -> None:
    a = rig.commit("2026-11-26T01:00:00")
    rig.bot_log(("1", a))
    _, rows = rig.run("2026-11-27T06:00:00", rig.version({"applied_sha": "e" * 40}))
    assert rows[0][4] == "UNKNOWN"


def test_done4_null_applied_sha_gives_one_unknown_line(rig: Rig) -> None:
    a = rig.commit("2026-11-26T01:00:00")
    rig.bot_log(("1", a))
    done, rows = rig.run("2026-11-27T06:00:00", rig.version({"sha": a, "applied_sha": None}))
    assert done.returncode == 0
    assert rows == [["2026-11-27T06:00:00Z", "-", "-", "-", "UNKNOWN", "-"]]


def test_done4_failed_read_gives_one_unknown_line(rig: Rig) -> None:
    rig.bot_log(("1", "a" * 40))
    cmd = f'"{sys.executable}" -c "import sys;sys.exit(3)"'
    done, rows = rig.run("2026-11-27T06:00:00", cmd)
    assert done.returncode == 0 and len(rows) == 1 and rows[0][4] == "UNKNOWN"


def test_done4_missing_url_gives_one_unknown_line(rig: Rig) -> None:
    done, rows = rig.run("2026-11-27T06:00:00")  # no command and no URL
    assert done.returncode == 0
    assert rows == [["2026-11-27T06:00:00Z", "-", "-", "-", "UNKNOWN", "-"]]


def test_done5_unit_text() -> None:
    timer = (HOME_SERVER / "metorite-live-check.timer").read_text(encoding="utf-8")
    service = (HOME_SERVER / "metorite-live-check.service").read_text(encoding="utf-8")
    cal = [ln.split("=", 1)[1].strip() for ln in timer.splitlines() if ln.startswith("OnCalendar=")]
    assert cal == ["*-*-* 06:00:00 UTC", "*-*-* 14:00:00 UTC", "*-*-* 22:00:00 UTC"]
    assert "Persistent=true" in timer
    lines = service.splitlines()
    assert "EnvironmentFile=-%h/.config/metorite/live-check.env" in lines
    assert "ExecStart=/usr/bin/python3 %h/.local/lib/metorite/live_check.py" in lines
    assert "Type=oneshot" in lines and "TimeoutStartSec=300" in lines
    body = "\n".join(ln for ln in lines if not ln.startswith("#")).lower()
    for word in ("token", "secret", "password", "api_key", "bearer"):
        assert word not in body
