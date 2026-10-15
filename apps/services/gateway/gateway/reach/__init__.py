"""Reach — Metorite messages a member first (WS-59, ``proactive_reach.md``).

* ``flags`` — the one reader of the ``REACH_*`` settings.
* ``classes`` — how far each card kind may reach.
* ``plan`` — pure: which cards reach, when, and the words.
* ``outbox`` — the one writer of ``reach_outbox`` and ``reach_signal_log``.
* ``whatsapp`` — the channel adapter, the only module that sends.
* ``loop`` — the 60-second loop. ``routes`` — ``/reach/preview`` and
  ``/reach/brief``.
"""
