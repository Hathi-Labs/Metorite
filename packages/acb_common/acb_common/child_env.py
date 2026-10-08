"""The one env for every child process that the gateway starts (WS-49 BH-1).

The gateway env holds every secret of the box: the database URLs, the
provider keys, the OAuth client secrets and the internal bearer. A child
process that inherits that env can read all of them. The Copilot CLI is the
sharp case, because it has a shell tool, and a prompt injection in an email
or a document can reach that tool (H-270).

So every spawn in the gateway, the orchestrator and ``packages/`` passes
``env=`` from this module. ``child_env()`` gives a short allowlist of names
that hold no secret. A call site that needs one more name adds it by value,
with ``extra=``, in code that a reviewer reads. ``env_values(...)`` reads
named values for ``extra=``. This module never copies a name by a pattern
that can match a secret.

* ``child_env(extra=...)`` is the base. Use it for ``git``, ``uv``,
  ``ffmpeg``, ``python`` and ``node`` children.
* ``copilot_env()`` is the env of the Copilot CLI. It holds no token. The
  SDK adds ``COPILOT_SDK_AUTH_TOKEN`` from ``github_token`` itself
  (``copilot/client.py`` ``_start_cli_server``).
* ``docker_env()`` adds the names that tell the ``docker`` CLI where the
  daemon is.

This module holds no I/O. The fence is ``tests/unit/test_child_env_seam.py``
(BH-F1), an AST scan of ``apps/`` and ``packages/``. The stub CLI test is
``tests/unit/test_copilot_child_env.py`` (BH-F2). Spec:
``project-docs/specs/box_hardening.md`` §5 BH-1.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping

__all__ = [
    "AGENT_PATH_VALUES",
    "BASE_NAMES",
    "BASE_PREFIXES",
    "COPILOT_NAMES",
    "DOCKER_NAMES",
    "child_env",
    "copilot_env",
    "docker_env",
    "env_values",
]

#: The names every child gets, when they are set. None of them holds a
#: secret. The three Python names are here and not call-site extras (spec fix
#: E3). Each one is a path. ``pdf_render`` and ``run_script`` start Python
#: children that need ``VIRTUAL_ENV`` and ``PYTHONPATH``. BH-2 sets
#: ``UV_CACHE_DIR`` for every ``uv`` child, and without it ``uv`` falls back
#: to ``~/.cache/uv``, which ``ProtectHome=read-only`` refuses.
BASE_NAMES: tuple[str, ...] = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "TZ",
    "TMPDIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "NODE_EXTRA_CA_CERTS",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "UV_CACHE_DIR",
)

#: The name families every child gets. A family name that looks like a
#: secret is still refused (``_SECRET_SHAPED``).
BASE_PREFIXES: tuple[str, ...] = ("LC_", "XDG_", "SSL_CERT_")

#: Names that a prefix matches and that no child gets. ``XDG_RUNTIME_DIR``
#: points at ``/run/user/<uid>``, the user systemd manager, which is path P4
#: of the spec. ``systemd-run --user`` finds the manager through it.
_REFUSED_NAMES: frozenset[str] = frozenset({"XDG_RUNTIME_DIR"})

#: The names a Windows dev box needs to start any process at all. They are
#: added only on Windows, and the box runs Linux.
_WINDOWS_NAMES: frozenset[str] = frozenset({
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "PATHEXT",
    "COMSPEC",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "SYSTEMDRIVE",
})

#: The extra names of the Copilot CLI. Each one is a path or a terminal
#: setting, and none is a secret. ``COPILOT_CLI_PATH`` keeps an operator's
#: override of the binary, because the SDK reads it from the env it is given.
#: ``AGENTS_CLONE_DIR`` and ``CUSTOM_APPS_T2_VENDOR_DIR`` reach
#: ``build_t2.mjs`` when the app-builder session runs it through the shell.
COPILOT_NAMES: tuple[str, ...] = (
    "SHELL",
    "TERM",
    "COPILOT_CLI_PATH",
    "COPILOT_HOME",
    "COPILOT_CACHE_HOME",
    "AGENTS_CLONE_DIR",
    "CUSTOM_APPS_T2_VENDOR_DIR",
)

#: Fixed path values for the children that run agent code: the Copilot CLI
#: (``copilot_env()``) and ``run_script`` (``code_tools._script_env``). This is
#: the ONE place to add one. A value here wins over the gateway's own value of
#: that name. It is empty today. WS-49 BH-7 adds ``PYTHONPATH`` (the agent-site
#: dir) and ``CUSTOM_APPS_T2_VENDOR_DIR`` here, and no call site changes.
#: Never put a secret here.
AGENT_PATH_VALUES: dict[str, str] = {}

#: The names that tell the ``docker`` CLI where the daemon is and how to
#: reach it. ``DOCKER_CERT_PATH`` is a dir, not a key.
DOCKER_NAMES: tuple[str, ...] = (
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY",
)

#: The token names that the Copilot CLI must never get from us. The SDK
#: gives it ``COPILOT_SDK_AUTH_TOKEN`` and nothing else.
_COPILOT_TOKEN_NAMES: frozenset[str] = frozenset({
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "COPILOT_GITHUB_TOKEN",
    "GITHUB_COPILOT_TOKEN",
    "COPILOT_SDK_AUTH_TOKEN",
})

_SECRET_SHAPED = re.compile(
    r"TOKEN|SECRET|KEY|PASSWORD|PASSWD|CREDENTIAL|DSN|AUTH", re.IGNORECASE
)


def _base_name(name: str) -> bool:
    if name in _REFUSED_NAMES:
        return False
    if name in BASE_NAMES:
        return True
    if os.name == "nt" and name.upper() in _WINDOWS_NAMES:
        return True
    if name.startswith(BASE_PREFIXES):
        return not _SECRET_SHAPED.search(name)
    return False


def env_values(*names: str) -> dict[str, str]:
    """The current values of the named variables, for ``extra=``.

    A name that is not set is left out. Give each name as a literal at the
    call site, or as a named tuple in the module, so a reviewer reads it.
    """
    out: dict[str, str] = {}
    for name in names:
        if not isinstance(name, str) or not name:
            raise TypeError(f"env_values takes names, not {name!r}")
        value = os.environ.get(name)
        if value is not None:
            out[name] = value
    return out


def child_env(*, extra: Mapping[str, str | None] | None = None) -> dict[str, str]:
    """A new env for a child process: the base allowlist, then ``extra``.

    ``extra`` maps a name to a value. A ``None`` value is left out, so a call
    site can write ``{"GH_TOKEN": token or None}``. ``extra`` must never be
    ``os.environ`` or a copy of it. The fence refuses that form in the code,
    and this function refuses the live object.
    """
    if extra is os.environ:
        raise TypeError("child_env(extra=os.environ) gives the child every secret")
    env = {name: value for name, value in os.environ.items() if _base_name(name)}
    if extra:
        for name, value in extra.items():
            if not isinstance(name, str) or not name:
                raise TypeError(f"child_env extra name must be a string, not {name!r}")
            if value is None:
                continue
            if not isinstance(value, str):
                raise TypeError(f"child_env extra value for {name} must be a string")
            env[name] = value
    return env


def copilot_env() -> dict[str, str]:
    """The env of the Copilot CLI child. It holds no token name.

    Pass it as ``CopilotClient(env=copilot_env(), github_token=...)``. The SDK
    copies this mapping and then sets ``COPILOT_SDK_AUTH_TOKEN`` itself.
    """
    env = child_env(extra={**env_values(*COPILOT_NAMES), **AGENT_PATH_VALUES})
    for name in _COPILOT_TOKEN_NAMES:
        env.pop(name, None)
    return env


def docker_env() -> dict[str, str]:
    """The env of a ``docker`` CLI child: the base, and ``DOCKER_NAMES``."""
    return child_env(extra=env_values(*DOCKER_NAMES))
