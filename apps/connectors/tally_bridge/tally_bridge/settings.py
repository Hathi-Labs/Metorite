"""The settings of the bridge, and the store that holds them.

The setup screen writes the settings (brief section 6.3). A customer never
edits a file. The secrets (the tokens) are not here: ``secret_store`` holds
them.

``SettingsStore`` is the seam. Rung 1 on Linux reads the environment
(``EnvSettingsStore``). Step 4 of the build order adds the Windows store
behind the same interface, and no tool changes.
"""

from __future__ import annotations

import ipaddress
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

# Metorite stops waiting for one tool call at 30 seconds (brief section 3).
METORITE_CALL_LIMIT_S = 30.0


class SettingsError(ValueError):
    """A setting that the bridge refuses to start with."""


@dataclass(frozen=True)
class Settings:
    tally_url: str = "http://127.0.0.1:9000"
    bind_host: str = "127.0.0.1"
    bind_port: int = 8765
    # The public host name of the tunnel, such as office-pc.example.ts.net.
    # The MCP layer accepts a request whose Host header is this name or a
    # loopback name. Empty means loopback only.
    public_host: str = ""
    allowed_companies: tuple[str, ...] = field(default_factory=tuple)
    tally_timeout_s: float = 20.0
    call_deadline_s: float = 25.0
    max_range_days: int = 92
    max_rows: int = 200
    calls_per_minute: int = 60
    # TLY-4, the local copy. The owner's answer to LNK-Q9 (task 3769): every
    # 30 minutes while TallyPrime is open, and a maximum age of 7 days. An
    # empty COPY_DIR turns the copy off.
    copy_dir: str = ""
    copy_interval_min: int = 30
    copy_max_age_days: int = 7
    log_dir: str = ""
    # TLY-5 (integrations_console.md §3.8.13, "Keep Tally reachable"). Each one
    # is off until the customer turns it on. The Windows module applies them:
    # a Task Scheduler task that starts TallyPrime at sign-in, and a request
    # to Windows that the machine does not sleep while the bridge runs.
    start_tally_at_signin: bool = False
    tally_exe: str = ""
    keep_awake: bool = False

    def validate(self) -> Settings:
        """Refuse a setting that breaks a rule of the brief. Returns self."""
        if not _is_loopback(self.bind_host):
            # Security rule 1: only the tunnel reaches the bridge.
            raise SettingsError(f"BIND_HOST must be a loopback address, not {self.bind_host!r}.")
        parts = urlsplit(self.tally_url)
        if parts.scheme != "http" or not parts.hostname or not _is_loopback(parts.hostname):
            # Brief section 5.2: the bridge talks to TallyPrime on this machine only.
            raise SettingsError("TALLY_URL must be http:// on a loopback address.")
        if not 0 < self.bind_port < 65536:
            raise SettingsError("BIND_PORT must be between 1 and 65535.")
        if not 0 < self.call_deadline_s < METORITE_CALL_LIMIT_S:
            raise SettingsError("CALL_DEADLINE_S must be above 0 and below 30.")
        if not 0 < self.tally_timeout_s <= self.call_deadline_s:
            raise SettingsError("TALLY_TIMEOUT_S must be above 0 and not above CALL_DEADLINE_S.")
        if self.start_tally_at_signin and not self.tally_exe:
            raise SettingsError("START_TALLY_AT_SIGNIN needs TALLY_EXE, the path of TallyPrime.")
        if self.tally_exe and (
            not self.tally_exe.lower().endswith(".exe") or '"' in self.tally_exe
        ):
            raise SettingsError("TALLY_EXE must be the path of a program (.exe), with no quotes.")
        if not 5 <= self.copy_interval_min <= 1440:
            raise SettingsError("COPY_INTERVAL_MIN must be between 5 and 1440.")
        if not 1 <= self.copy_max_age_days <= 90:
            raise SettingsError("COPY_MAX_AGE_DAYS must be between 1 and 90.")
        if not 1 <= self.calls_per_minute <= 600:
            raise SettingsError("CALLS_PER_MINUTE must be between 1 and 600.")
        if self.public_host and ("/" in self.public_host or ":" in self.public_host):
            raise SettingsError("PUBLIC_HOST is a host name only, with no scheme, path or port.")
        return self


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


# The names of brief section 6.3, without the token hashes.
_NAMES: dict[str, tuple[str, type]] = {
    "TALLY_URL": ("tally_url", str),
    "BIND_HOST": ("bind_host", str),
    "BIND_PORT": ("bind_port", int),
    "PUBLIC_HOST": ("public_host", str),
    "ALLOWED_COMPANIES": ("allowed_companies", tuple),
    "TALLY_TIMEOUT_S": ("tally_timeout_s", float),
    "CALL_DEADLINE_S": ("call_deadline_s", float),
    "MAX_RANGE_DAYS": ("max_range_days", int),
    "MAX_ROWS": ("max_rows", int),
    "CALLS_PER_MINUTE": ("calls_per_minute", int),
    "COPY_DIR": ("copy_dir", str),
    "COPY_INTERVAL_MIN": ("copy_interval_min", int),
    "COPY_MAX_AGE_DAYS": ("copy_max_age_days", int),
    "START_TALLY_AT_SIGNIN": ("start_tally_at_signin", bool),
    "TALLY_EXE": ("tally_exe", str),
    "KEEP_AWAKE": ("keep_awake", bool),
    "LOG_DIR": ("log_dir", str),
}


def settings_from_mapping(values: Mapping[str, str]) -> Settings:
    """Build the settings from the names of brief section 6.3.

    ``ALLOWED_COMPANIES`` is a list split by ``|``, because a Tally company
    name can hold a comma. A name that is missing keeps its default.
    """
    kwargs: dict[str, object] = {}
    for name, (attr, kind) in _NAMES.items():
        raw = values.get(name)
        if raw is None or raw == "":
            continue
        try:
            if kind is tuple:
                kwargs[attr] = tuple(p.strip() for p in raw.split("|") if p.strip())
            elif kind is bool:
                kwargs[attr] = _on_off(raw)
            else:
                kwargs[attr] = kind(raw)
        except ValueError as exc:
            raise SettingsError(f"{name} has a bad value.") from exc
    return Settings(**kwargs).validate()  # type: ignore[arg-type]


def settings_to_mapping(settings: Settings) -> dict[str, str]:
    """The names of brief section 6.3, as text. The reverse of settings_from_mapping."""
    out: dict[str, str] = {}
    for name, (attr, kind) in _NAMES.items():
        value = getattr(settings, attr)
        if kind is tuple:
            out[name] = "|".join(value)
        elif kind is bool:
            out[name] = "true" if value else "false"
        else:
            out[name] = str(value)
    return out


def _on_off(raw: str) -> bool:
    """``true``/``false``, ``1``/``0``, ``yes``/``no`` or ``on``/``off``.
    Anything else is refused, never read as true."""
    text = raw.strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ValueError(raw)


class SettingsStore(Protocol):
    def load(self) -> Settings: ...

    def save(self, settings: Settings) -> None:
        """Write the settings. The setup screen calls it. A read-only store raises."""
        ...


class EnvSettingsStore:
    """Reads ``TALLY_BRIDGE_<NAME>`` from the environment. For a dev box."""

    PREFIX = "TALLY_BRIDGE_"

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = os.environ if env is None else env

    def load(self) -> Settings:
        values = {
            name: self._env[self.PREFIX + name]
            for name in _NAMES
            if self.PREFIX + name in self._env
        }
        return settings_from_mapping(values)

    def save(self, settings: Settings) -> None:
        raise SettingsError("The environment store is read only. Pass --settings-file.")


class FileSettingsStore:
    """The settings in one JSON file, with the names of brief section 6.3.

    For a dev box, and the shape that step 4 keeps on Windows (under
    ProgramData). A save writes a new file and then replaces the old one, so a
    crash never leaves half a file. The file holds no secret.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    def load(self) -> Settings:
        if not self._path.exists():
            return Settings().validate()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SettingsError(f"The settings file {self._path} is not readable.") from exc
        if not isinstance(data, dict):
            raise SettingsError(f"The settings file {self._path} is not a JSON object.")
        return settings_from_mapping({str(k): str(v) for k, v in data.items()})

    def save(self, settings: Settings) -> None:
        settings.validate()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.unlink(missing_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(settings_to_mapping(settings), fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self._path)


class LiveSettings:
    """The settings that the running bridge reads on each call.

    The tools and the setup screen share one LiveSettings. When the setup
    screen saves a new allow-list, ``replace`` swaps the settings, and the next
    tool call reads the new list with no restart. The bind address, the port,
    the public host and the call limit take effect at the next start.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings.validate()

    @property
    def current(self) -> Settings:
        return self._settings

    def replace(self, settings: Settings) -> None:
        self._settings = settings.validate()

    def __getattr__(self, name: str) -> object:
        return getattr(self._settings, name)
