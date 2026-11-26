#!/usr/bin/env python3
"""The sign-in alarm of a dev machine (MCP-G17g, Metorite task 4033).

**Why.** When the Metorite MCP sign-in breaks, every agent lane loses the
board. Nobody notices for hours. A user timer runs this file every 10
minutes on each dev machine (home box, aroh-dev-1, the PC's WSL).

**What it does.** It runs ``claude mcp list`` in the lane checkout and reads
the ``metorite`` line. The state is one of:

- ``ok``: "Connected".
- ``auth``: "Needs authentication". DOWN.
- ``missing``: no ``metorite`` line. DOWN.
- ``error``: a timeout, other text, or a failed run with no ``metorite``
  line. Logged only. It neither notifies nor resets, so a flaky check never
  pages the owner.

On the first DOWN after ``ok`` or unknown, it writes one NOTE line to the
lane log and sends ONE owner notice on the channel. A repeated DOWN sends
nothing. On recovery it writes one "back" NOTE and resets.

**Channel** (env ``METORITE_ALARM_CHANNEL``):

- ``note`` (default): the NOTE line only. Ships dark.
- ``gh``: one GitHub issue (``gh issue create``). It needs
  ``METORITE_ALARM_GH_USER``, the login to mention. With that unset it
  refuses to send. On recovery it comments and closes the issue.

It never prints a token and never mails or mentions anyone else.

Fence: ``tests/unit/test_mcp_signin_alarm.py``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
from collections.abc import Callable
from typing import Any

HOME = pathlib.Path.home()
STATE_PATH = pathlib.Path(os.environ.get(
    "METORITE_ALARM_STATE", str(HOME / ".local/state/metorite/signin_alarm.json")))
NOTE_LOG = pathlib.Path(os.environ.get(
    "METORITE_ALARM_LOG", str(HOME / "lanes/merge-queue.log")))
LIST_TIMEOUT_S = 90
DOWN = ("auth", "missing")

RESIGN = ("Re-sign: docs/home_box.md section 5.2 and docs/box_connect.md "
          "(claude mcp login metorite --no-browser, then send the callback address).")


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def host() -> str:
    return os.environ.get("METORITE_ALARM_HOST") or socket.gethostname()


def classify(text: str, returncode: int = 0) -> str:
    """Read the ``metorite`` line of ``claude mcp list`` output."""
    for line in text.splitlines():
        if re.match(r"\s*metorite\s*:", line):
            low = line.lower()
            if "needs authentication" in low:
                return "auth"
            if "connected" in low and "not connected" not in low and "failed" not in low:
                return "ok"
            return "error"
    return "error" if returncode != 0 else "missing"


def run_list(cwd: pathlib.Path) -> tuple[str, int] | None:
    try:
        p = subprocess.run(["claude", "mcp", "list"], cwd=str(cwd), capture_output=True,
                           text=True, timeout=LIST_TIMEOUT_S, encoding="utf-8", errors="replace")
    except (subprocess.TimeoutExpired, OSError):
        return None
    return p.stdout + p.stderr, p.returncode


def check(cwd: pathlib.Path, runner: Callable[[pathlib.Path], Any] = run_list) -> str:
    res = runner(cwd)
    if res is None:
        return "error"
    return classify(res[0], res[1])


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "unknown", "since": None, "notified": False}


def save_state(st: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(st), encoding="utf-8")


def write_note(msg: str) -> None:
    t = now_utc().strftime("%H:%M")
    NOTE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with NOTE_LOG.open("a", encoding="utf-8") as f:
        f.write(f"NOTE\tsignin-alarm\t{t} UTC: {msg}\n")


def _gh(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)


def notify_down(state: str, since: str, st: dict, gh: Callable = _gh) -> None:
    """Send the ONE owner notice. Sets ``st['issue']`` for the gh channel."""
    if os.environ.get("METORITE_ALARM_CHANNEL", "note") != "gh":
        return
    user = os.environ.get("METORITE_ALARM_GH_USER", "").strip().lstrip("@")
    if not user:
        print("signin-alarm: gh channel needs METORITE_ALARM_GH_USER. Not sent.", file=sys.stderr)
        return
    body = f"@{user} Metorite sign-in is {state} on {host()} since {since}.\n\n{RESIGN}\n"
    p = gh(["issue", "create", "--title", f"Metorite sign-in down on {host()}", "--body", body])
    m = re.search(r"/issues/(\d+)", p.stdout or "")
    if p.returncode == 0 and m:
        st["issue"] = int(m.group(1))


def notify_back(st: dict, gh: Callable = _gh) -> None:
    n = st.get("issue")
    if os.environ.get("METORITE_ALARM_CHANNEL", "note") != "gh" or not n:
        return
    gh(["issue", "comment", str(n), "--body", f"Metorite sign-in is back on {host()}."])
    gh(["issue", "close", str(n)])


def step(observed: str, gh: Callable = _gh) -> dict:
    st = load_state()
    if observed == "error":
        print("signin-alarm: check error, state unchanged", file=sys.stderr)
        return st
    since = now_utc().strftime("%H:%M UTC")
    if observed in DOWN:
        if st.get("state") in DOWN:
            st["state"] = observed
            save_state(st)
            return st
        st = {"state": observed, "since": since, "notified": True}
        write_note(f"{host()}: metorite {observed} since {since}. {RESIGN}")
        notify_down(observed, since, st, gh)
        save_state(st)
        return st
    # ok
    if st.get("state") in DOWN:
        write_note(f"{host()}: metorite back (was {st['state']} since {st.get('since')}).")
        notify_back(st, gh)
    st = {"state": "ok", "since": since, "notified": False}
    save_state(st)
    return st


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", default=os.environ.get("METORITE_REPO", str(HOME / "Metorite")))
    a = ap.parse_args(argv)
    observed = check(pathlib.Path(a.cwd))
    step(observed)
    print(f"signin-alarm: {host()} metorite {observed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
