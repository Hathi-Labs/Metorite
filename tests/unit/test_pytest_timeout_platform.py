"""CIV-15b (task 2187): pytest-timeout must not use ``signal`` where there is no SIGALRM.

``pyproject.toml`` sets ``timeout_method = "signal"``. Windows has no
``signal.SIGALRM``, so every run stopped with an INTERNALERROR before the first
test. ``tests/conftest.py`` sets the ``thread`` method on such a platform.
"""
from __future__ import annotations

import signal
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_the_ini_still_names_signal_for_linux_and_ci() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'timeout_method = "signal"' in text


def test_the_conftest_picks_thread_where_there_is_no_sigalrm() -> None:
    text = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert 'hasattr(signal, "SIGALRM")' in text
    assert 'config.option.timeout_method = "thread"' in text


def test_this_run_uses_a_method_the_platform_supports(request: pytest.FixtureRequest) -> None:
    method = request.config._env_timeout_method  # set by pytest-timeout
    if hasattr(signal, "SIGALRM"):
        assert method in ("signal", "thread")
    else:
        assert method == "thread", f"{sys.platform} has no SIGALRM, the method is {method}"


def test_premerge_shows_an_internalerror_of_the_collision_fences() -> None:
    """A pytest that stops with INTERNALERROR prints no FAILED line. premerge.sh
    named no test, and a session read that as a real collision."""
    text = (ROOT / "scripts" / "premerge.sh").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if 'fences.log" || true' in ln)
    assert "INTERNALERROR" in line and "no tests ran" in line
