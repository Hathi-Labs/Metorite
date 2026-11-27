#!/usr/bin/env python3
"""One guarded flip of a per-org flag on the production box (task 4086).

Run it on the box as ``acb``, through ``scripts/flag_flip.sh``. Examples::

    bash scripts/flag_flip.sh TOOL_PACKS_ORGS --add <org id> --task 4086
    bash scripts/flag_flip.sh DECIDE_FEATURE_MODES --add '<org id>=warn' --task 4086
    bash scripts/flag_flip.sh CRM_ORGS --off --task 4086 --dry-run

**Why.** Flags went on by hand edits of ``.env``. On 2026-11-27 a hand edit
wrote two edge settings into the root ``.env``, and the edge reads only its
own env file, so neither line did anything. This tool makes each flip the
same set of checked steps.

**The guards, in order.** Each one refuses before any write.

1. The name must be a field of ``acb_common.flag_registry.FlagRegistry``.
   A switch, a limit or a secret is not a per-org flag, and a reload does
   not apply it.
2. A flag in ``OWNER_ONLY_FLAGS`` (money, identity, a third party, a real
   person) needs ``--owner-approved <answer id>``, the id of the owner's
   answer on the task.
3. The new value must be empty, ``*``, or org ids. A ``*_MODES`` value is
   ``<org id or *>=<mode>`` pairs.
4. The value must be the same in each env file that holds the key, so
   ``--add`` and ``--remove`` start from one known value.
5. The gateway must report ``flag_reload: true`` on ``/version``. A gateway
   with no reload handler ends on SIGHUP.

**The flip.** It takes a lock, backs up each env file that it changes, and
rewrites each one atomically, with the same mode. The root ``.env`` always
holds the key. Another env file changes only when it already holds the key.
It sends SIGHUP to the gateway main process only, and it waits for the
``flags.reloaded`` line that names the key. It restarts acb-agent-edge only
when the edge env file changed, and then checks the new process env.

**The record.** It appends one line to ``<app>/.flag_flips.log``, and it
prints the evidence and the rollback commands. When the chat-turn smoke is on
(``SMOKE_CHAT_TURN=1``, task 4061), it runs that smoke after the flip.

Exit codes: 0 flipped (or no change), 2 refused before any write, 3 the flip
is written but not proved, 4 the smoke failed after a proved flip.

Fence: ``tests/unit/test_flag_flip.py``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

APP_DIR = Path(os.environ.get("FLAG_FLIP_APP_DIR", "/opt/acb/app"))
ROOT_ENV = ".env"
#: The other env files of the box, each with the unit that reads it. A file
#: changes only when it already holds the key.
OTHER_ENVS = {
    "apps/services/agent_edge/.env": "acb-agent-edge",
    "apps/services/customer_console/.env": "acb-customer-console",
    "workbench/control_plane/.env.local": "acb-workbench",
    "workbench/operator_console/.env.local": "acb-operator-console",
}
#: The units that read the root .env at start, besides the gateway. A flip
#: reaches them at their next restart.
ROOT_ENV_OTHERS = ("acb-whatsapp-bridge",)
GATEWAY = "acb-gateway"
EDGE = "acb-agent-edge"
VERSION_URL = os.environ.get("FLAG_FLIP_VERSION_URL", "http://127.0.0.1:8080/version")
RELOAD_WAIT_S = float(os.environ.get("FLAG_FLIP_RELOAD_WAIT_S", "20"))
SMOKE_TIMEOUT_S = 600

ORG_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MODE_RE = re.compile(r"[a-z][a-z0-9_]{0,30}")
KEY_RE = re.compile(r"[A-Z][A-Z0-9_]{0,80}")
ANSWER_RE = re.compile(r"[0-9a-fA-F-]{8,64}")


class Refused(Exception):
    """A guard refused the flip. Nothing was written."""


# ── The registry ────────────────────────────────────────────────────────────


def registry() -> tuple[frozenset[str], frozenset[str]]:
    """The flag names of FlagRegistry and the owner-only names, in upper case."""
    from acb_common import flag_registry as fr

    names = frozenset(n.upper() for n in fr.FlagRegistry.model_fields)
    owner = frozenset(n.upper() for n in fr.OWNER_ONLY_FLAGS)
    return names, owner


# ── Values ──────────────────────────────────────────────────────────────────


def _is_modes(key: str) -> bool:
    return key.endswith("_MODES")


def parse(key: str, raw: str) -> list[str]:
    """The items of a value: org ids, or ``org=mode`` pairs. Raises Refused."""
    raw = raw.strip().strip('"').strip("'").strip()
    if raw == "":
        return []
    items = [i.strip() for i in raw.split(",") if i.strip()]
    for item in items:
        if _is_modes(key):
            org, sep, mode = item.partition("=")
            if not sep or not (org == "*" or ORG_RE.fullmatch(org)) or not MODE_RE.fullmatch(mode):
                raise Refused(f"{key}: {item!r} is not <org id or *>=<mode>")
        elif not (item == "*" or ORG_RE.fullmatch(item)):
            raise Refused(f"{key}: {item!r} is not an org id or *")
    if not _is_modes(key) and "*" in items and len(items) > 1:
        raise Refused(f"{key}: * with org ids is ambiguous. Use * alone.")
    orgs = [i.partition("=")[0] for i in items]
    if len(set(orgs)) != len(orgs):
        raise Refused(f"{key}: an org is named twice")
    return items


def new_items(key: str, cur: list[str], op: str, arg: str | None) -> list[str]:
    """The items after one operation. Raises Refused for a vague operation."""
    if op == "off":
        return []
    if op == "set":
        return parse(key, arg or "")
    item = (arg or "").strip()
    if op == "add":
        parse(key, item)
        if not _is_modes(key) and item == "*":
            return ["*"]
        if not _is_modes(key) and "*" in cur:
            raise Refused(f"{key} is already * (every org)")
        org = item.partition("=")[0]
        kept = [i for i in cur if i.partition("=")[0] != org]
        return [*kept, item]
    if op == "remove":
        if not (item == "*" or ORG_RE.fullmatch(item)):
            raise Refused(f"{key}: {item!r} is not an org id or *")
        if not _is_modes(key) and "*" in cur and item != "*":
            raise Refused(f"{key} is * (every org). Name the orgs with --set.")
        kept = [i for i in cur if i.partition("=")[0] != item]
        if kept == cur:
            raise Refused(f"{key} does not name {item}")
        return kept
    raise Refused(f"unknown operation {op}")


# ── Env files ───────────────────────────────────────────────────────────────


def read_key(path: Path, key: str) -> str | None:
    """The value of the LAST ``KEY=`` line, as python-dotenv reads it. None when absent."""
    val = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            val = line[len(key) + 1 :].strip().strip('"').strip("'").strip()
    return val


def rewrite(path: Path, key: str, value: str) -> bytes:
    """The new bytes of *path*: one ``KEY=value`` line, every other byte kept."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    done = False
    for line in lines:
        if line.startswith(f"{key}="):
            if not done:
                nl = "\n" if line.endswith("\n") else ""
                out.append(f"{key}={value}{nl}")
                done = True
            continue  # a second KEY= line goes, so one line holds the key
        out.append(line)
    if not done:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(f"{key}={value}\n")
    return "".join(out).encode("utf-8")


def atomic_write(path: Path, data: bytes) -> None:
    """Write *data* over *path* with one rename. The mode and owner stay."""
    st = path.stat()
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.flagflip.", dir=str(path.parent))
    try:
        os.fchmod(fd, st.st_mode & 0o7777)
        try:
            os.fchown(fd, st.st_uid, st.st_gid)
        except PermissionError:
            pass  # the same user owns both: nothing to change
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def backup(path: Path, stamp: str, task: str) -> Path:
    bak = path.with_name(f"{path.name}.bak.{stamp}.flagflip-{task}")
    shutil.copy2(path, bak)
    os.chmod(bak, 0o600)
    return bak


# ── The box ─────────────────────────────────────────────────────────────────


def run(argv: list[str], timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def gateway_reloads() -> bool:
    """True when /version reports ``flag_reload: true``."""
    res = run(["curl", "-s", "-m", "5", VERSION_URL], timeout=15)
    try:
        return json.loads(res.stdout or "{}").get("flag_reload") is True
    except ValueError:
        return False


def _since(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def reload_gateway(key: str, t0: float) -> str:
    """SIGHUP the gateway main process and wait for its log line. Returns the line.

    Raises RuntimeError when the reload is refused or no line names the key.
    """
    res = run(["sudo", "systemctl", "kill", "-s", "HUP", "--kill-whom=main", GATEWAY])
    if res.returncode != 0:
        raise RuntimeError("the SIGHUP to the gateway failed")
    deadline = time.monotonic() + RELOAD_WAIT_S
    while True:
        log = run(["sudo", "journalctl", "-u", GATEWAY, "--since", _since(t0), "-o", "cat", "--no-pager"])
        for line in (ANSI_RE.sub("", ln) for ln in log.stdout.splitlines()):
            if "flags.reload_refused" in line or "flags.reload_failed" in line:
                raise RuntimeError(f"the gateway refused the reload: {line.strip()[:300]}")
            if "flags.reloaded" in line and re.search(rf"\b{key}\b", line, re.IGNORECASE):
                return line.strip()[:300]
        if time.monotonic() >= deadline:
            raise RuntimeError(f"no flags.reloaded line names {key} after {RELOAD_WAIT_S:.0f} s")
        time.sleep(1)


def restart_edge(key: str, value: str) -> str:
    """Restart the edge and prove that its process env holds the new value."""
    if run(["sudo", "systemctl", "restart", EDGE], timeout=120).returncode != 0:
        raise RuntimeError("the restart of acb-agent-edge failed")
    time.sleep(3)
    if run(["systemctl", "is-active", EDGE]).stdout.strip() != "active":
        raise RuntimeError("acb-agent-edge is not active after the restart")
    pid = run(["systemctl", "show", "-p", "MainPID", "--value", EDGE]).stdout.strip()
    env = run(["sudo", "cat", f"/proc/{pid}/environ"]).stdout
    if f"{key}={value}" not in env.split("\0"):
        raise RuntimeError(f"the edge process env does not hold the new {key}")
    return f"acb-agent-edge restarted, pid {pid}, env holds {key}"


def smoke_on(app: Path) -> bool:
    return read_key(app / ROOT_ENV, "SMOKE_CHAT_TURN") == "1" and (app / "deploy/smoke_chat.sh").is_file()


def run_smoke(app: Path) -> bool:
    res = subprocess.run(["bash", "deploy/smoke_chat.sh", "--turn-only"], cwd=app, timeout=SMOKE_TIMEOUT_S,
                         check=False)
    return res.returncode == 0


# ── The flip ────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flag_flip.sh", description="Flip one per-org flag on the box.")
    ap.add_argument("key", help="the flag name, such as TOOL_PACKS_ORGS")
    op = ap.add_mutually_exclusive_group(required=True)
    op.add_argument("--add", metavar="ORG", help="add an org id, * or <org>=<mode>")
    op.add_argument("--remove", metavar="ORG", help="remove an org id or *")
    op.add_argument("--set", metavar="VALUE", help="set the whole value")
    op.add_argument("--off", action="store_true", help="set the value to empty")
    ap.add_argument("--task", required=True, help="the Metorite task number of the flip")
    ap.add_argument("--owner-approved", metavar="ANSWER_ID", help="the owner's answer id, for an owner-only flag")
    ap.add_argument("--dry-run", action="store_true", help="check and print the change, and write nothing")
    ap.add_argument("--no-smoke", action="store_true", help="skip the chat-turn smoke")
    a = ap.parse_args(argv)

    try:
        return flip(a)
    except Refused as exc:
        print(f"flag_flip: REFUSED: {exc}. Nothing was written.", file=sys.stderr)
        return 2


def flip(a: argparse.Namespace) -> int:
    key = a.key.strip().upper()
    if not KEY_RE.fullmatch(key):
        raise Refused(f"{a.key!r} is not a flag name")
    if not re.fullmatch(r"[0-9]{1,6}", a.task or ""):
        raise Refused("--task must be a task number")
    names, owner_only = registry()
    if key not in names:
        raise Refused(f"{key} is not in acb_common/flag_registry.py. This tool flips per-org flags only")
    if key in owner_only:
        if not a.owner_approved or not ANSWER_RE.fullmatch(a.owner_approved):
            raise Refused(f"{key} is owner-only (money, identity or a third party). Pass --owner-approved <answer id>")

    app = APP_DIR
    root = app / ROOT_ENV
    lock_fd = os.open(app / ".flag_flip.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused("another flag flip holds the lock") from None
        return _flip_locked(a, key, app, root)
    finally:
        os.close(lock_fd)


def _flip_locked(a: argparse.Namespace, key: str, app: Path, root: Path) -> int:
    files = [root] + [app / rel for rel in OTHER_ENVS if (app / rel).is_file() and read_key(app / rel, key) is not None]
    values = {str(f.relative_to(app)): read_key(f, key) for f in files}
    seen = {v or "" for v in values.values()}
    if len(seen) > 1:
        raise Refused(f"{key} differs between env files {values}. Fix it with --set")
    old_raw = seen.pop() if seen else ""
    cur = parse(key, old_raw)
    operation = "off" if a.off else "set" if a.set is not None else "add" if a.add is not None else "remove"
    new = new_items(key, cur, operation, a.set if a.set is not None else a.add if a.add is not None else a.remove)
    new_raw = ",".join(new)
    if new == cur and read_key(root, key) is not None:
        print(f"flag_flip: {key} is already {old_raw!r}. No change.")
        return 0
    if not gateway_reloads():
        raise Refused("the gateway does not report flag_reload: true on /version")

    print(f"flag_flip: {key}: {old_raw!r} -> {new_raw!r}")
    print("flag_flip: files: " + ", ".join(str(f.relative_to(app)) for f in files))
    if a.dry_run:
        print("flag_flip: dry run. Nothing was written.")
        return 0

    t0 = time.time()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(t0))
    baks = {f: backup(f, stamp, a.task) for f in files}
    for f in files:
        atomic_write(f, rewrite(f, key, new_raw))
    edge_changed = any(str(f.relative_to(app)) == "apps/services/agent_edge/.env" for f in files)

    evidence: list[str] = []
    result = "proved"
    code = 0
    try:
        evidence.append("gateway: " + reload_gateway(key, t0))
        if edge_changed:
            evidence.append(restart_edge(key, new_raw))
    except RuntimeError as exc:
        result, code = f"not proved: {exc}", 3

    audit = "\t".join([
        dt.datetime.fromtimestamp(t0, dt.UTC).isoformat(timespec="seconds"), a.task, key, old_raw or "-",
        new_raw or "-", ",".join(str(f.relative_to(app)) for f in files), result.split(":")[0],
        a.owner_approved or "-",
    ])
    with open(app / ".flag_flips.log", "a", encoding="utf-8") as fh:
        fh.write(audit + "\n")

    for line in evidence:
        print(f"flag_flip: evidence: {line}")
    if ROOT_ENV_OTHERS:
        print(f"flag_flip: {', '.join(ROOT_ENV_OTHERS)} read the root .env at start. They apply it at their next restart.")
    print("flag_flip: rollback:")
    for f, bak in baks.items():
        print(f"  cp -p {bak} {f}")
    print(f"  sudo systemctl kill -s HUP --kill-whom=main {GATEWAY}" + (f" && sudo systemctl restart {EDGE}" if edge_changed else ""))
    if code:
        print(f"flag_flip: FLIP NOT PROVED: {result[len('not proved: '):]}", file=sys.stderr)
        return code
    print(f"flag_flip: OK {key} flipped and proved at {stamp}.")

    if not a.no_smoke and smoke_on(app):
        print("flag_flip: the chat-turn smoke runs (task 4061).")
        if not run_smoke(app):
            print("flag_flip: SMOKE FAILED after the flip. Read the smoke output, and roll back when the flip caused it.",
                  file=sys.stderr)
            return 4
        print("flag_flip: the smoke passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
