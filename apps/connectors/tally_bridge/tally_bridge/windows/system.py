"""The changes that the program makes to the Windows machine (§3.8.13).

Each change is a fixed command, built here as a list of words and run by the
``Runner`` seam, with no shell. Only the TallyPrime path is the customer's
text, and ``Settings.validate`` checks it first (an ``.exe`` path). A test on
Linux checks each command word for word.

* **Start TallyPrime at sign-in:** a Task Scheduler task, made from a task
  XML, that runs at the sign-in of any person on the machine, as that person
  (the group of all users, SID S-1-5-32-545, least privilege). So the task works for the
  person at the desk, not only for the administrator who ran the installer.
  TallyPrime loads the companies of its own "load companies on start-up"
  setting.
* **The firewall rule:** block inbound TCP 9000 (Tally) and 8765 (the
  bridge) from the network. Windows does not filter loopback traffic, so the
  bridge still reaches Tally on 127.0.0.1 (brief section 5.2).
* **Keep awake:** ``SetThreadExecutionState`` while the bridge runs. It
  changes no power plan, and it ends when the bridge stops.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol
from xml.sax.saxutils import escape as xml_escape

TASK_NAME = "Tally Bridge - Start TallyPrime"
FIREWALL_RULE = "Tally Bridge - block network access"
BLOCKED_PORTS = "9000,8765"

# SetThreadExecutionState flags.
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

Runner = Callable[[Sequence[str]], int]


#: The variables that ``schtasks`` and ``netsh`` need. A child gets only
#: these, never the whole environment of the bridge (the rule of
#: ``test_child_env_seam.py``, kept locally: the program imports nothing of
#: the monorepo).
CHILD_ENV_NAMES = ("SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP")


def child_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment of a child command: :data:`CHILD_ENV_NAMES` only."""
    source = os.environ if env is None else env
    upper = {k.upper(): v for k, v in source.items()}
    return {name: upper[name] for name in CHILD_ENV_NAMES if name in upper}


def run(argv: Sequence[str]) -> int:  # pragma: no cover - Windows
    """Run one command with no shell. Returns the exit code."""
    return subprocess.run(list(argv), check=False, capture_output=True, env=child_env()).returncode


#: The well-known SID of the built-in Users group. The task runs for each person
#: who signs in, as that person.
USERS_SID = "S-1-5-32-545"


def startup_xml(tally_exe: str) -> str:
    """The task XML of the sign-in task. ``schtasks /XML`` reads it."""
    if not tally_exe or not tally_exe.lower().endswith(".exe") or '"' in tally_exe:
        raise ValueError("The TallyPrime path must be a program path (.exe).")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Starts TallyPrime when a person signs in.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <GroupId>{USERS_SID}</GroupId>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{xml_escape(tally_exe)}</Command>
    </Exec>
  </Actions>
</Task>
"""


def startup_on(xml_path: str) -> list[str]:
    """Make the sign-in task from the file that :func:`startup_xml` wrote."""
    return ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", xml_path, "/F"]


def write_startup_xml(tally_exe: str, folder: str | None = None) -> str:
    """Write the task XML in UTF-16 (the encoding that ``schtasks`` reads) to
    a new temporary file, and return its path. The caller deletes it."""
    text = startup_xml(tally_exe)
    fd, path = tempfile.mkstemp(prefix="tally-bridge-task-", suffix=".xml", dir=folder)
    with os.fdopen(fd, "w", encoding="utf-16") as fh:
        fh.write(text)
    return path


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
    if not enabled:
        return runner(startup_off())
    path = write_startup_xml(tally_exe)
    try:
        return runner(startup_on(path))
    finally:
        os.unlink(path)


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
