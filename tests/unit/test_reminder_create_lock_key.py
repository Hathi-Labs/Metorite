"""RX-7 item 9 — the create lock of a reminder is per tenant and per member.

The key of ``pg_advisory_xact_lock`` in ``create_reminder`` holds the tenant.
Without it, one address in two organizations shares one lock.

Mutation: drop ``tenant`` from ``create_lock_key``, and the first test fails.
Make ``create_reminder`` build its own key again, and the last test fails.
"""
from __future__ import annotations

import inspect

from gateway.routes.projects import reminders


def test_two_tenants_give_two_keys_for_one_address() -> None:
    a = reminders.create_lock_key("org-a", "ana@x.test")
    b = reminders.create_lock_key("org-b", "ana@x.test")
    assert a != b


def test_two_members_give_two_keys_in_one_tenant() -> None:
    assert reminders.create_lock_key("org-a", "ana@x.test") != reminders.create_lock_key(
        "org-a", "bo@x.test")


def test_no_tenant_is_a_key_of_its_own() -> None:
    assert reminders.create_lock_key(None, "ana@x.test") != reminders.create_lock_key(
        "org-a", "ana@x.test")


def test_the_create_route_takes_its_key_from_the_builder() -> None:
    src = inspect.getsource(reminders.create_reminder)
    assert "create_lock_key(current_tenant(), me)" in src
