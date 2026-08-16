"""WS-28q against a REAL Postgres (R8).

The hermetic suite runs the real encoder, so the image half is already honest.
What it cannot answer is what happens when the result meets the database:

* a ~30 KB data URI **round-trips through a TEXT column** unchanged — base64 is
  ASCII, but a value that large through a bound parameter is worth proving once
  rather than assuming;
* the write reaches the row through the real endpoint and comes back on the
  real read, at the **directory tier** — a display image is what a directory
  IS, so a colleague with no HR grant must still see it;
* migration 173 applies on top of the full ladder, twice.

Run it::

    su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D <datadir> \\
        -o '-k /var/tmp -p 55432' start"
    uv run python tests/live/live_ws28q.py

⚠️ Writes and deletes `gtd_people` rows under `@ws28q.invalid`. Scratch only.
"""
import asyncio
import base64
import os
import sys

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432",
)
sys.path.insert(0, "/home/user/Metorite/apps/services/gateway")

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, release_tenant
from gateway import avatar as avatar_mod
from gateway.db import get_db
from gateway.routes.people import core as people_core
from gateway.routes.people import profile as people_profile
from gateway.routes.people import selfservice as people_self
from gateway.routes.tasks import people as tasks_people
from sqlalchemy import text

SUBJECT_EMAIL = "priya@ws28q.invalid"

failures: list[str] = []


def check(label: str, got, want) -> None:
    """Compare, and print something a human can read.

    A data URI is ~30 KB, and printing two of them per assertion buries every
    other line in the run — the output of a verification harness nobody scrolls
    through is a verification harness nobody reads. Long values are elided to
    their head and length, which is still enough to see WHICH value differs.
    """
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {_short(got)}, want {_short(want)}")
    if not ok:
        failures.append(label)


def _short(value, limit: int = 60) -> str:
    text_form = repr(value)
    if len(text_form) <= limit:
        return text_form
    return f"{text_form[:limit]}… ({len(text_form)} chars)"


def user(email, *grants) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


ADMIN = user("admin@ws28q.invalid", "feature:people", "admin:members:manage",
             "admin:members:read")
SUBJECT = user(SUBJECT_EMAIL)                    # a colleague with NO grants
COLLEAGUE = user("other@ws28q.invalid", "feature:people")


class Upload:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self) -> bytes:
        return self._data


def photo(width: int, height: int) -> bytes:
    """A BANDED image, not a flat one.

    A solid colour encodes to the same JPEG whatever region is cropped, so a
    flat fixture made "the admin replaced it" pass or fail for reasons that had
    nothing to do with the code — the first run of this harness reported a
    failure that was entirely the fixture's.
    """
    import fitz

    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
    for index, colour in enumerate(((40, 90, 160), (200, 60, 40), (30, 170, 90))):
        left = index * width // 3
        right = width if index == 2 else (index + 1) * width // 3
        pix.set_rect(fitz.IRect(left, 0, right, height), colour)
    return pix.tobytes("png")


async def main() -> None:
    db = await get_db()
    org = (await db.execute(
        text("SELECT id FROM organization ORDER BY created_at LIMIT 1"))).fetchone()
    token = bind_tenant(str(org.id))
    try:
        await db.execute(text(
            "DELETE FROM gtd_people WHERE email LIKE '%@ws28q.invalid'"))
        await db.commit()

        # ── The column exists and takes a data URI ─────────────────────────
        cols = (await db.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'gtd_people' "
            "AND column_name IN ('avatar', 'avatar_updated_at') "
            "ORDER BY column_name"))).fetchall()
        check("migration 173 applied",
              [(c.column_name, c.data_type) for c in cols],
              [("avatar", "text"),
               ("avatar_updated_at", "timestamp with time zone")])

        person = await tasks_people.create_person(
            tasks_people.PersonWrite(name="Priya WS28Q", email=SUBJECT_EMAIL),
            ADMIN)

        # ── An ungranted member sets their own, through the real endpoint ──
        out = await people_self.upload_my_avatar(
            Upload(photo(1200, 400)), 0.0, 0.0, 1.0, SUBJECT)
        check("an ungranted member sets their own picture",
              out["avatar"].startswith("data:image/jpeg;base64,"), True)

        # ── It round-trips through the TEXT column unchanged ───────────────
        row = (await db.execute(
            text("SELECT avatar, avatar_updated_at FROM gtd_people "
                 "WHERE id = CAST(:id AS uuid)"),
            {"id": person.id})).fetchone()
        check("the stored value is byte-identical to what was returned",
              row.avatar, out["avatar"])
        check("…and it stamped when", row.avatar_updated_at is not None, True)

        jpeg = base64.b64decode(row.avatar.split(",", 1)[1])
        check("what came back out of Postgres is still a JPEG",
              jpeg[:3], b"\xff\xd8\xff")

        import fitz
        stored = fitz.Pixmap(jpeg)
        check("…of exactly the fixed size",
              (stored.width, stored.height),
              (avatar_mod.AVATAR_PX, avatar_mod.AVATAR_PX))
        check("…and small", len(jpeg) < 60_000, True)

        # ── Directory tier: a colleague with no HR grant still sees it ─────
        full = (await db.execute(
            text("SELECT * FROM gtd_people WHERE id = CAST(:id AS uuid)"),
            {"id": person.id})).fetchone()
        seen = await people_core.person_payload(db, full, COLLEAGUE)
        check("a colleague sees the picture", seen["avatar"], row.avatar)
        check("…and still not their skills", seen["skills"], [])

        # ── A refusal never reaches the column ─────────────────────────────
        before = row.avatar
        try:
            await people_self.upload_my_avatar(
                Upload(b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
                0.0, 0.0, 1.0, SUBJECT)
            check("an SVG is refused", "stored", "400")
        except Exception as exc:
            check("an SVG is refused", getattr(exc, "status_code", None), 400)
        after = (await db.execute(
            text("SELECT avatar FROM gtd_people WHERE id = CAST(:id AS uuid)"),
            {"id": person.id})).fetchone()
        check("…and the old picture is untouched", after.avatar, before)

        # ── An admin may replace somebody else's, and a crop lands ─────────
        zoomed = await people_profile.upload_avatar(
            person.id, Upload(photo(1200, 400)), 0.6, 0.1, 0.5, ADMIN)
        check("an admin may replace it",
              zoomed["avatar"] != before, True)
        check("…and it is still the fixed size",
              fitz.Pixmap(base64.b64decode(
                  zoomed["avatar"].split(",", 1)[1])).width,
              avatar_mod.AVATAR_PX)

        # ── Removal falls back to initials, and says when ──────────────────
        cleared = await people_self.delete_my_avatar(SUBJECT)
        check("removing it clears the column", cleared["avatar"], None)
        stamp = (await db.execute(
            text("SELECT avatar, avatar_updated_at FROM gtd_people "
                 "WHERE id = CAST(:id AS uuid)"),
            {"id": person.id})).fetchone()
        check("…in the database too", stamp.avatar, None)
        check("…and the change is still stamped",
              stamp.avatar_updated_at is not None, True)
    finally:
        await db.execute(text(
            "DELETE FROM gtd_people WHERE email LIKE '%@ws28q.invalid'"))
        await db.commit()
        release_tenant(token)
        await db.close()

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all checks passed")


asyncio.run(main())
