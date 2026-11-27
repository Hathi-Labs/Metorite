"""Each API-connection route module imports alone (task 4112).

``routes/api_connections`` imports ``routes/integrations_api_connect``. A
top-level import in the second module once reached back to the first, so a
direct import of the second module failed. A full-suite run imported them in
another order and passed. Each case below uses a fresh interpreter, so no
earlier import can hide the cycle.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytest.importorskip("gateway", reason="gateway not installed")

_CONNECT = "gateway.routes.integrations_api_connect"
_CALL = "gateway.routes.api_connections"


@pytest.mark.parametrize(
    "modules",
    [(_CONNECT,), (_CALL,), (_CONNECT, _CALL), (_CALL, _CONNECT)],
    ids=["connect", "call", "connect-then-call", "call-then-connect"],
)
def test_the_module_imports_in_a_fresh_interpreter(modules: tuple[str, ...]) -> None:
    code = "; ".join(f"import {name}" for name in modules)
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )

    assert done.returncode == 0, done.stderr[-3000:]
