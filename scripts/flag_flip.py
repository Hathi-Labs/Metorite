#!/usr/bin/env python3
"""One guarded flip of a per-org flag on the production box (task 4086).

Run it on the box as ``acb``, through ``scripts/flag_flip.sh``. Examples::

    bash scripts/flag_flip.sh TOOL_PACKS_ORGS --add <org id> --task 4086
    bash scripts/flag_flip.sh DECIDE_FEATURE_MODES --add email.cold_check=shadow --task 4086
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
   answer on the task. The tool records the id and cannot check it. ``--off``
   and ``--remove`` need no id, because closing a flag is the safe way.
3. The new value must be empty, ``*``, or org ids. A ``*_MODES`` value is
   ``<name>=<mode>`` pairs (see :func:`parse`). A flag in
   :data:`EMPTY_MEANS_ALL` is never set empty.
4. The value must be the same in each env file that holds the key, so
   ``--add`` and ``--remove`` start from one known value.
5. A key in an env file of a unit that the tool does not restart (the
   consoles) is refused.
6. The gateway must report ``flag_reload: true`` on ``/version``. A gateway
   with no reload handler ends on SIGHUP.

**The flip.** It takes the DEPLOY lock (``/opt/acb/acb-deploy.lock``), because a
release writes ``.env`` too. It backs up each env file that it changes, and it
writes each one IN PLACE, so the file keeps its inode: the gateway unit binds
the inode of the root ``.env`` (``50-hardening.conf``). The root ``.env``
always holds the key. A flag in :data:`EDGE_FLAGS` also goes into the edge env
file, because the edge reads only that file. It sends SIGHUP to the gateway
main process only, and it waits for the ``flags.reloaded`` line that names the
key. It restarts acb-agent-edge when the edge env file changed, and then checks
the new process env.

**The record.** It appends one line to ``/opt/acb/acb-flag-flips.log``, outside
the checkout, and it prints the evidence and the rollback commands. When the
chat-turn smoke is on (``SMOKE_CHAT_TURN=1``, task 4061), it runs that smoke
after the flip.

Exit codes: 0 the files and the services hold the value, 2 refused before
any write, 3 a write failed (the tool puts the files back) or the flip is not
proved (the files keep the new value, and the output names the rollback), 4
the smoke failed after a proved flip.

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
EDGE_ENV = "apps/services/agent_edge/.env"
#: The per-org flags that acb-agent-edge reads. The edge reads only its own
#: env file, so a flip of one of these writes that file too and restarts the
#: edge. Fence: test_flag_flip.py scans the edge source for registry names.
EDGE_FLAGS = frozenset({
    "AGENT_ACCESS_ORGS", "AGENT_PROTOCOL_ORGS", "AGENT_RESOURCES_ORGS",
    "MCP_IMAGE_UPLOAD_ORGS", "PLAYBOOK_ORGS", "SEQUENCE_MARKS_ORGS",
})
#: The env files whose units this tool does not restart. A key in one of them
#: is refused, so the tool never reports a flip that a unit does not apply.
UNMANAGED_ENVS = {
    "apps/services/customer_console/.env": "acb-customer-console",
    "workbench/control_plane/.env.local": "acb-workbench",
    "workbench/operator_console/.env.local": "acb-operator-console",
}
#: The flags whose EMPTY value means every org (the reader returns
#: ``not orgs or org in orgs``). For these, --off and a --remove that empties
#: the value turn the feature ON for every org, so the tool refuses both.
#: Fence: test_flag_flip.py reads the reader.
EMPTY_MEANS_ALL = frozenset({"ASSISTANT_PROFILE_ORGS"})
#: How long a flip waits for a deploy that holds the deploy lock.
LOCK_WAIT_S = float(os.environ.get("FLAG_FLIP_LOCK_WAIT_S", "120"))
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
#: A name of a ``*_MODES`` pair: ``*``, an org id, a feature or a kind.
MODE_NAME_RE = re.compile(r"\*|[a-z0-9][a-z0-9_.-]{0,80}")
KEY_RE = re.compile(r"[A-Z][A-Z0-9_]{0,80}")
ANSWER_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


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
    """The items of a value. Raises Refused.

    A ``*_ORGS`` value is ``*`` alone, or org ids. A ``*_MODES`` value is
    ``<name>=<mode>`` pairs. The name is an org id or ``*``
    (``AGENT_CLAIM_SCOPE_MODES``), a feature (``DECIDE_FEATURE_MODES``:
    ``email.cold_check=shadow``) or a kind (``PROACTIVE_KIND_MODES``). Each
    reader drops a name that it does not know, so this grammar checks the
    shape, and the reader's log names an unknown name.
    """
    raw = raw.strip().strip('"').strip("'").strip()
    if raw == "":
        return []
    items = [i.strip() for i in raw.split(",") if i.strip()]
    for item in items:
        if _is_modes(key):
            name, sep, mode = item.partition("=")
            if not sep or not MODE_NAME_RE.fullmatch(name) or not MODE_RE.fullmatch(mode):
                raise Refused(f"{key}: {item!r} is not <name>=<mode>")
        elif not (item == "*" or ORG_RE.fullmatch(item)):
            raise Refused(f"{key}: {item!r} is not an org id or *")
    if not _is_modes(key) and "*" in items and len(items) > 1:
        raise Refused(f"{key}: * with org ids is ambiguous. Use * alone.")
    names = [i.partition("=")[0] for i in items]
    if len(set(names)) != len(names):
        raise Refused(f"{key}: a name is given twice")
    return items


def _one_item(key: str, arg: str | None) -> str:
    """The one normalised item of ``--add`` or ``--remove``."""
    items = parse(key, arg or "")
    if len(items) != 1:
        raise Refused(f"{key}: --add and --remove take one item. Use --set for a list.")
    return items[0]


def new_items(key: str, cur: list[str], op: str, arg: str | None) -> list[str]:
    """The items after one operation. Raises Refused for a vague operation."""
    if op == "off":
        return []
    if op == "set":
        return parse(key, arg or "")
    if op == "add":
        item = _one_item(key, arg)
        if not _is_modes(key) and item == "*":
            return ["*"]
        if not _is_modes(key) and "*" in cur:
            raise Refused(f"{key} is already * (every org)")
        name = item.partition("=")[0]
        kept = [i for i in cur if i.partition("=")[0] != name]
        return [*kept, item]
    if op == "remove":
        name = (arg or "").strip()
        if not (MODE_NAME_RE.fullmatch(name) if _is_modes(key) else (name == "*" or ORG_RE.fullmatch(name))):
            raise Refused(f"{key}: {name!r} is not a name that the value can hold")
        if not _is_modes(key) and "*" in cur and name != "*":
            raise Refused(f"{key} is * (every org). Name the orgs with --set.")
        kept = [i for i in cur if i.partition("=")[0] != name]
        if kept == cur:
            raise Refused(f"{key} does not name {name}")
        return kept
    raise Refused(f"unknown operation {op}")


# ── Env files ───────────────────────────────────────────────────────────────


#: A line that sets *key*, in each form that python-dotenv reads.
def _key_line(key: str) -> re.Pattern[str]:
    return re.compile(rf"^[ \t]*(?:export[ \t]+)?{re.escape(key)}[ \t]*=")


def read_key(path: Path, key: str) -> str | None:
    """The value of *key* as python-dotenv reads it (the gateway's reader). None when absent."""
    from dotenv import dotenv_values

    if not any(_key_line(key).match(ln) for ln in path.read_text(encoding="utf-8").splitlines()):
        return None
    val = dotenv_values(path).get(key)
    return (val or "").strip()


def rewrite(path: Path, key: str, value: str) -> bytes:
    """The new bytes of *path*: one ``KEY=value`` line, every other byte kept.

    Each line that sets *key* (also ``export KEY=`` and ``KEY = v``) goes. The
    first one becomes ``KEY=value``, so one line holds the key.
    """
    pat = _key_line(key)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    out: list[str] = []
    done = False
    inside = False  # inside a multi-line double-quoted value, such as a PEM
    for line in lines:
        quotes = len(re.findall(r'(?<!\\)"', line))
        if inside:
            out.append(line)
            inside = quotes % 2 == 0
            continue
        if re.match(r'^[ \t]*(?:export[ \t]+)?[A-Za-z_][A-Za-z0-9_]*[ \t]*=[ \t]*"', line) and quotes % 2 == 1:
            out.append(line)
            inside = True
            continue
        if pat.match(line):
            if not done:
                nl = "\n" if line.endswith("\n") else ""
                out.append(f"{key}={value}{nl}")
                done = True
            continue
        out.append(line)
    if not done:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(f"{key}={value}\n")
    return "".join(out).encode("utf-8")


def write_in_place(path: Path, data: bytes) -> None:
    """Write *data* INTO *path*, so the file keeps its inode.

    The gateway unit binds the inode that the root ``.env`` has at start
    (``50-hardening.conf``). A rename leaves the gateway on a dead copy, so
    this writes into the same file, as ``env_edit_in_place`` in
    ``vps_apply.sh`` does. A full copy goes first to a temp file beside it. When
    the read-back differs, the copy stays and the write fails.
    """
    # The full copy holds secrets, so it goes beside the deploy lock, outside
    # the checkout, at mode 600.
    fd, tmp = tempfile.mkstemp(prefix="acb-flagflip-", dir=str(APP_DIR.parent))
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    with open(path, "r+b") as fh:
        fh.seek(0)
        fh.write(data)
        fh.truncate()
        fh.flush()
        os.fsync(fh.fileno())
    if path.read_bytes() != data:
        raise RuntimeError(f"the write of {path} failed. The full copy is at {tmp}")
    os.unlink(tmp)


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


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


APPLIED_RE = re.compile(r"applied=\[([^\]]*)\]")


def journal_cursor() -> str:
    """The cursor of the newest gateway journal entry, before the SIGHUP."""
    out = run(["sudo", "journalctl", "-u", GATEWAY, "-n", "0", "--show-cursor", "--no-pager"]).stdout
    for line in out.splitlines():
        if line.startswith("-- cursor: "):
            return line[len("-- cursor: "):].strip()
    raise RuntimeError("journalctl gave no cursor")


def reload_gateway(key: str, cursor: str, expect_applied: bool = True) -> str:
    """SIGHUP the gateway main process and prove the reload. Returns the line.

    It reads only the entries AFTER *cursor*, so an older line never proves
    this flip. A ``flags.reload_refused`` or ``flags.reload_failed`` line
    there fails the flip. When the root .env changed (*expect_applied*), the
    proof is a ``flags.reloaded`` line whose ``applied=[...]`` names *key*.
    When it did not change, any ``flags.reloaded`` line proves it: the reload
    compares the file with the values that the gateway runs, so a line that
    does not name the key says that the gateway already runs the file value.
    """
    res = run(["sudo", "systemctl", "kill", "-s", "HUP", "--kill-whom=main", GATEWAY])
    if res.returncode != 0:
        raise RuntimeError("the SIGHUP to the gateway failed")
    deadline = time.monotonic() + RELOAD_WAIT_S
    while True:
        log = run(["sudo", "journalctl", "-u", GATEWAY, f"--after-cursor={cursor}", "-o", "cat", "--no-pager"])
        lines = [ANSI_RE.sub("", ln).strip() for ln in log.stdout.splitlines()]
        for line in lines:
            if "flags.reload_refused" in line or "flags.reload_failed" in line:
                raise RuntimeError(f"the gateway refused the reload: {line[:300]}")
        for line in lines:
            m = APPLIED_RE.search(line) if "flags.reloaded" in line else None
            if m and (not expect_applied or re.search(rf"['\"]{key}['\"]", m.group(1), re.IGNORECASE)):
                return line[:300]
        if time.monotonic() >= deadline:
            raise RuntimeError(f"no flags.reloaded line applies {key} after {RELOAD_WAIT_S:.0f} s")
        time.sleep(1)


def _edge_env() -> tuple[str, list[str]]:
    pid = run(["systemctl", "show", "-p", "MainPID", "--value", EDGE]).stdout.strip()
    return pid, run(["sudo", "cat", f"/proc/{pid}/environ"]).stdout.split("\0")


def sync_edge(key: str, value: str) -> str:
    """Make the edge process run *value*: restart it when its env differs, then prove it."""
    pid, env = _edge_env()
    if f"{key}={value}" in env:
        return f"acb-agent-edge pid {pid} already runs {key}={value!r}"
    if run(["sudo", "systemctl", "restart", EDGE], timeout=120).returncode != 0:
        raise RuntimeError("the restart of acb-agent-edge failed")
    time.sleep(3)
    if run(["systemctl", "is-active", EDGE]).stdout.strip() != "active":
        raise RuntimeError("acb-agent-edge is not active after the restart")
    pid, env = _edge_env()
    if f"{key}={value}" not in env:
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
    # --off and --remove only close a flag, the fail-safe way, so they need no
    # answer. The answer id is recorded, not checked: the box has no Metorite
    # token.
    closing = a.off or a.remove is not None
    if key in owner_only and not closing and not (a.owner_approved and ANSWER_RE.fullmatch(a.owner_approved)):
        raise Refused(f"{key} is owner-only (money, identity or a third party). Pass --owner-approved <answer id>")

    app = APP_DIR
    root = app / ROOT_ENV
    # The deploy lock: a release (vps_pull, vps_apply) also writes .env. While
    # a flip holds it, the deploy timer finds it busy and retries.
    lock = Path(os.environ.get("FLAG_FLIP_DEPLOY_LOCK", str(app.parent / "acb-deploy.lock")))
    if not lock.exists():
        lock.touch(mode=0o644)
    lock_fd = os.open(lock, os.O_RDONLY)
    try:
        deadline = time.monotonic() + LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Refused(f"a deploy or another flip holds {lock} after {LOCK_WAIT_S:.0f} s") from None
                time.sleep(2)
        code = _flip_locked(a, key, app, root)
    finally:
        os.close(lock_fd)
    # The smoke takes a shared hold of the deploy lock (smoke_chat.sh), so it
    # runs only after this flip let the lock go.
    if code == 0 and getattr(a, "_flipped", False):
        return _smoke_after(a, app)
    return code


def target_files(app: Path, key: str) -> list[Path]:
    """The env files that the flip writes: the root .env, and the edge's for an edge flag."""
    for rel, unit in UNMANAGED_ENVS.items():
        if (app / rel).is_file() and read_key(app / rel, key) is not None:
            raise Refused(f"{rel} holds {key}, and this tool does not restart {unit}")
    edge = app / EDGE_ENV
    edge_held = edge.is_file() and read_key(edge, key) is not None
    if key in EDGE_FLAGS and not edge.is_file():
        raise Refused(f"{key} is read by the edge, and {EDGE_ENV} is missing")
    return [app / ROOT_ENV] + ([edge] if key in EDGE_FLAGS or edge_held else [])


def current_value(app: Path, key: str, files: list[Path], allow_drift: bool = False) -> str:
    """The one value of *key* over the *files* that hold it. Empty when none does."""
    values = {str(f.relative_to(app)): read_key(f, key) for f in files}
    seen = {v for v in values.values() if v is not None}
    if len(seen) > 1:
        if not allow_drift:
            raise Refused(f"{key} differs between env files {values}. Fix it with --set")
        print(f"flag_flip: {key} differs between env files {values}. --set makes them one.")
        return ""
    return seen.pop() if seen else ""


def _flip_locked(a: argparse.Namespace, key: str, app: Path, root: Path) -> int:
    files = target_files(app, key)
    edge = app / EDGE_ENV
    old_raw = current_value(app, key, files, allow_drift=a.set is not None)
    cur = parse(key, old_raw)
    operation = "off" if a.off else "set" if a.set is not None else "add" if a.add is not None else "remove"
    new = new_items(key, cur, operation, a.set if a.set is not None else a.add if a.add is not None else a.remove)
    new_raw = ",".join(new)
    if key in EMPTY_MEANS_ALL and not new and any(read_key(f, key) for f in files):
        raise Refused(f"{key}: empty means EVERY org for this flag, so this would turn it on everywhere")
    # Only a file whose bytes change is backed up and written. When no file
    # changes, the tool still makes the services run the file value (a hand
    # edit with no reload, or a value that only one file lacked).
    plan = {f: rewrite(f, key, new_raw) for f in files}
    changed = [f for f in files if plan[f] != f.read_bytes()]
    if not gateway_reloads():
        raise Refused("the gateway does not report flag_reload: true on /version")

    print(f"flag_flip: {key}: {old_raw!r} -> {new_raw!r}")
    print("flag_flip: files that change: " + (", ".join(str(f.relative_to(app)) for f in changed) or "none"))
    if a.dry_run:
        print("flag_flip: dry run. Nothing was written.")
        return 0

    try:
        cursor = journal_cursor()
    except RuntimeError as exc:
        raise Refused(str(exc)) from None
    t0 = time.time()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(t0))
    baks: dict[Path, Path] = {}
    try:
        for f in changed:
            baks[f] = backup(f, stamp, a.task)
    except OSError as exc:
        for bak in baks.values():
            bak.unlink(missing_ok=True)
        raise Refused(f"the backup failed ({exc})") from None
    try:
        for f in changed:
            write_in_place(f, plan[f])
    except (OSError, RuntimeError) as exc:
        print(f"flag_flip: WRITE FAILED: {exc}. The tool puts each file back from its backup.", file=sys.stderr)
        restored = True
        for f, bak in baks.items():
            try:
                write_in_place(f, bak.read_bytes())
            except (OSError, RuntimeError):
                restored = False
                print(f"  restore by hand: cat {bak} > {f}", file=sys.stderr)
        _record(app, t0, a, key, old_raw, new_raw, changed, "write failed" + ("" if restored else ", not restored"))
        return 3

    edge_used = edge in files
    evidence: list[str] = []
    result = "proved"
    code = 0
    try:
        evidence.append("gateway: " + reload_gateway(key, cursor, expect_applied=root in changed))
        if edge_used:
            evidence.append(sync_edge(key, new_raw))
    except RuntimeError as exc:
        result, code = f"not proved: {exc}", 3

    _record(app, t0, a, key, old_raw, new_raw, changed, result)
    _print_report(evidence, baks, edge_used)
    if code:
        print(f"flag_flip: FLIP NOT PROVED: {result[len('not proved: '):]}. The files keep the new value.",
              file=sys.stderr)
        return code
    print(f"flag_flip: OK {key} is {new_raw!r} in the files and the services, proved at {stamp}.")
    a._flipped = True
    return 0


def _record(app: Path, t0: float, a: argparse.Namespace, key: str, old_raw: str, new_raw: str,
            files: list[Path], result: str) -> None:
    """Append one audit line, outside the checkout."""
    audit = "\t".join([
        dt.datetime.fromtimestamp(t0, dt.UTC).isoformat(timespec="seconds"), a.task, key, old_raw or "-",
        new_raw or "-", ",".join(str(f.relative_to(app)) for f in files), result.split(":")[0],
        a.owner_approved or "-",
    ])
    audit_log = Path(os.environ.get("FLAG_FLIP_AUDIT_LOG", str(app.parent / "acb-flag-flips.log")))
    with open(audit_log, "a", encoding="utf-8") as fh:
        fh.write(audit + "\n")


def _print_report(evidence: list[str], baks: dict[Path, Path], edge_changed: bool) -> None:
    for line in evidence:
        print(f"flag_flip: evidence: {line}")
    if ROOT_ENV_OTHERS:
        print(f"flag_flip: {', '.join(ROOT_ENV_OTHERS)} read the root .env at start. They apply it at their next restart.")
    print("flag_flip: rollback:")
    for f, bak in baks.items():
        print(f"  cat {bak} > {f}")
    print(f"  sudo systemctl kill -s HUP --kill-whom=main {GATEWAY}" + (f" && sudo systemctl restart {EDGE}" if edge_changed else ""))


def _smoke_after(a: argparse.Namespace, app: Path) -> int:
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
