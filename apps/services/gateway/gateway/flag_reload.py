"""Reload the feature flags of the gateway with no restart (2026-10-28).

A flag flip used to restart ``acb-gateway``. Each restart ends the chat runs
in flight, and members see "Gateway error 502" for 10 to 25 s. The flip at
02:46 UTC on 2026-10-28 was one. Now a SIGHUP to the gateway process reads
its env file again and applies each changed flag in place::

    curl -s http://127.0.0.1:8080/version   # needs "flag_reload": true
    sudo systemctl kill -s HUP --kill-whom=main acb-gateway
    sudo journalctl -u acb-gateway -n 50 -o cat | grep flags.reload

⚠️ ``--kill-whom=main``. Without it, systemd sends the signal to EVERY process
of the unit, and a child process of an agent ends on SIGHUP.
⚠️ Check ``/version`` first. A gateway built before this module has no
handler, and SIGHUP then ends it like a crash. systemd starts it again, with
no drain.

What a reload does:

1. It reads the env file with python-dotenv, as pydantic-settings does.
2. It takes each key whose value differs from the last read. A key that left
   the file leaves the environment too, so its default holds again.
3. It applies only a reloadable key (:func:`reloadable`) to ``os.environ``.
4. It builds a new ``Settings``. If that fails, it puts each value back and
   keeps the running settings, so a bad value never breaks a request.
5. It clears the cache of ``get_settings``.
6. It logs ``flags.reloaded`` with the NAMES of the applied keys and of the
   keys that need a restart. It never logs a value.

Why this is enough: each flag reader calls ``get_settings()`` or reads
``os.environ`` when it runs, and each flag parser caches on the raw value
(``_parse_orgs(raw)``). No module keeps a flag from its import.

A key that is not reloadable keeps its running value until a restart:

- a name with a secret or infrastructure word (:data:`DENY_WORDS`), such as a
  key, a token, a URL or a port;
- a name with a prefix that the start reads once (:data:`RESTART_PREFIXES`),
  such as the database pool;
- a key read once at start (:data:`RESTART_ONLY`), such as the switches of
  the background loops.

Fence: ``tests/unit/test_flag_reload.py``.
"""
from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from acb_common import get_logger, get_settings
from acb_common.settings import Settings
from dotenv import dotenv_values

_log = get_logger(__name__)

#: A word of the name (split on ``_``) that marks a secret or a value that
#: the start reads once. Such a key needs a restart, as before.
DENY_WORDS = frozenset({
    "KEY", "KEYS", "TOKEN", "TOKENS", "SECRET", "SECRETS", "PASSWORD",
    "PASSWD", "PW", "DSN", "URL", "URLS", "URI", "HOST", "HOSTS", "PORT",
    "PATH", "DIR", "CERT", "CERTS", "CREDENTIALS", "CREDS", "PRIVATE", "SALT",
    "ID", "IDS", "WEBHOOK", "AUTH",
})

#: A prefix whose settings the start reads once: the engine and its pools,
#: the Redis client, the browser build, the interpreter.
RESTART_PREFIXES = ("DB_", "REDIS_", "NEXT_PUBLIC_", "PYTHON")

#: The keys that the start reads once. ``EMAIL_SYNC_ENABLED`` and
#: ``WORKFLOW_SCHEDULER_ENABLED`` decide whether a background loop starts
#: (``main.py``, the lifespan), so a reload cannot start or stop the loop.
RESTART_ONLY = frozenset({
    "EMAIL_SYNC_ENABLED", "WORKFLOW_SCHEDULER_ENABLED", "LOG_LEVEL",
    "ACB_ENV", "DEPLOY_ENV",
})

_state: dict[str, Any] = {"installed": False, "path": None, "loaded": {}, "at": None}


@dataclass(frozen=True)
class ReloadResult:
    """The names that a reload applied, and the names that need a restart.
    ``error`` names the exception when the new settings did not load."""

    applied: tuple[str, ...] = ()
    restart: tuple[str, ...] = ()
    error: str | None = None


def reloadable(key: str) -> bool:
    """True when a reload may apply *key*."""
    name = key.upper()
    if name in RESTART_ONLY or name.startswith(RESTART_PREFIXES):
        return False
    return not set(name.split("_")) & DENY_WORDS


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
    applied = [k for k in changed if reloadable(k)]
    restart = tuple(k for k in changed if not reloadable(k))
    before = {k: os.environ.get(k) for k in applied}
    for k in applied:
        if k in new:
            os.environ[k] = new[k]
        else:
            os.environ.pop(k, None)
    try:
        Settings()  # type: ignore[call-arg]
    except Exception as exc:  # noqa: BLE001 - a bad value keeps the old settings
        for k, value in before.items():
            if value is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = value
        return ReloadResult(restart=restart, error=type(exc).__name__)
    get_settings.cache_clear()
    # Only the applied keys move on. A key that needs a restart still differs,
    # so the next reload names it again.
    loaded = dict(old)
    for k in applied:
        if k in new:
            loaded[k] = new[k]
        else:
            loaded.pop(k, None)
    _state["loaded"] = loaded
    _state["at"] = datetime.now(UTC).isoformat(timespec="seconds")
    return ReloadResult(applied=tuple(applied), restart=restart)


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
    """When the last reload applied its keys (UTC, ISO), or None."""
    return _state["at"]
