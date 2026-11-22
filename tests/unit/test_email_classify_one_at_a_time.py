"""WS-17 EM-T16 H-285 — one Reply Zero classify of a mailbox at a time.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.17 PR-A, "The
risks that PR-A accepts".

The webhook and the manual sync call ``process_new_mail``. The scheduler runs
``hooks.classify_threads``. Both end in ``_maybe_classify_threads``. With
``EMAIL_CLASSIFY_ONE_AT_A_TIME`` on, a second call for the SAME mailbox
skips while the first runs. A different mailbox still runs.

Mutations that this file catches (R7):

* the guard is missing -> ``test_two_calls_for_one_account_run_once``;
* the guard is global, not per account -> ``test_two_accounts_both_run``;
* the guard stays held after a run or a raise -> the two ``releases`` cases;
* the guard ignores the flag -> ``test_flag_off_runs_both``.

Run::

    uv run pytest tests/unit/test_email_classify_one_at_a_time.py -q
"""
from __future__ import annotations

import asyncio

import email_ingestion.post_sync as post_sync
import pytest
from acb_common.settings import get_settings

ACC = "acc-one-at-a-time"
OTHER = "acc-other"


@pytest.fixture(autouse=True)
def _settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _flag(monkeypatch, on: bool) -> None:
    monkeypatch.setenv("EMAIL_CLASSIFY_ONE_AT_A_TIME", "true" if on else "false")
    get_settings.cache_clear()


class _Inner:
    """The fake classify. It blocks on ``gate`` and counts its starts."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.gate = asyncio.Event()
        self.raises = False

    async def __call__(self, account_id: str, **_kw) -> None:
        self.started.append(account_id)
        await self.gate.wait()
        if self.raises:
            raise RuntimeError("the classify broke")


@pytest.fixture()
def wiring(monkeypatch):
    from gateway.routes.email import scheduler_hooks
    from gateway.routes.email.automation import cleanup, replyzero, senders

    inner = _Inner()

    async def _noop(*_a, **_kw) -> None:
        return None

    monkeypatch.setattr(replyzero, "_maybe_classify_threads", inner)
    monkeypatch.setattr(scheduler_hooks, "auto_run_rules_for_account", _noop)
    monkeypatch.setattr(scheduler_hooks, "mailbox_owner", _noop)
    monkeypatch.setattr(cleanup, "sweep_uncategorized", _noop)
    monkeypatch.setattr(senders, "_categorize_senders_job", _noop)
    monkeypatch.setattr(senders, "_maybe_auto_archive", _noop)
    for name in ("on_new_mail", "classify_threads", "send_digest",
                 "send_follow_up_reminders", "ensure_subscription",
                 "learn_label_changes"):
        monkeypatch.setattr(post_sync.hooks, name, None)
    scheduler_hooks.register_email_post_sync_hooks()
    for name in ("send_digest", "send_follow_up_reminders", "ensure_subscription"):
        monkeypatch.setattr(post_sync.hooks, name, None)
    return scheduler_hooks, inner


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_two_calls_for_one_account_run_once(monkeypatch, wiring) -> None:
    hooks_mod, inner = wiring
    _flag(monkeypatch, True)
    first = asyncio.create_task(hooks_mod.process_new_mail(ACC))
    await _settle()
    second = asyncio.create_task(post_sync.hooks.classify_threads(ACC))
    await _settle()
    assert inner.started == [ACC], "the second classify of one mailbox ran too"
    inner.gate.set()
    await asyncio.gather(first, second)
    assert inner.started == [ACC]


async def test_two_accounts_both_run(monkeypatch, wiring) -> None:
    hooks_mod, inner = wiring
    _flag(monkeypatch, True)
    a = asyncio.create_task(hooks_mod.process_new_mail(ACC))
    await _settle()
    b = asyncio.create_task(post_sync.hooks.classify_threads(OTHER))
    await _settle()
    assert sorted(inner.started) == sorted([ACC, OTHER])
    inner.gate.set()
    await asyncio.gather(a, b)


async def test_a_later_call_runs_again_after_the_first_ends(monkeypatch, wiring) -> None:
    hooks_mod, inner = wiring
    _flag(monkeypatch, True)
    inner.gate.set()
    await hooks_mod.process_new_mail(ACC)
    await post_sync.hooks.classify_threads(ACC)
    assert inner.started == [ACC, ACC]


async def test_a_raise_releases_the_guard(monkeypatch, wiring) -> None:
    hooks_mod, inner = wiring
    _flag(monkeypatch, True)
    inner.gate.set()
    inner.raises = True
    await hooks_mod.process_new_mail(ACC)  # logs the error, never raises
    with pytest.raises(RuntimeError):
        await post_sync.hooks.classify_threads(ACC)
    inner.raises = False
    await post_sync.hooks.classify_threads(ACC)
    assert inner.started == [ACC, ACC, ACC]


async def test_a_cancel_releases_the_guard(monkeypatch, wiring) -> None:
    hooks_mod, inner = wiring
    _flag(monkeypatch, True)
    task = asyncio.create_task(hooks_mod.process_new_mail(ACC))
    await _settle()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    inner.gate.set()
    await post_sync.hooks.classify_threads(ACC)
    assert inner.started == [ACC, ACC]


async def test_flag_off_runs_both(monkeypatch, wiring) -> None:
    """The control: with the flag off, both calls run, as today."""
    hooks_mod, inner = wiring
    _flag(monkeypatch, False)
    first = asyncio.create_task(hooks_mod.process_new_mail(ACC))
    await _settle()
    second = asyncio.create_task(post_sync.hooks.classify_threads(ACC))
    await _settle()
    assert inner.started == [ACC, ACC]
    inner.gate.set()
    await asyncio.gather(first, second)


def test_the_flag_ships_off(monkeypatch) -> None:
    monkeypatch.delenv("EMAIL_CLASSIFY_ONE_AT_A_TIME", raising=False)
    get_settings.cache_clear()
    assert get_settings().email_classify_one_at_a_time is False
    assert post_sync.classify_one_at_a_time() is False
