"""The two digest reads as they were BEFORE WS-58 MD-3d, word for word.

MD-3d gave ``_digest_thread_list`` the optional bounds ``before``, ``after``
and ``this_tenant``, and moved the body of ``_digest_commitments`` into a
core that raises (``my_day_feed.md`` §5.9 item 12). The live email digest
must not change. So these copies are the base code of ``ca1d8d6d8``, and the
fences run the digest once with them and once with the code of today:

* ``test_email_digest.py::TestTheDigestDidNotChange`` compares each SQL
  statement, each bound parameter and the output, with a recording fake.
* ``test_feed_mail_kinds_r8.py::TestTheDigestOnARealDatabase`` compares the
  output on a seeded mailbox (R8).

Each name that the old code read from its module is read from ``digest``
here, at the call, so a test can patch it in one place. Do not edit the
bodies: they are the record of what the digest sent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from gateway.routes.email import digest as d
from sqlalchemy import text


async def old_digest_thread_list(
    db: Any, account_id: str, status: str, limit: int,
    *, prioritized: bool = False, with_time: bool = False,
) -> list[dict[str, Any]]:
    order = (f"{d._PRIORITY_SCORE} DESC, ts.last_message_at ASC"
             if prioritized else "ts.last_message_at ASC")
    rows = (await db.execute(text(
        f"""SELECT ts.thread_id, ts.last_message_id, em.subject,
                   em.from_address->>'name'  AS from_name,
                   em.from_address->>'email' AS from_email,
                   em.to_addresses->0->>'name'  AS to_name,
                   em.to_addresses->0->>'email' AS to_email,
                   LOWER(COALESCE(em.importance, '')) = 'high' AS high,
                   em.is_read AS is_read,
                   GREATEST(0, EXTRACT(DAY FROM now() - ts.last_message_at))::int
                     AS age_days,
                   ts.last_message_at AS last_at
            FROM email_thread_status ts
            LEFT JOIN email_messages em ON ts.last_message_id = em.id
            WHERE ts.account_id = :aid AND ts.status = '{status}'
              AND ts.last_message_at IS NOT NULL
              AND {d._LIVE_THREAD}
            ORDER BY {order}
            LIMIT :lim"""
    ), {"aid": account_id, "lim": limit})).fetchall()
    selves = await d.resolve_self_addresses(db, account_id)

    out: list[dict[str, Any]] = []
    for r in rows:
        ours = (r.from_email or "").lower() in selves
        who = ((r.to_name or r.to_email) if ours
               else (r.from_name or r.from_email)) or ""
        item = {
            "subject": (r.subject or "(no subject)"),
            "age_days": r.age_days,
            "thread_id": r.thread_id,
            "message_id": (str(r.last_message_id)
                           if r.last_message_id else None),
            "who": who,
            "important": bool(r.high),
            "unread": (r.is_read is False),
        }
        if with_time:
            item["last_message_at"] = (
                r.last_at.isoformat() if r.last_at is not None else None)
        out.append(item)
    return out


async def old_digest_commitments(
    db: Any, account_id: str, horizon_days: int = 3, limit: int = 8,
    *, include_undated: bool = False,
) -> list[dict[str, Any]]:
    from gateway.routes.tasks.item_source import item_source

    try:
        owner = (await db.execute(text(
            "SELECT user_id FROM email_accounts WHERE id = :aid"
        ), {"aid": account_id})).fetchone()
        uid = str(getattr(owner, "user_id", "") or "") if owner else ""
        if not uid:
            return []
        items = await item_source().items_by_origin(
            db, uid, "account_id", str(account_id))
        at = datetime.now(UTC)
        until = at + timedelta(days=horizon_days)
        picked: list[tuple[Any, datetime | None]] = []
        for it in items:
            due = d._as_utc(getattr(it, "due_at", None))
            if (due is not None and due <= until) or (
                    include_undated and due is None):
                picked.append((it, due))
        picked.sort(key=lambda p: (p[1] is None, p[1] or at))
        picked = picked[:limit]
        threads = sorted({
            str(d._origin_of(it).get("thread_id"))
            for it, _due in picked if d._origin_of(it).get("thread_id")
        })
        latest: dict[str, str] = {}
        if threads:
            rows = (await db.execute(text(
                """SELECT DISTINCT ON (em.thread_id)
                          em.thread_id, em.id AS message_id
                   FROM email_messages em
                   WHERE em.account_id = :aid
                     AND em.thread_id = ANY(:tids)
                   ORDER BY em.thread_id, em.received_at DESC NULLS LAST"""
            ), {"aid": account_id, "tids": threads})).fetchall()
            latest = {str(r.thread_id): str(r.message_id) for r in rows}
    except Exception as exc:
        try:  # noqa: SIM105  (the old body, word for word)
            await db.rollback()
        except Exception:
            pass
        d._log.warning("email.digest_commitments_failed", error=str(exc))
        return []
    out: list[dict[str, Any]] = []
    for it, due in picked:
        thread_id = d._origin_of(it).get("thread_id") or None
        out.append({
            "title": (it.title or "(untitled)"),
            "due": due.strftime("%b %d") if due else None,
            "overdue": bool(due is not None and due < at),
            "task_id": str(it.id),
            "thread_id": thread_id,
            "message_id": latest.get(str(thread_id)) if thread_id else None,
        })
    return out
