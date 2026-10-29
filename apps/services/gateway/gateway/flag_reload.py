"""Reload the per-org flags of the gateway with no restart (2026-10-28).

A flag flip used to restart ``acb-gateway``. Each restart ends the chat runs
in flight, and members see "Gateway error 502" for 10 to 25 s. The flip at
02:46 UTC on 2026-10-28 was one. Now a SIGHUP to the gateway process reads
its env file again and applies each changed RELOADABLE flag in place::

    curl -s http://127.0.0.1:8080/version   # needs "flag_reload": true
    sudo systemctl kill -s HUP --kill-whom=main acb-gateway
    sudo journalctl -u acb-gateway -n 50 -o cat | grep flags.reload

⚠️ ``--kill-whom=main``. Without it, systemd sends the signal to EVERY process
of the unit, and a child process of an agent ends on SIGHUP.
⚠️ Check ``/version`` first. A gateway built before this module has no
handler, and SIGHUP then ends it like a crash. systemd starts it again, with
no drain.

What a reload may apply (:data:`RELOADABLE`): each ``*_orgs`` and ``*_modes``
field of ``Settings``. These are the per-org rollout lists, the usual flip.
Each reader calls ``get_settings()`` when it runs, and each parser caches on
the raw value (``_parse_orgs(raw)``). ``tests/unit/test_flag_reload.py``
fails when a module reads one at import, or when a start function of the
lifespan reads one. Any other changed key needs a restart, as before, and the
log names it. An allow list, not a deny list: the review of 2026-10-29 found
loop switches that the start reads once, values that modules copy at import,
and ``PGPASSWORD``, each of which a deny list let through.

What a reload does:

1. It reads the env file with python-dotenv, as pydantic-settings does.
2. It takes each key whose value differs from the last read.
3. For each reloadable key, it builds the new settings from the RUNNING
   settings plus the new values (a key that left the file takes its
   default). It never builds from the environment again, so no other key
   changes (``acb_common.settings.install_settings``).
4. If the new settings do not validate, nothing changes.
5. It writes the applied keys to ``os.environ`` too, for a child process.
6. It logs ``flags.reloaded`` with the NAMES of the applied keys and of the
   keys that need a restart. It never logs a value.
"""
from __future__ import annotations

import asyncio
import os
import re
import signal
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from acb_common import get_logger, get_settings
from acb_common.settings import Settings, install_settings
from dotenv import dotenv_values

_log = get_logger(__name__)

#: The env names that a reload may apply: each ``*_orgs`` and ``*_modes``
#: field of ``Settings``, in upper case.
RELOADABLE = frozenset(
    name.upper() for name in Settings.model_fields
    if re.fullmatch(r"[a-z0-9_]+_(orgs|modes)", name)
)

_state: dict[str, Any] = {"installed": False, "path": None, "loaded": {}, "at": None}


@dataclass(frozen=True)
class ReloadResult:
    """The names that a reload applied, and the names that need a restart.
    ``error`` names the exception when the new settings did not validate."""

    applied: tuple[str, ...] = ()
    restart: tuple[str, ...] = ()
    error: str | None = None


def reloadable(key: str) -> bool:
    """True when a reload may apply *key*."""
    return key.upper() in RELOADABLE


def env_path() -> Path:
    """The env file of ``Settings``, from the working directory."""
    return Path(str(Settings.model_config.get("env_file") or ".env")).resolve()


def _read(path: Path) -> dict[str, str]:
    """The keys of *path* that hold a value. A missing file holds none."""
    if not path.is_file():
        return {}
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def reload_flags(path: Path | None = None) -> ReloadResult:
    """Apply each changed, reloadable key of the env file. See the module."""
    path = path or _state["path"] or env_path()
    new = _read(path)
    old: dict[str, str] = _state["loaded"]
    changed = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
    applied = tuple(k for k in changed if reloadable(k))
    restart = tuple(k for k in changed if not reloadable(k))
    if not applied:
        return ReloadResult(restart=restart)
    current = get_settings()
    data = current.model_dump()
    for key in applied:
        field = key.lower()
        data[field] = new[key] if key in new else \
            Settings.model_fields[field].get_default(call_default_factory=True)
    try:
        rebuilt = Settings(**data)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - a bad value keeps the old settings
        return ReloadResult(restart=restart, error=type(exc).__name__)
    install_settings(rebuilt)
    for key in applied:
        if key in new:
            os.environ[key] = new[key]
        else:
            os.environ.pop(key, None)
    # Only the applied keys move on. A key that needs a restart still differs,
    # so the next reload names it again.
    loaded = dict(old)
    for key in applied:
        if key in new:
            loaded[key] = new[key]
        else:
            loaded.pop(key, None)
    _state["loaded"] = loaded
    _state["at"] = datetime.now(UTC).isoformat(timespec="seconds")
    return ReloadResult(applied=applied, restart=restart)


def _on_hup() -> None:
    try:
        res = reload_flags()
    except Exception as exc:  # noqa: BLE001 - a reload never ends the process
        _log.warning("flags.reload_failed", error=type(exc).__name__)
        return
    if res.error:
        _log.warning("flags.reload_refused", error=res.error, restart=list(res.restart))
    else:
        _log.info("flags.reloaded", applied=list(res.applied), restart=list(res.restart))


def install(loop: asyncio.AbstractEventLoop | None = None,
            path: Path | None = None) -> bool:
    """Read the env file once, and handle SIGHUP with :func:`reload_flags`.
    False where the platform has no SIGHUP (Windows) or no loop handler."""
    if not hasattr(signal, "SIGHUP"):
        return False
    loop = loop or asyncio.get_running_loop()
    _state["path"] = path or env_path()
    _state["loaded"] = _read(_state["path"])
    try:
        loop.add_signal_handler(signal.SIGHUP, _on_hup)
    except (NotImplementedError, RuntimeError, ValueError):
        return False
    _state["installed"] = True
    return True


def installed() -> bool:
    """True when SIGHUP reloads the flags of this process."""
    return bool(_state["installed"])


def last_reload() -> str | None:
    """When a reload last applied a flag (UTC, ISO), or None."""
    return _state["at"]
