"""Live check of the calendar write-out against REAL Google and Microsoft Graph.

Two facts that no recorded reply can prove (WS-CAL CE-13b and CE-13c):

* Google. A create with a client id that a deleted event holds answers HTTP 409
  (``exists``). A PATCH with ``status: confirmed`` (the adopt, ``revive``) brings
  the cancelled event back. A plain PATCH of a cancelled event must NOT.
* Graph. A repeat create with the same ``transactionId`` answers with the
  event that the first create made, and no second event exists.

An OPERATOR runs it, once, before ``CALENDAR_SYNC_WRITE`` is turned on::

    uv run python scripts/calendar_live_check.py --live --org <org id> --connection <connection id>

Rules it keeps (each is fenced by ``tests/unit/test_calendar_live_check.py``):

* It runs ONLY with ``--live``. Without it, it prints why and exits 2.
* It never flips a flag and never prints a token, a secret or an account name.
* It writes ONLY into the calendar "Metorite" of that one connection. It never
  makes that calendar: when the connection has none, it stops.
* Each event has no attendees (the provider sends no mail), is free time, and is
  deleted at the end, also when a check fails.
* Exit 0 when every check passes, 1 when a check fails, 2 on a refusal or setup
  error.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

TITLE = "Metorite live check (safe to delete)"


@dataclass
class Row:
    """One line of the table. ``ok`` is None for information with no verdict."""

    check: str
    expected: str
    got: str
    ok: bool | None


def _event(client_id: str, now: datetime) -> Any:
    from gateway.calendar_sync.provider import OutEvent

    start = now + timedelta(days=2)  # free time, no attendees, gone in seconds
    return OutEvent(title=TITLE, starts_at=start, ends_at=start + timedelta(hours=1),
                    busy=False, client_id=client_id)


async def _quiet_delete(provider: Any, cred: Any, cal_id: str, event_id: str) -> None:
    # Cleanup must never hide the verdict.
    with contextlib.suppress(Exception):
        await provider.delete_event(cred, cal_id, event_id)


async def check_google(provider: Any, cred: Any, cal_id: str, *, now: datetime | None = None,
                       client_id: str | None = None) -> list[Row]:
    """The adopt-by-PATCH of CE-13b."""
    from gateway.calendar_sync.provider import ProviderError, Window

    now = now or datetime.now(UTC)
    cid = client_id or "lc" + uuid.uuid4().hex[:24]  # base32hex: legal for a Google id
    ev = _event(cid, now)
    rows: list[Row] = []

    async def alive() -> bool:
        res = await provider.sync(cred, None, Window.around(now), calendar_id=cal_id)
        return any(c.external_id == cid and not c.deleted and c.status != "cancelled"
                   for c in res.changes)

    try:
        made = await provider.upsert_event(cred, cal_id, ev, None)
        rows.append(Row("create with a client id", "the event id is the client id",
                        "same" if made.external_id == cid else "different",
                        made.external_id == cid))
        await provider.delete_event(cred, cal_id, cid)
        rows.append(Row("delete it", "no error", "deleted", True))
        try:
            await provider.upsert_event(cred, cal_id, ev, None)
            rows.append(Row("create again with the SAME client id", "HTTP 409 (exists)",
                            "created again, no 409", False))
        except ProviderError as exc:
            rows.append(Row("create again with the SAME client id", "HTTP 409 (exists)",
                            exc.code, exc.code == "exists"))
        await provider.upsert_event(cred, cal_id, replace(ev, revive=True), cid)
        back = await alive()
        rows.append(Row("adopt PATCH with status confirmed", "the cancelled event is back",
                        "back" if back else "still cancelled", back))
        await provider.delete_event(cred, cal_id, cid)
        try:
            await provider.upsert_event(cred, cal_id, ev, cid)  # an ORDINARY update
            stays = not await alive()
            got = "stays cancelled" if stays else "came back"
        except ProviderError as exc:
            stays, got = True, f"refused ({exc.code})"
        rows.append(Row("plain PATCH without revive", "the event stays cancelled", got, stays))
    except ProviderError as exc:
        rows.append(Row("a provider call", "no error", f"failed ({exc.code}); the rest was skipped", False))
    finally:
        await _quiet_delete(provider, cred, cal_id, cid)
    return rows


async def check_graph(provider: Any, cred: Any, cal_id: str, *, now: datetime | None = None,
                      transaction_id: str | None = None) -> list[Row]:
    """The transactionId dedupe of CE-13c."""
    from gateway.calendar_sync.provider import ProviderError

    now = now or datetime.now(UTC)
    tx = transaction_id or "lc" + uuid.uuid4().hex[:24]
    ev = _event(tx, now)
    made: list[str] = []
    rows: list[Row] = []
    try:
        first = await provider.upsert_event(cred, cal_id, ev, None)
        made.append(first.external_id)
        try:
            again = await provider.upsert_event(cred, cal_id, ev, None)
            made.append(again.external_id)
            same = again.external_id == first.external_id
            rows.append(Row("repeat create with the SAME transactionId",
                            "the SAME event id (no second event)",
                            "same id" if same else "a second event", same))
        except ProviderError as exc:
            rows.append(Row("repeat create with the SAME transactionId",
                            "the SAME event id (no second event)", f"answered {exc.code}", False))
        await provider.delete_event(cred, cal_id, first.external_id)
        third = await provider.upsert_event(cred, cal_id, ev, None)
        made.append(third.external_id)
        rows.append(Row("create again after the delete (information)",
                        "a NEW event (the writer salts the id for each new link anyway)",
                        "the binned event came back" if third.external_id == first.external_id
                        else "a new event", None))
    except ProviderError as exc:
        rows.append(Row("a provider call", "no error", f"failed ({exc.code}); the rest was skipped", False))
    finally:
        for event_id in dict.fromkeys(made):
            await _quiet_delete(provider, cred, cal_id, event_id)
    return rows


def render(rows: list[Row]) -> str:
    """The PASS or FAIL table. Plain text, no secret."""
    verdict = {True: "PASS", False: "FAIL", None: "info"}
    width = max([len(r.check) for r in rows] + [5])
    exp = max([len(r.expected) for r in rows] + [8])
    lines = [f"{'check':<{width}}  {'expected':<{exp}}  {'got':<28}  result",
             f"{'-' * width}  {'-' * exp}  {'-' * 28}  ------"]
    lines += [f"{r.check:<{width}}  {r.expected:<{exp}}  {r.got:<28}  {verdict[r.ok]}" for r in rows]
    bad = sum(1 for r in rows if r.ok is False)
    lines.append("")
    lines.append("ALL CHECKS PASS" if not bad else f"{bad} CHECK(S) FAILED")
    return "\n".join(lines)


def exit_code(rows: list[Row]) -> int:
    return 1 if any(r.ok is False for r in rows) else 0


REFUSAL = ("calendar_live_check: refused. This script writes real events (and deletes them) in the "
           "calendar \"Metorite\" of ONE connection. Pass --live, --org and --connection to run it.")


async def _run(org: str, connection_id: str) -> int:
    from acb_common.db import tenant_session
    from gateway.calendar_sync import connections
    from gateway.calendar_sync.scheduler import PROVIDERS

    async with tenant_session(org) as db:
        conn = await connections.get_connection(db, org, connection_id)
    if conn is None or conn.status != "connected":
        print("calendar_live_check: no connected calendar connection with that id in that org.")
        return 2
    cal_id = connections.metorite_calendar_id(conn)
    if not cal_id:
        print("calendar_live_check: this connection has no calendar \"Metorite\" yet. It makes none; "
              "run one task pass with the write flag on for a test org first.")
        return 2
    factory = PROVIDERS.get(conn.provider)
    provider = factory() if factory else None
    if provider is None:
        print("calendar_live_check: the provider is not set up on this box.")
        return 2
    try:
        cred = await connections.fresh_credential(org, conn, provider)
    except Exception as exc:
        print(f"calendar_live_check: the token cannot be used ({type(exc).__name__}). Reconnect the account.")
        return 2
    if cred is None:
        print("calendar_live_check: no usable token. Reconnect the account.")
        return 2
    print(f"connection {connection_id} ({provider.provider}), calendar \"Metorite\" found")
    try:
        if provider.provider == "google_calendar":
            rows = await check_google(provider, cred, cal_id)
        else:
            rows = await check_graph(provider, cred, cal_id)
    except Exception as exc:
        print(f"calendar_live_check: stopped by {type(exc).__name__}. Events it made were deleted.")
        return 2
    print(render(rows))
    return exit_code(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Live check of the calendar write-out (operator only).")
    ap.add_argument("--live", action="store_true", help="really call Google or Microsoft")
    ap.add_argument("--org", help="the organization id")
    ap.add_argument("--connection", help="the calendar connection id")
    args = ap.parse_args(argv)
    if not (args.live and args.org and args.connection):
        print(REFUSAL)
        return 2
    return asyncio.run(_run(args.org, args.connection))


if __name__ == "__main__":
    sys.exit(main())
