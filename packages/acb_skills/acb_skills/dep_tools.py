"""install_dependency — let an agent add a Python package at runtime.

Agents run in-process in the gateway interpreter, so a package an agent needs
mid-task must be importable there. WS-49 BH-7 (BH-D10): the package goes to
agent-site (``/var/lib/acb-gateway/agent-site``) with ``uv pip install
--target``, and NEVER into the shared venv, because the deploy runs code from
the venv as a user with sudo. ``acb_skills.agent_site`` builds the command,
with the venv's constraints. When the unit's dirs are absent, the tool refuses
and installs nothing.

Auto-injected into every agent (MAF and GitHub Copilot SDK) by the executor.
"""
from __future__ import annotations

import re

from acb_common import get_logger
from acb_common.child_env import child_env

from acb_skills import agent_site

_log = get_logger("acb_skills.dep_tools")

# A valid pip requirement token: a package name, optional [extras], optional
# version specifier.  Anything with shell metacharacters / flags is rejected.
_SPEC_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?"
    r"([<>=!~][^\s]*)?$"
)


async def install_dependency(packages: str) -> str:
    """Install one or more Python packages into the agent runtime so your
    imports/tools work.

    Call this when a task needs a package that isn't installed yet (you hit a
    ``ModuleNotFoundError`` or know you'll need one).  The package is installed
    into the agent-site dir and is importable immediately afterwards.

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

    # The guard (spec BH-7): with no agent-site, refuse. Never the venv.
    reason = agent_site.not_ready()
    uv = agent_site.find_uv()
    if reason is None and not uv:
        reason = "uv is not on this box"
    if reason is not None or uv is None:
        _log.warning("dep_tools.install_refused", packages=specs, error=reason)
        return (
            f"Refused to install {', '.join(specs)}: the agent package dir is "
            f"not ready ({reason}). Nothing was installed."
        )

    uv_bin: str = uv

    def _run() -> tuple[int, str]:
        try:
            agent_site.prepare(uv_bin)
            cmd = agent_site.install_command(uv_bin, specs, only_binary=False)
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600,
                env=child_env(),
            )
            agent_site.scrub()
            return r.returncode, (r.stderr or r.stdout or "")
        except Exception as exc:  # noqa: BLE001
            return 1, str(exc)

    agent_site.ensure_on_sys_path()
    code, out = await asyncio.to_thread(_run)
    if code == 0:
        _log.info("dep_tools.installed", packages=specs, target=str(agent_site.AGENT_SITE))
        msg = f"Installed into the agent package dir: {', '.join(specs)}."
    else:
        _log.warning(
            "dep_tools.install_failed", packages=specs, error=out[-500:],
        )
        msg = f"Failed to install {', '.join(specs)}: {out[-400:].strip()}"
    if rejected:
        msg += f" (ignored invalid specs: {rejected})"
    return msg
