"""No shrink before the seal (HRD-17e-1, task 3646).

Spec: ``project-docs/specs/chat_failure_modes.md`` §6 row HRD-17e-1, and
§4.7a rule 8.

A client write may update an agent row of a run before the run's fold seals
it (the S13 ``WHERE`` of ``routes/chat.py`` ``_MESSAGE_UPSERT_SQL``). Its
``content`` replaces the stored text, so an old second tab can write older,
shorter text over a newer reply. This module holds the two flags and the one
log line of the guard. ``routes/chat.py`` holds the SQL, and it decides which
write the guard reaches.

Rules that bind:

1. **Two per-org lists.** ``CHAT_NO_SHRINK_SHADOW_ORGS`` only logs. The write
   is the write of before, and one line names each client write that makes a
   stored row shorter. ``CHAT_NO_SHRINK_ORGS`` enforces. The stored text
   stays when the new text is shorter. An org in both lists enforces. Each
   list is empty by default. Then each write runs the SQL of before, byte for
   byte, with no extra read.
2. **The org is the tenant of the write.** The caller took it from the
   session, never from request input (R5).
3. **Only a client write of an agent row.** The fold, the mint, a human row
   and a system row never reach the guard. ``routes/chat.py`` makes that
   choice in one place (``_no_shrink_sql``).
4. **No text in the log.** The line names the chat, the message, the org and
   two lengths in characters, and one mark: ``prefix`` is true when the new
   text starts the stored text, as an older copy of the same reply does.

Fences: ``tests/unit/test_chat_upsert_no_shrink.py`` and
``tests/unit/test_chat_upsert_no_shrink_r8.py`` (R8).
"""
from __future__ import annotations

from acb_common import get_logger

_log = get_logger("gateway.chat")

#: The modes. :func:`no_shrink_mode` gives one of them.
OFF = "off"
SHADOW = "shadow"
ON = "on"

#: The one event of a write that the shadow mode would refuse.
SHADOW_EVENT = "chat_no_shrink_shadow"

#: The one event of a write whose stored text the guard kept.
REFUSED_EVENT = "chat_no_shrink_refused"


def _listed(raw: str, org: str) -> bool:
    entries = {part.strip().lower() for part in raw.split(",") if part.strip()}
    return "*" in entries or org in entries


def no_shrink_mode(organization_id: str | None) -> str:
    """The mode of *organization_id*: :data:`ON`, :data:`SHADOW` or :data:`OFF`.

    THE reader of ``CHAT_NO_SHRINK_ORGS`` and ``CHAT_NO_SHRINK_SHADOW_ORGS``.
    Each is a comma list of organization ids, or ``*``. An org in the first
    list is ON, even when the second list names it too. No org, a fault and
    two empty lists read OFF. It reads the settings on each call, so a reload
    (``docs/flag_reload.md``, SIGHUP) applies a change with no restart.
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return OFF
    try:
        from acb_common import get_settings

        settings = get_settings()
        enforce = str(getattr(settings, "chat_no_shrink_orgs", "") or "")
        shadow = str(getattr(settings, "chat_no_shrink_shadow_orgs", "") or "")
    except Exception:
        return OFF
    if _listed(enforce, org):
        return ON
    if _listed(shadow, org):
        return SHADOW
    return OFF


def log_shrink(
    event: str,
    *,
    session_id: str,
    message_id: str,
    organization_id: str | None,
    old_chars: int,
    new_chars: int,
    prefix: bool,
) -> None:
    """Write the ONE line of a shorter client write. It never holds the text.

    *event* is :data:`SHADOW_EVENT` or :data:`REFUSED_EVENT`, and any other
    name raises :class:`ValueError`. So a count in the production logs reads
    one fixed name for each mode.
    """
    if event not in (SHADOW_EVENT, REFUSED_EVENT):
        raise ValueError(f"unknown no-shrink event: {event!r}")
    write = _log.warning if event == REFUSED_EVENT else _log.info
    write(
        event,
        thread_id=str(session_id)[:12],
        message_id=str(message_id)[:40],
        org=str(organization_id or ""),
        old_chars=int(old_chars),
        new_chars=int(new_chars),
        prefix=bool(prefix),
    )
