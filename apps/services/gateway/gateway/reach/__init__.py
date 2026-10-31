"""Reach — Metorite messages a member first (WS-59, ``proactive_reach.md``).

* ``flags`` — the one reader of the ``REACH_*`` settings.
* ``classes`` — how far each card kind may reach.
* ``plan`` — pure: which cards reach, when, and the words.
* ``outbox`` — the one writer of ``reach_outbox`` and ``reach_signal_log``.
* ``requests`` — "Tell me on WhatsApp": ``tell_me``, the one call an app
  makes, and the one writer of ``reach_requests`` (RE-5).
* ``whatsapp`` — the channel adapter, the only module that sends. No module
  outside this package imports it (``test_reach_requests.py``).
* ``loop`` — the 60-second loop. ``routes`` — ``/reach/preview``,
  ``/reach/brief`` and ``/reach/tell-me``.
* ``push_message`` and ``push_channel`` — the web push of a reminder (WS-66
  RE-9a, RE-9b). The reminder loop plans and sends it. ``loop`` never runs
  it, and ``whatsapp`` never sees a ``web_push`` row.
"""
