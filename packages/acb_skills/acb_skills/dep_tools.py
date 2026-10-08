"""install_dependency — let an agent add a Python package to the shared agent
venv at runtime.

Agents run in-process in the gateway interpreter, so a package an agent needs
mid-task must be installed into the SAME venv the gateway runs from.  A bare
``pip install`` fails (the uv-created venv has no pip) and a bare ``uv pip
install`` has no target venv, so agents can't reliably self-install via shell.
This tool does it correctly: ``uv pip install --python <gateway venv>``.

Auto-injected into every agent (MAF and GitHub Copilot SDK) by the executor.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from acb_common import get_logger
from acb_common.child_env import child_env

_log = get_logger("acb_skills.dep_tools")

# A valid pip requirement token: a package name, optional [extras], optional
# version specifier.  Anything with shell metacharacters / flags is rejected.
_SPEC_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?"
    r"([<>=!~][^\s]*)?$"
)


def _find_uv() -> str | None:
    """Locate the ``uv`` binary even when it's not on the service PATH."""
    found = shutil.which("uv")
    if found:
        return found
    for cand in (
        Path.home() / ".local" / "bin" / "uv",
        Path("/usr/local/bin/uv"),
        Path("/root/.local/bin/uv"),
    ):
        try:
            if cand.is_file():
                return str(cand)
        except Exception:  # noqa: BLE001
            continue
    return None


async def install_dependency(packages: str) -> str:
    """Install one or more Python packages into the agent runtime so your
    imports/tools work.

    Call this when a task needs a package that isn't installed yet (you hit a
    ``ModuleNotFoundError`` or know you'll need one).  The package is installed
    into the shared agent venv and is importable immediately afterwards.

    Args:
        packages: Space- or comma-separated package specs — plain names with an
                  optional version, e.g. ``"pandas openpyxl"`` or
                  ``"requests==2.31.0"``.  Flags / URLs are not accepted.

    Returns:
        A short status string: what was installed, or the failure reason.
    """
    import asyncio  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    raw = [p.strip() for p in re.split(r"[\s,]+", packages or "") if p.strip()]
    specs = [p for p in raw if _SPEC_RE.match(p)]
    rejected = [p for p in raw if not _SPEC_RE.match(p)]
    if not specs:
        return (
            f"No valid package names in {packages!r}."
            + (f" Rejected: {rejected}." if rejected else "")
        )

    uv = _find_uv()
    if uv:
        cmd = [uv, "pip", "install", "--python", sys.executable, *specs]
    else:
        # Fallback — works only if the venv has pip; uv is the expected path.
        cmd = [sys.executable, "-m", "pip", "install", *specs]

    def _run() -> tuple[int, str]:
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600,
                env=child_env(),
            )
            return r.returncode, (r.stderr or r.stdout or "")
        except Exception as exc:  # noqa: BLE001
            return 1, str(exc)

    code, out = await asyncio.to_thread(_run)
    if code == 0:
        _log.info("dep_tools.installed", packages=specs)
        msg = f"Installed into the agent venv: {', '.join(specs)}."
    else:
        _log.warning(
            "dep_tools.install_failed", packages=specs, error=out[-500:],
        )
        msg = f"Failed to install {', '.join(specs)}: {out[-400:].strip()}"
    if rejected:
        msg += f" (ignored invalid specs: {rejected})"
    return msg
