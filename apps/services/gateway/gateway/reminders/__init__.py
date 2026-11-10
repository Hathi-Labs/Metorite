"""Reminders — the loop and the smart slot (WS-66 RM-2, D114).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.4 and §5.5.

* ``slot`` — pure: the grace, quiet hours, the next day start, and
  ``next_slot``. No model, no database and no clock.
* ``loop`` — the 60-second worker. It fires a reminder, checks back, moves
  it to the brief, reschedules it, and gives up. Each step is a row move and
  one ledger row, in one transaction.
* ``context`` — pure: the words of a tap that opens the chat (RM-4, §5.7).
  The first message, the context block and the card data.

The store and the member's routes are ``routes/projects/reminders.py``
(RM-1). The cards come from the ``reminders`` source of
``routes/shell/feed.py``.
"""
