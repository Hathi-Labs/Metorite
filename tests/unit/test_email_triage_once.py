"""WS-17 EM-T16 PR-A, A1 — one Reply Zero classify in each sync cycle.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.17 PR-A (D-EM-62).

Before PR-A, a cycle with new mail called ``_maybe_classify_threads`` two
times for each mailbox: once inside ``process_new_mail`` (the ``on_new_mail``
hook) and once in the ``classify_threads`` hook. With
``EMAIL_TRIAGE_ONCE_PER_CYCLE`` on, a cycle in which ``on_new_mail`` was
registered and did not raise skips the ``classify_threads`` hook. Every other
cycle runs it, so a quiet mailbox still drains its backlog.

These cases drive ONE real cycle of ``_account_sync_loop`` over the REAL hook
wiring of the gateway (``register_email_post_sync_hooks``). Only the leaves
are fakes, and the count is the calls of ``_maybe_classify_threads``.

Mutations that this file catches (R7):

* the scheduler loses the skip -> ``test_one_classify_in_a_cycle_with_new_mail``;
* the skip ignores the flag -> ``test_flag_off_keeps_the_two_classifies``;
* the skip ignores a raise or a missing hook -> the two ``still_classifies``
  cases.

Run::

    uv run pytest tests/unit/test_email_triage_once.py -q
"""
from __future__ import annotations

import asyncio

import email_ingestion.post_sync as post_sync
import email_ingestion.scheduler as sched
import pytest
from acb_common.settings import get_settings

ORG = "77777777-7777-7777-7777-777777777777"
ACC = "acc-triage-once"


@pytest.fixture()
def wiring(monkeypatch):
    """The real gateway hooks, with each leaf of ``process_new_mail`` and the
    owner read replaced. Returns the list of classify calls."""
    from gateway.routes.email import scheduler_hooks
    from gateway.routes.email.automation import cleanup, replyzero, senders

    classified: list[str] = []

    async def _classify(account_id: str) -> None:
        classified.append(account_id)

    async def _noop(*_a, **_kw) -> None:
        return None

    async def _owner(_account_id: str) -> None:
        return None

    monkeypatch.setattr(replyzero, "_maybe_classify_threads", _classify)
    monkeypatch.setattr(scheduler_hooks, "auto_run_rules_for_account", _noop)
    monkeypatch.setattr(scheduler_hooks, "mailbox_owner", _owner)
    monkeypatch.setattr(cleanup, "sweep_uncategorized", _noop)
    monkeypatch.setattr(senders, "_categorize_senders_job", _noop)
    monkeypatch.setattr(senders, "_maybe_auto_archive", _noop)
    for name in ("on_new_mail", "classify_threads", "send_digest",
                 "send_follow_up_reminders", "ensure_subscription",
                 "learn_label_changes"):
        monkeypatch.setattr(post_sync.hooks, name, None)
    scheduler_hooks.register_email_post_sync_hooks()
    # Only the two hooks of the count run. The others reach a database.
    for name in ("send_digest", "send_follow_up_reminders", "ensure_subscription"):
        monkeypatch.setattr(post_sync.hooks, name, None)
    return classified


def _flag(monkeypatch, on: bool) -> None:
    monkeypatch.setenv("EMAIL_TRIAGE_ONCE_PER_CYCLE", "true" if on else "false")
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _one_cycle(monkeypatch, *, synced: int) -> None:
    """Run ONE cycle of the loop. The sleep after it stops the loop."""

    async def _sync(account_id, **_kw):
        return {"synced": synced}

    async def _interval(*_a, **_kw):
        return 300

    async def _stop(_secs):
        raise asyncio.CancelledError

    monkeypatch.setattr(sched, "_sync_account", _sync)
    monkeypatch.setattr(sched, "_get_account_sync_interval", _interval)
    monkeypatch.setattr(sched.asyncio, "sleep", _stop)
    with pytest.raises(asyncio.CancelledError):
        await sched._account_sync_loop(ACC, 300, organization_id=ORG)


async def test_one_classify_in_a_cycle_with_new_mail(monkeypatch, wiring) -> None:
    _flag(monkeypatch, True)
    await _one_cycle(monkeypatch, synced=3)
    assert wiring == [ACC], f"a cycle with new mail classified {len(wiring)} times"


async def test_flag_off_keeps_the_two_classifies(monkeypatch, wiring) -> None:
    """The control: with the flag off, the cycle is the cycle of today."""
    _flag(monkeypatch, False)
    await _one_cycle(monkeypatch, synced=3)
    assert wiring == [ACC, ACC]


@pytest.mark.parametrize("on", [True, False])
async def test_a_quiet_cycle_still_classifies_once(monkeypatch, wiring, on) -> None:
    """The drain keeps its rate: a cycle with no new mail runs the hook."""
    _flag(monkeypatch, on)
    await _one_cycle(monkeypatch, synced=0)
    assert wiring == [ACC]


async def test_a_raising_new_mail_hook_still_classifies(monkeypatch, wiring) -> None:
    """Rule 3: "ran" means registered AND did not raise."""
    _flag(monkeypatch, True)

    async def _raises(_account_id: str) -> None:
        raise RuntimeError("the pipeline broke")

    monkeypatch.setattr(post_sync.hooks, "on_new_mail", _raises)
    await _one_cycle(monkeypatch, synced=3)
    assert wiring == [ACC], "a failed new-mail hook skipped the classify"


async def test_a_missing_new_mail_hook_still_classifies(monkeypatch, wiring) -> None:
    _flag(monkeypatch, True)
    monkeypatch.setattr(post_sync.hooks, "on_new_mail", None)
    await _one_cycle(monkeypatch, synced=3)
    assert wiring == [ACC], "an unregistered new-mail hook skipped the classify"


async def test_the_manual_sync_and_the_webhook_still_classify(monkeypatch, wiring) -> None:
    """Both call ``process_new_mail`` directly, and it keeps its classify."""
    from gateway.routes.email import scheduler_hooks

    _flag(monkeypatch, True)
    await scheduler_hooks.process_new_mail(ACC)
    assert wiring == [ACC]


def test_the_flag_has_one_reader_and_ships_off(monkeypatch) -> None:
    monkeypatch.delenv("EMAIL_TRIAGE_ONCE_PER_CYCLE", raising=False)
    get_settings.cache_clear()
    assert get_settings().email_triage_once_per_cycle is False
    assert post_sync.triage_once_per_cycle() is False
    assert sched.triage_once_per_cycle is post_sync.triage_once_per_cycle
