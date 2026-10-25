"""Web push, the transport of ``chat_run_continuity.md`` §S7 (WS-66 PN-1).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.9 and §10.2.

* ``flags`` — the one reader of the four web push settings.
* ``endpoints`` — the rule for a push service URL that a browser gives.
* ``store`` — the one writer of ``push_subscriptions`` (migration 306).
* ``sender`` — the one sender, on ``pywebpush``.
* ``routes`` — ``/push``, gated on ``feature:chat``.
"""
