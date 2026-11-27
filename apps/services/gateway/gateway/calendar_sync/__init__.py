"""Calendar sync with Google and Outlook (WS-CAL CE-10, CE-11).

The provider-neutral seam: ``provider`` (the protocol), ``http`` (the one
guarded door), ``connections`` (the token, the cursor), ``writer`` (the rows),
``engine`` (one connection) and ``scheduler`` (the 60 s loop). A provider is
one file beside them. Dark behind ``CALENDAR_SYNC_READ``.
"""
