"""One meeting per worker — enforced at dispatch, not discovered in a launch.

A bot is a real Chrome joining a live WebRTC call (~1.5-3 vCPU and 3-6 GB, the
figures the commercial providers publish for their own per-bot pods), and a
persistent signed-in profile can only be held by ONE Chrome at a time — Chrome
takes an exclusive lock on the user-data dir.

So a second concurrent dispatch is not merely slow, it either dies deep in the
browser launch with a lock error or corrupts the profile that makes unattended
joining possible. This matters the moment more than one person uses the app,
which is exactly when nobody is watching the logs.

The worker lives outside the uv workspace, so it loads by path with a stub for
`.meet` (no Playwright, no browser).
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

import pytest
from fastapi import HTTPException

_APP = pathlib.Path(__file__).resolve().parents[2] / "apps/services/meeting_bot/app"


def _load(profile_dir: str = "", max_concurrent: str = "1"):
    """Load the worker's main module with `.meet` stubbed out."""
    pkg = types.ModuleType("mbapp2")
    pkg.__path__ = [str(_APP)]
    sys.modules["mbapp2"] = pkg

    meet_stub = types.ModuleType("mbapp2.meet")
    meet_stub.PROFILE_DIR = profile_dir
    meet_stub.DIAG_DIR = "/tmp"

    class _Err(Exception):
        def __init__(self, message, status="failed", diagnostics=None):
            super().__init__(message)
            self.status = status
            self.diagnostics = diagnostics or {}

    meet_stub.MeetingBotError = _Err
    meet_stub.join_and_record = lambda *a, **k: None
    sys.modules["mbapp2.meet"] = meet_stub

    import os

    os.environ["MEET_MAX_CONCURRENT"] = max_concurrent
    spec = importlib.util.spec_from_file_location("mbapp2.main", _APP / "main.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mbapp2.main"] = mod
    spec.loader.exec_module(mod)
    return mod


#: The worker refuses a call with no token since task 4120. These cases test
#: the capacity guard, so each one carries the right bearer.
_TOKEN = "concurrency-test-token"
_AUTH = f"Bearer {_TOKEN}"


@pytest.fixture(autouse=True)
def _bot_token(monkeypatch) -> None:
    monkeypatch.setenv("MEETING_BOT_TOKEN", _TOKEN)


def _busy_job(mod, status: str = "in_call"):
    job = mod._Job("busyid", "https://meet.google.com/aaa-bbbb-ccc", "Bot", None)
    job.status = status
    mod._JOBS["busyid"] = job
    return job


@pytest.mark.asyncio
async def test_a_second_meeting_is_refused_while_one_is_live() -> None:
    mod = _load(profile_dir="/profile")
    _busy_job(mod)
    with pytest.raises(HTTPException) as caught:
        await mod.create_bot(
            mod.JoinRequest(meeting_url="https://meet.google.com/xyz-abcd-efg"),
            authorization=_AUTH,
        )
    assert caught.value.status_code == 409
    # One-at-a-time is the normal state here, not a fault, so the message has to
    # name a way forward rather than just report the refusal.
    detail = str(caught.value.detail).lower()
    assert "already in" in detail
    assert "wait" in detail or "another worker" in detail


@pytest.mark.asyncio
async def test_the_profile_lock_is_refused_even_if_capacity_was_raised() -> None:
    """Raising MEET_MAX_CONCURRENT while sharing one signed-in profile is a
    footgun: two Chromes would race for the same lock. Refuse it explicitly
    rather than trusting whoever set the env var to know that."""
    mod = _load(profile_dir="/profile", max_concurrent="4")
    _busy_job(mod)
    with pytest.raises(HTTPException) as caught:
        await mod.create_bot(
            mod.JoinRequest(meeting_url="https://meet.google.com/xyz-abcd-efg"),
            authorization=_AUTH,
        )
    assert caught.value.status_code == 409
    assert "profile" in str(caught.value.detail).lower()


@pytest.mark.asyncio
async def test_capacity_counts_only_live_jobs() -> None:
    """A finished bot must not block the next meeting — otherwise the worker
    bricks itself after the first call until someone restarts it."""
    mod = _load(profile_dir="/profile")
    for terminal in ("done", "failed", "left", "not_admitted"):
        mod._JOBS.clear()
        _busy_job(mod, status=terminal)
        assert mod._active_count() == 0, f"{terminal} must not count as active"


@pytest.mark.asyncio
async def test_an_idle_worker_accepts_a_meeting(monkeypatch) -> None:
    """The guard must not be so eager that it refuses the first bot."""
    mod = _load(profile_dir="/profile")
    mod._JOBS.clear()
    monkeypatch.setattr(mod, "_persist", lambda job: None)
    monkeypatch.setattr(mod, "_run", lambda job: None)

    async def _noop(_job):
        return None

    monkeypatch.setattr(mod, "_run", _noop)
    out = await mod.create_bot(
        mod.JoinRequest(meeting_url="https://meet.google.com/xyz-abcd-efg"),
        authorization=_AUTH,
    )
    assert out["status"] == "joining"
    assert out["id"]


def test_capacity_defaults_to_one_and_cannot_be_zero() -> None:
    """A 0 or negative override would silently disable the notetaker entirely;
    clamp it so a typo degrades to the working default."""
    assert _load(max_concurrent="1").MAX_CONCURRENT == 1
    assert _load(max_concurrent="0").MAX_CONCURRENT == 1
    assert _load(max_concurrent="-3").MAX_CONCURRENT == 1
