"""The changes that the program makes to the Windows machine (§3.8.13).

Each change is a fixed command, built here as a list of words and run by the
``Runner`` seam, with no shell. Only the TallyPrime path is the customer's
text, and ``Settings.validate`` checks it first (an ``.exe`` path). A test on
Linux checks each command word for word.

* **Start TallyPrime at sign-in:** a Task Scheduler task that runs at the
  sign-in of the person at the desk. TallyPrime loads the companies of its
  own "load companies on start-up" setting.
* **The firewall rule:** block inbound TCP 9000 (Tally) and 8765 (the
  bridge) from the network. Windows does not filter loopback traffic, so the
  bridge still reaches Tally on 127.0.0.1 (brief section 5.2).
* **Keep awake:** ``SetThreadExecutionState`` while the bridge runs. It
  changes no power plan, and it ends when the bridge stops.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence
from typing import Protocol

TASK_NAME = "Tally Bridge - Start TallyPrime"
FIREWALL_RULE = "Tally Bridge - block network access"
BLOCKED_PORTS = "9000,8765"

# SetThreadExecutionState flags.
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

Runner = Callable[[Sequence[str]], int]


def run(argv: Sequence[str]) -> int:  # pragma: no cover - Windows
    """Run one command with no shell. Returns the exit code."""
    return subprocess.run(list(argv), check=False, capture_output=True).returncode


def startup_on(tally_exe: str) -> list[str]:
    if not tally_exe or not tally_exe.lower().endswith(".exe") or '"' in tally_exe:
        raise ValueError("The TallyPrime path must be a program path (.exe).")
    return [
        "schtasks",
        "/Create",
        "/TN",
        TASK_NAME,
        "/TR",
        f'"{tally_exe}"',
        "/SC",
        "ONLOGON",
        "/RL",
        "LIMITED",
        "/F",
    ]


def startup_off() -> list[str]:
    return ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]


def firewall_on() -> list[str]:
    return [
        "netsh",
        "advfirewall",
        "firewall",
        "add",
        "rule",
        f"name={FIREWALL_RULE}",
        "dir=in",
        "action=block",
        "protocol=TCP",
        f"localport={BLOCKED_PORTS}",
        "profile=any",
    ]


def firewall_off() -> list[str]:
    return ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE}"]


def apply_startup(enabled: bool, tally_exe: str, runner: Runner = run) -> int:
    """Make or remove the sign-in task, to match the setting."""
    return runner(startup_on(tally_exe) if enabled else startup_off())


class PowerApi(Protocol):
    def SetThreadExecutionState(self, flags: int) -> int: ...


def _kernel32() -> PowerApi:  # pragma: no cover - Windows
    import ctypes

    return ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[return-value]


class KeepAwake:
    """Ask Windows not to sleep while the bridge runs. ``start`` and
    ``stop`` are safe to call twice. Off any system but Windows, it does
    nothing."""

    def __init__(self, api: PowerApi | None = None) -> None:
        self._api = api
        self._on = False

    def _power(self) -> PowerApi | None:
        if self._api is None and sys.platform == "win32":  # pragma: no cover - Windows
            self._api = _kernel32()
        return self._api

    def start(self) -> bool:
        api = self._power()
        if api is None or self._on:
            return self._on
        self._on = bool(api.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
        return self._on

    def stop(self) -> None:
        api = self._power()
        if api is not None and self._on:
            api.SetThreadExecutionState(ES_CONTINUOUS)
        self._on = False
