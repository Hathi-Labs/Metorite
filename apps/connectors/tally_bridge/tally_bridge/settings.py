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
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
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
    log_dir: str = ""

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
            else:
                kwargs[attr] = kind(raw)
        except ValueError as exc:
            raise SettingsError(f"{name} has a bad value.") from exc
    return Settings(**kwargs).validate()  # type: ignore[arg-type]


class SettingsStore(Protocol):
    def load(self) -> Settings: ...


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
