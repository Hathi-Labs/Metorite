"""Agent installs go to agent-site, never into the shared venv (WS-49 BH-7).

Spec: ``project-docs/specs/box_hardening.md`` §5 BH-7 and BH-D10.

Before BH-7, ``loader._install_agent_deps`` and ``dep_tools.install_dependency``
installed into the gateway's own venv. The deploy then ran code from that venv
as ``acb``, with sudo (P5 variant 1). So both now install with ``uv pip install
--target`` into ``AGENT_SITE_DIR`` (``/var/lib/acb-gateway/agent-site``), and
this module is the ONE place that builds those commands.

* **Constraints (B7-1).** Each install passes ``-c`` with a freeze of the venv,
  so a package in agent-site never takes a version or an ABI that differs from
  the venv. The freeze needs ``--exclude-editable``: a plain freeze gives the
  ``-e file:///`` lines of the workspace packages, and uv refuses them as
  constraints. The file goes to ``/var/cache/acb-gateway/constraints.txt``.
  The venv does not change while the gateway runs, so one freeze per process
  is enough. A deploy that changes the venv also restarts the gateway.
* **Lookup (Q3a).** The gateway APPENDS agent-site to ``sys.path``, so a venv
  package always wins. A child gets ``PYTHONPATH`` by value from
  ``acb_common.child_env.AGENT_PATH_VALUES``. Nothing here writes a ``.pth``
  file or a ``sitecustomize``, and ``scrub()`` removes one that a package
  brings.
* **The guard.** ``not_ready()`` names the reason when the unit's
  ``StateDirectory`` or ``CacheDirectory`` is absent. Then nothing installs,
  and nothing falls back to the venv.
* **The venv first (fix round 1, P1).** ``--target`` does not see the venv. So
  a declared dependency that the venv already holds would resolve against
  PyPI, and an editable workspace member (``skill-projects``) answers 404
  there. ``split_provided()`` drops each dependency that the venv provides,
  before any uv call.
* **No shadow copies (fix round 1, P2).** ``--target`` also copies the
  dependencies of a package that the venv holds (``idna``, ``numpy``). A child
  puts ``PYTHONPATH`` before site-packages, so such a copy would shadow the
  venv after a venv bump. ``prune_venv_duplicates()`` removes each one, by
  its ``RECORD``, after every install and once for each new venv state.

The fence is ``tests/unit/test_agent_deps_target.py`` (BH-F6).
"""
from __future__ import annotations

import csv
import hashlib
import importlib
import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from acb_common import get_logger
from acb_common.child_env import AGENT_SITE_DIR, child_env

__all__ = [
    "AGENT_SITE",
    "CACHE_ROOT",
    "CONSTRAINTS",
    "STATE_ROOT",
    "constraints_digest",
    "ensure_on_sys_path",
    "find_uv",
    "freeze_command",
    "install_command",
    "not_ready",
    "prepare",
    "prune_venv_duplicates",
    "requirement_lines",
    "scrub",
    "split_provided",
    "venv_version",
]

_log = get_logger("acb_skills.agent_site")

#: The install target. ``StateDirectory=acb-gateway`` makes its parent.
AGENT_SITE = Path(AGENT_SITE_DIR)
STATE_ROOT = AGENT_SITE.parent
#: ``CacheDirectory=acb-gateway`` makes this dir. The freeze goes here.
CACHE_ROOT = Path("/var/cache/acb-gateway")
CONSTRAINTS = CACHE_ROOT / "constraints.txt"

#: Names that Python runs at start when they sit on ``sys.path`` or in a site
#: dir. ``scrub()`` removes each one from the top of agent-site.
_STARTUP_NAMES = ("sitecustomize", "usercustomize")

_lock = threading.Lock()
_frozen: dict[str, str] = {}
#: The constraints digests that this process pruned agent-site for.
_pruned_for: set[str] = set()

#: The project name at the start of a requirement line (PEP 508).
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")


def find_uv() -> str | None:
    """Locate the ``uv`` binary, even when it is not on the service PATH."""
    found = shutil.which("uv")
    if found:
        return found
    for cand in (
        Path.home() / ".local" / "bin" / "uv",
        Path("/usr/local/bin/uv"),
    ):
        try:
            if cand.is_file():
                return str(cand)
        except OSError:
            continue
    return None


def _writable_dir(p: Path) -> bool:
    try:
        return p.is_dir() and os.access(p, os.W_OK)
    except OSError:
        return False


def not_ready() -> str | None:
    """The reason an install cannot run, or ``None`` when it can.

    The two dirs come from the gateway unit (``40-agent-site.conf``). Without
    them, the install refuses. It never falls back to the shared venv.
    """
    if not _writable_dir(STATE_ROOT):
        return f"{STATE_ROOT} is absent or not writable (the gateway unit's StateDirectory)"
    if not _writable_dir(CACHE_ROOT):
        return f"{CACHE_ROOT} is absent or not writable (the gateway unit's CacheDirectory)"
    return None


def ensure_on_sys_path() -> None:
    """Append agent-site to ``sys.path``, after the venv, once.

    Only an append. ``site.addsitedir`` would run each ``.pth`` file in the
    dir, and a ``PYTHONPATH`` value would go BEFORE the venv.
    """
    entry = str(AGENT_SITE)
    if entry not in sys.path:
        sys.path.append(entry)


def freeze_command(uv: str) -> list[str]:
    """The freeze of the venv that becomes the constraints file (B7-1)."""
    return [uv, "pip", "freeze", "--exclude-editable", "--python", sys.executable]


def _run(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    r = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, env=child_env(),
    )
    return r.returncode, r.stdout or "", r.stderr or ""


def constraints_digest(uv: str) -> str:
    """Write the constraints file, once per process, and return its SHA-256.

    Raises ``RuntimeError`` when the freeze fails, so the caller installs
    nothing rather than install with no constraints.
    """
    with _lock:
        cached = _frozen.get(uv)
        if cached is not None and CONSTRAINTS.is_file():
            return cached
        code, out, err = _run(freeze_command(uv), timeout=120)
        if code != 0:
            raise RuntimeError(f"uv pip freeze failed: {(err or out)[-400:]}")
        # A line that starts with "-" is an option, not a pin. uv refuses an
        # editable line as a constraint, so drop any that slipped through.
        lines = [ln.strip() for ln in out.splitlines()]
        pins = [ln for ln in lines if ln and not ln.startswith(("-", "#"))]
        text = "\n".join(pins) + "\n"
        tmp = CONSTRAINTS.with_name(CONSTRAINTS.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, CONSTRAINTS)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        _frozen[uv] = digest
        return digest


def prepare(uv: str) -> str:
    """Make agent-site and the constraints file. Returns the constraints
    digest. Call it only after ``not_ready()`` returned ``None``.

    On each new venv state (a new digest), it prunes from agent-site every
    distribution that the venv also holds (P2)."""
    AGENT_SITE.mkdir(exist_ok=True)
    digest = constraints_digest(uv)
    if digest not in _pruned_for:
        prune_venv_duplicates()
        _pruned_for.add(digest)
    return digest


# ── What the venv already provides (fix round 1, P1 and P2) ─────────────────


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(spec: str) -> str | None:
    """The project name of a requirement line, or ``None`` for an option line
    (``-r``, ``--index-url``) or a line that names no project."""
    line = spec.strip()
    if not line or line.startswith(("-", "#")):
        return None
    m = _NAME_RE.match(line)
    return m.group(1) if m else None


def requirement_lines(text: str) -> list[str]:
    """The requirement lines of a ``requirements.txt``, with no comment."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _venv_paths() -> list[str]:
    """``sys.path`` of the gateway's interpreter, WITHOUT agent-site. A copy in
    agent-site must never count as a venv package, or the prune would remove
    what the agent installed."""
    site = os.path.normcase(os.path.abspath(str(AGENT_SITE)))
    return [
        p for p in sys.path
        if p and os.path.normcase(os.path.abspath(p)) != site
    ]


def venv_version(name: str) -> str | None:
    """The version of ``name`` in the gateway's interpreter, or ``None``.

    The lookup is ``importlib.metadata`` over ``_venv_paths()``, with the name
    normalised. An editable workspace member has a ``.dist-info`` in
    site-packages, so it counts as provided.
    """
    want = _canonical(name)
    try:
        for dist in importlib.metadata.distributions(name=want, path=_venv_paths()):
            meta_name = dist.metadata["Name"] if dist.metadata else None
            if meta_name is None or _canonical(meta_name) == want:
                return dist.version
    except Exception as exc:  # a broken dist must not stop a load
        _log.warning("agent_site.venv_lookup_failed", name=name, error=str(exc))
    return None


def split_provided(specs: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """Split ``specs`` into the ones to install and the ones the venv provides.

    Returns ``(to_install, provided)``. ``provided`` holds ``(spec, version)``.
    An option line stays in ``to_install``.
    """
    to_install: list[str] = []
    provided: list[tuple[str, str]] = []
    for spec in specs:
        name = requirement_name(spec)
        version = venv_version(name) if name else None
        if version is None:
            to_install.append(spec)
        else:
            provided.append((spec, version))
    return to_install, provided


def _dist_name(info: Path) -> str | None:
    try:
        for line in (info / "METADATA").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Name:"):
                return line[5:].strip()
            if not line:
                break
    except OSError:
        pass
    stem = info.name[: -len(".dist-info")]
    return stem.rsplit("-", 1)[0] or None


def _inside(root: str, p: str) -> bool:
    return p != root and p.startswith(root + os.sep)


def _drop_empty_dirs(dirs: set[str], root: str) -> None:
    """Remove each dir that the removal left empty, up to agent-site. A dir
    that holds only ``__pycache__`` counts as empty."""
    for d in sorted(dirs, key=len, reverse=True):
        while _inside(root, d):
            try:
                names = os.listdir(d)
            except OSError:
                break
            if names == ["__pycache__"]:
                shutil.rmtree(os.path.join(d, "__pycache__"), ignore_errors=True)
                names = []
            if names:
                break
            try:
                os.rmdir(d)
            except OSError:
                break
            d = os.path.dirname(d)


def _remove_dist(info: Path) -> None:
    """Remove one distribution from agent-site: each file of its ``RECORD``
    inside agent-site, then its ``.dist-info``. Shared dirs (a namespace
    package) keep the files of other distributions."""
    root = os.path.normpath(os.path.abspath(str(AGENT_SITE)))
    dirs: set[str] = set()
    record = info / "RECORD"
    rows: list[list[str]] = []
    try:
        rows = list(csv.reader(record.read_text(encoding="utf-8", errors="replace").splitlines()))
    except OSError:
        rows = []
    if rows:
        for row in rows:
            if not row or not row[0]:
                continue
            path = os.path.normpath(os.path.join(root, row[0]))
            if not _inside(root, path) or path.startswith(str(info)):
                continue
            try:
                if os.path.isfile(path) or os.path.islink(path):
                    os.unlink(path)
                    dirs.add(os.path.dirname(path))
            except OSError as exc:
                _log.warning("agent_site.prune_file_failed", path=row[0], error=str(exc))
    else:
        try:
            tops = (info / "top_level.txt").read_text(encoding="utf-8").split()
        except OSError:
            tops = []
        for top in tops:
            for cand in (os.path.join(root, top), os.path.join(root, top + ".py")):
                if not _inside(root, os.path.normpath(cand)):
                    continue
                if os.path.isdir(cand) and not os.path.islink(cand):
                    shutil.rmtree(cand, ignore_errors=True)
                elif os.path.exists(cand):
                    os.unlink(cand)
    shutil.rmtree(info, ignore_errors=True)
    _drop_empty_dirs(dirs, root)


def prune_venv_duplicates() -> list[str]:
    """Remove from agent-site each distribution that the venv also holds.

    A child takes ``PYTHONPATH`` before site-packages, so a copy here would
    shadow the venv's version. With the copy gone, the child imports the
    venv's own. Returns the pruned names.
    """
    removed: list[str] = []
    try:
        infos = sorted(AGENT_SITE.glob("*.dist-info"))
    except OSError:
        infos = []
    for info in infos:
        name = _dist_name(info)
        if not name or venv_version(name) is None:
            continue
        _remove_dist(info)
        removed.append(name)
    if removed:
        _log.info("agent_site.pruned_venv_duplicates", names=removed)
        importlib.invalidate_caches()
    return removed


def install_command(uv: str, args: list[str], *, only_binary: bool) -> list[str]:
    """One install into agent-site, with the venv's constraints.

    ``--python`` picks the interpreter for the wheel tags. ``--target`` keeps
    the install out of that interpreter's venv.
    """
    cmd = [
        uv, "pip", "install",
        "--python", sys.executable,
        "--target", str(AGENT_SITE),
        "-c", str(CONSTRAINTS),
    ]
    if only_binary:
        cmd += ["--only-binary", ":all:"]
    return cmd + list(args)


def scrub() -> list[str]:
    """Remove each top-level ``.pth`` file and each start-up module from
    agent-site, and make new packages importable in this process.

    A ``.pth`` file does nothing on ``sys.path`` or ``PYTHONPATH``. A
    ``sitecustomize`` on ``PYTHONPATH`` runs at the start of every child
    Python. Neither one belongs in agent-site. Returns the removed names.
    """
    removed: list[str] = []
    try:
        entries = list(AGENT_SITE.iterdir())
    except OSError:
        entries = []
    for p in entries:
        stem = p.name.split(".", 1)[0]
        if p.name.endswith(".pth") or stem in _STARTUP_NAMES:
            try:
                if p.is_dir() and not p.is_symlink():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                removed.append(p.name)
            except OSError as exc:
                _log.warning("agent_site.scrub_failed", name=p.name, error=str(exc))
    if removed:
        _log.warning("agent_site.scrubbed", names=removed)
    importlib.invalidate_caches()
    return removed
