"""WS43-S1 follow-up (task 2465): two P3s of the S1 re-review, one fence file.

1. A cancelled sweep still removes a planted nested ``.git``. ``_sweep_after``
   puts the sweep under a 30 s timeout. The sweep removed links and then nested
   repos in two ``asyncio.to_thread`` calls, and a cancel that landed on the
   first left the second unstarted. Both removals now run in ONE thread call.
2. The host-build refusal for a sandbox dir tells the member what to do. It names
   no flag and no admin, and it says to ask Metorite support.

Hermetic: the two removals are fakes, and the tests prove the ORDER and the
single thread call, not the file work. ``test_app_workspace_links.py`` holds the
real removals.
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


def _sweep_with_a_slow_first_removal(monkeypatch: pytest.MonkeyPatch):
    from acb_skills import safe_open, sandbox_tools

    ran: list[str] = []
    done = threading.Event()

    def slow_links(_root: Path) -> list[str]:
        ran.append("links:start")
        time.sleep(0.3)  # longer than the cancel below
        ran.append("links:end")
        return []

    def nested_git(_root: Path) -> list[str]:
        ran.append("nested_git")
        done.set()
        return []

    monkeypatch.setattr(safe_open, "remove_links", slow_links)
    monkeypatch.setattr(safe_open, "remove_nested_git", nested_git)
    binding = SimpleNamespace(workspace=Path("."))
    return sandbox_tools._sweep_app_links, binding, ran, done


async def test_a_cancel_during_the_link_removal_still_removes_the_nested_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Red when the two removals are two ``to_thread`` calls: the cancel lands
    on the first await and ``remove_nested_git`` never starts."""
    sweep, binding, ran, done = _sweep_with_a_slow_first_removal(monkeypatch)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(sweep(binding, since=0.0), timeout=0.05)
    assert await asyncio.to_thread(done.wait, 5.0), (
        f"the nested .git removal never ran after the cancel: {ran}"
    )
    assert ran == ["links:start", "links:end", "nested_git"]


async def test_the_sweep_without_a_cancel_runs_both_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sweep, binding, ran, _done = _sweep_with_a_slow_first_removal(monkeypatch)
    assert await sweep(binding, since=0.0) == []
    assert ran == ["links:start", "links:end", "nested_git"]


def test_the_refusal_for_a_sandbox_dir_says_what_to_do(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from gateway.routes.apps.files import host_build_refusal
    from orchestrator import sandbox_broker

    monkeypatch.setattr(sandbox_broker, "is_sandbox_dir", lambda _ws: True)
    text = host_build_refusal(tmp_path)
    assert text is not None
    assert "Metorite support" in text
    assert "sandbox" in text
    # The member cannot act on an internal flag or on "an admin".
    for word in ("MAF", "MAF_CODING_SCOPE", "app_builder", "admin"):
        assert word not in text, f"the refusal names {word!r}"
