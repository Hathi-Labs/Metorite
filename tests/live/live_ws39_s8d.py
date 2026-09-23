"""WS-39 S8 PR 2 — migration 216 drops the `gtd_*` task store (R8).

Spec `my_tasks_cutover.md` §5 S8 · board WS-39 · decisions D53.5, D73.

── Why this file exists ─────────────────────────────────────────────────────

Migration 216 is one-way. A drop that is wrong cannot be taken back, so each
claim below is proven against a real Postgres, in a database built for it:

  A. THE UPGRADE. A database in production's shape (the ladder before 216,
     with the three surviving tables under their OLD names) is seeded, moved
     by the real backfill, and then given 48, 52 and 216. Those are the files
     a deploy re-runs, because their checksums changed or they are new.
     1. Every `gtd_*` table, the view and both functions are gone.
     2. The WhatsApp commitment that pointed at a gtd row now points at the
        `pm_tasks` row it became, and `gtd_item_id` is gone.
     3. `attachments`, `my_tasks_horizons` and `my_tasks_reviews` hold the
        seeded rows. The D53.6 survivors still exist.
     4. A second run of all three files changes nothing.
  B. THE REFUSAL. The same shape with one gtd row the backfill never moved.
     216 must RAISE, and every table, column and row must stay where it was.

── How to run ───────────────────────────────────────────────────────────────

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run python tests/live/live_ws39_s8d.py

`LIVE_DSN` wins when set. Otherwise `TENANT_LADDER_DATABASE_URL` names the
server. The script makes its OWN scratch databases on that server and drops
them at the end, so it never changes the shared ladder database.
"""
from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

REPO = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO / "infra" / "postgres"
DROP_FILE = "216_gtd_task_store_drop.sql"
#: What a deploy re-runs on the upgrade: two edited files and the new one.
RERUN = ["48_task_manager_gtd.sql", "52_gtd_attachments.sql", DROP_FILE]

#: new name -> the name production still uses before this PR
RENAMED = {
    "attachments": "gtd_attachments",
    "my_tasks_horizons": "gtd_horizons",
    "my_tasks_reviews": "gtd_reviews",
}
DROPPED = [
    "gtd_items", "gtd_waiting", "gtd_projects", "gtd_spaces", "gtd_folders",
    "gtd_contexts", "gtd_retirement_arm",
]
SURVIVE = [
    "user_settings", "calendar_day_state", "calendar_rollover_log", "people",
    "people_absences", "people_credentials", "people_resumes", "people_skills",
    "task_accounts", "pm_tasks", "pm_task_personal",
]

TAG = uuid.uuid4().hex[:8]
WHO = f"dana-{TAG}@s8dtest.invalid"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def _server_url() -> str:
    raw = os.environ.get("LIVE_DSN") or os.environ["TENANT_LADDER_DATABASE_URL"]
    url = make_url(raw).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def _db_url(name: str) -> str:
    return make_url(_server_url()).set(database=name).render_as_string(
        hide_password=False)


def _run_file(conn: psycopg.Connection, filename: str) -> None:
    """One migration, verbatim, the way psql -f runs it. No parameters, so
    psycopg sends the simple protocol and one call carries many statements."""
    sql = (MIGRATIONS / filename).read_text(encoding="utf-8")
    conn.execute(sql)  # type: ignore[arg-type]


def _ladder_before_216() -> list[str]:
    found = []
    for p in MIGRATIONS.iterdir():
        m = re.match(r"^(\d+)_.*\.sql$", p.name)
        if m and not p.name.startswith(("00_", "01_")) and int(m.group(1)) < 216:
            found.append((int(m.group(1)), p.name))
    return [name for _, name in sorted(found)]


def _one(conn: psycopg.Connection, sql: str, *args):
    row = conn.execute(sql, args or None).fetchone()  # type: ignore[arg-type]
    return row[0] if row else None


def _exists(conn: psycopg.Connection, name: str) -> bool:
    return _one(conn, "SELECT to_regclass(%s) IS NOT NULL", f"public.{name}")


def _has_column(conn: psycopg.Connection, table: str, column: str) -> bool:
    return bool(_one(conn,
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
        table, column))


def build_template(admin: psycopg.Connection, name: str) -> None:
    """Production's shape before this PR: the ladder to 215, old names back."""
    admin.execute(f'CREATE DATABASE "{name}"')
    with psycopg.connect(_db_url(name), autocommit=True) as conn:
        _run_file(conn, "01_schema.sql")
        for filename in _ladder_before_216():
            _run_file(conn, filename)
        for new, old in RENAMED.items():
            conn.execute(f"ALTER TABLE {new} RENAME TO {old}")



def seed(conn: psycopg.Connection, *, unmigrated: bool = False,
         uncopied: bool = False, tree: bool = False) -> dict[str, str]:
    """A member with a moved store, in the shape production has.

    Each flag adds one thing 216 must refuse: `unmigrated` a row nobody moved,
    `uncopied` a moved row with `leveraged = true` (a column the backfill
    never copied), and `tree` a row in `gtd_projects`.
    """
    ids = {k: str(uuid.uuid4()) for k in (
        "org", "horizon", "item", "waiting_item", "stray", "project", "wa",
        "chat", "msg", "msg2", "k1", "k2", "meeting", "a1", "a2")}
    conn.execute("INSERT INTO organization (id, slug, display_name) "
                 "VALUES (%s, %s, 'S8d Ltd')", (ids["org"], f"s8dtest-{TAG}"))
    conn.execute("INSERT INTO app_user (email, display_name, organization_id) "
                 "VALUES (%s, 'Dana', %s)", (WHO, ids["org"]))

    conn.execute("INSERT INTO gtd_horizons (id, user_id, level, title) "
                 "VALUES (%s, %s, 3, 'Ship My Tasks')", (ids["horizon"], WHO))
    conn.execute("INSERT INTO gtd_reviews (user_id) VALUES (%s)", (WHO,))
    conn.execute("INSERT INTO gtd_attachments (user_id, name, path) "
                 "VALUES (%s, 'whiteboard.png', 'data/gtd_attachments/w.png')",
                 (WHO,))
    conn.execute(
        "INSERT INTO gtd_items (id, user_id, title, disposition, context) "
        "VALUES (%s, %s, 'Call the plumber', 'NEXT', '@calls')", (ids["item"], WHO))
    conn.execute(
        "INSERT INTO gtd_items (id, user_id, title, disposition) "
        "VALUES (%s, %s, 'Quote from Priya', 'WAITING')", (ids["waiting_item"], WHO))
    conn.execute(
        "INSERT INTO gtd_waiting (item_id, waiting_on, delegated_at) "
        "VALUES (%s, '{\"name\": \"Priya\"}'::jsonb, now())", (ids["waiting_item"],))

    moved = conn.execute("SELECT * FROM gtd_backfill_to_pm(true)").fetchall()
    ids["moved_rows"] = str(len(moved))

    if unmigrated:
        conn.execute(
            "INSERT INTO gtd_items (id, user_id, title, disposition) "
            "VALUES (%s, %s, 'Captured after the move', 'INBOX')", (ids["stray"], WHO))
    if uncopied:
        conn.execute("UPDATE gtd_items SET leveraged = true WHERE id = %s",
                     (ids["item"],))
    if tree:
        conn.execute("INSERT INTO gtd_projects (id, user_id, source, outcome) "
                     "VALUES (%s, %s, 'LOCAL', 'Kitchen Reno')", (ids["project"], WHO))

    conn.execute(
        "INSERT INTO wa_accounts (id, user_id, phone_number, phone_number_id, "
        "credentials_encrypted) VALUES (%s, %s, '+910000000000', %s, 'x')",
        (ids["wa"], WHO, f"pn-{TAG}"))
    conn.execute("INSERT INTO wa_chats (id, account_id, wa_chat_id) "
                 "VALUES (%s, %s, 'jid-1')", (ids["chat"], ids["wa"]))
    for msg, wamid in ((ids["msg"], "wamid.1"), (ids["msg2"], "wamid.2")):
        conn.execute("INSERT INTO wa_messages (id, account_id, chat_id, wa_message_id) "
                     "VALUES (%s, %s, %s, %s)", (msg, ids["wa"], ids["chat"], wamid))
    # One promise captured into the moved row, and one whose gtd row is gone.
    conn.execute(
        "INSERT INTO wa_commitments (id, account_id, chat_id, message_id, "
        "direction, text, gtd_item_id) VALUES (%s, %s, %s, %s, 'ours', "
        "'send the drawings', %s)",
        (ids["k1"], ids["wa"], ids["chat"], ids["msg"], ids["item"]))
    conn.execute(
        "INSERT INTO wa_commitments (id, account_id, chat_id, message_id, "
        "direction, text, gtd_item_id) VALUES (%s, %s, %s, %s, 'ours', "
        "'call back', %s)",
        (ids["k2"], ids["wa"], ids["chat"], ids["msg2"], str(uuid.uuid4())))

    # A meeting action approved into the old store (migration 129's
    # convention: kind = 'task' puts the gtd id in dispatch_ref), and an email
    # action whose ref is not a task id at all.
    conn.execute("INSERT INTO meeting (id, title, platform, start_at) "
                 "VALUES (%s, 'Site visit', 'meet', now())", (ids["meeting"],))
    conn.execute(
        "INSERT INTO action_item (id, meeting_id, description, kind, dispatch_ref) "
        "VALUES (%s, %s, 'Call the plumber', 'task', %s)",
        (ids["a1"], ids["meeting"], ids["item"]))
    conn.execute(
        "INSERT INTO action_item (id, meeting_id, description, kind, dispatch_ref) "
        "VALUES (%s, %s, 'Send the notes', 'email', %s)",
        (ids["a2"], ids["meeting"], f"sent:{ids['item']}"))
    return ids


def scenario_upgrade(admin: psycopg.Connection, template: str) -> None:
    name = f"ws39_s8d_up_{TAG}"
    admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
    with psycopg.connect(_db_url(name), autocommit=True) as conn:
        ids = seed(conn)
        task = _one(conn, "SELECT migrated_task_id FROM gtd_items WHERE id = %s",
                    ids["item"])
        check("A0 the backfill moved both seeded rows",
              task is not None and _one(conn,
                  "SELECT count(*) FROM gtd_items WHERE migrated_task_id IS NULL") == 0,
              f"moved={ids['moved_rows']} task={task}")

        for filename in RERUN:
            _run_file(conn, filename)

        gone = [t for t in DROPPED if _exists(conn, t)]
        check("A1 every gtd_ table is gone", not gone, f"left={gone}")
        left = _one(conn,
            "SELECT string_agg(relname, ',') FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r','v') "
            "AND c.relname LIKE 'gtd%%'")
        check("A2 no relation named gtd_* remains (\\dt gtd_*)", left is None,
              f"left={left}")
        funcs = _one(conn, "SELECT string_agg(proname, ',') FROM pg_proc "
                           "WHERE proname LIKE 'gtd%%'")
        check("A3 the backfill and the guard functions are gone", funcs is None,
              f"left={funcs}")

        check("A4 wa_commitments.gtd_item_id is gone",
              not _has_column(conn, "wa_commitments", "gtd_item_id"))
        k1 = _one(conn, "SELECT task_id::text FROM wa_commitments WHERE id = %s",
                  ids["k1"])
        check("A5 the commitment points at the pm task its gtd row became",
              k1 == str(task), f"task_id={k1} want={task}")
        title = _one(conn, "SELECT title FROM pm_tasks WHERE id = %s", k1)
        check("A6 that pm task exists and carries the title",
              title == "Call the plumber", f"title={title}")
        k2 = _one(conn, "SELECT task_id FROM wa_commitments WHERE id = %s", ids["k2"])
        check("A7 a commitment whose gtd row is gone keeps no pointer", k2 is None,
              f"task_id={k2}")

        check("A8 attachments holds the seeded file",
              _one(conn, "SELECT name FROM attachments WHERE user_id = %s", WHO)
              == "whiteboard.png")
        check("A9 my_tasks_horizons holds the seeded horizon",
              _one(conn, "SELECT title FROM my_tasks_horizons WHERE id = %s",
                   ids["horizon"]) == "Ship My Tasks")
        check("A10 my_tasks_reviews holds the seeded review",
              _one(conn, "SELECT count(*) FROM my_tasks_reviews WHERE user_id = %s",
                   WHO) == 1)
        old = [o for o in RENAMED.values() if _exists(conn, o)]
        check("A11 no old name survives beside a new one", not old, f"old={old}")
        missing = [t for t in SURVIVE if not _exists(conn, t)]
        check("A12 the D53.6 survivors and the one store are intact",
              not missing, f"missing={missing}")

        try:
            for filename in RERUN:
                _run_file(conn, filename)
            again = True
            detail = ""
        except psycopg.Error as exc:  # pragma: no cover - reported, not raised
            again, detail = False, str(exc).splitlines()[0]
        check("A13 a second run of 48, 52 and 216 is a no-op",
              again and _one(conn, "SELECT task_id::text FROM wa_commitments "
                                   "WHERE id = %s", ids["k1"]) == str(task)
              and not any(_exists(conn, t) for t in DROPPED),
              detail)


def scenario_refusal(admin: psycopg.Connection, template: str, *, tag: str,
                     label: str, expect: str, **flags: bool) -> None:
    """216 must RAISE with `expect` in the message and change nothing."""
    name = f"ws39_s8d_{tag}_{TAG}"
    admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
    with psycopg.connect(_db_url(name), autocommit=True) as conn:
        ids = seed(conn, **flags)
        before = {t: _one(conn, f"SELECT count(*) FROM {t}")
                  for t in DROPPED if t != "gtd_retirement_arm"}
        for filename in RERUN[:-1]:
            _run_file(conn, filename)

        try:
            _run_file(conn, DROP_FILE)
            raised, message = False, ""
        except psycopg.Error as exc:
            raised, message = True, str(exc).splitlines()[0]
            conn.execute("ROLLBACK")
        p = tag.upper()
        check(f"{p}1 216 RAISES: {label}",
              raised and expect in message, f"message={message!r}")

        after = {t: (_one(conn, f"SELECT count(*) FROM {t}")
                     if _exists(conn, t) else None) for t in before}
        check(f"{p}2 every gtd_ table is still there, with every row",
              after == before, f"before={before} after={after}")
        check(f"{p}3 the arm row was taken back with the rest",
              _exists(conn, "gtd_retirement_arm")
              and _one(conn, "SELECT count(*) FROM gtd_retirement_arm") == 0)
        check(f"{p}4 wa_commitments.gtd_item_id is still there, value unchanged",
              _has_column(conn, "wa_commitments", "gtd_item_id")
              and _one(conn, "SELECT gtd_item_id::text FROM wa_commitments "
                             "WHERE id = %s", ids["k1"]) == ids["item"]
              and _one(conn, "SELECT task_id FROM wa_commitments WHERE id = %s",
                       ids["k1"]) is None)
        check(f"{p}5 the task action still names the gtd row",
              _one(conn, "SELECT dispatch_ref FROM action_item WHERE id = %s",
                   ids["a1"]) == ids["item"])
        check(f"{p}6 the guard, the backfill and its preview are still there",
              _one(conn, "SELECT count(*) FROM pg_proc WHERE proname IN "
                         "('gtd_retirement_drop', 'gtd_backfill_to_pm')") == 2
              and _exists(conn, "gtd_backfill_plan"))


REFUSALS = [
    dict(tag="b", label="one gtd_items row was never migrated",
         expect="S3c REFUSED", unmigrated=True),
    dict(tag="c", label="a migrated row holds leveraged = true",
         expect="gtd_items rows hold a value in leveraged", uncopied=True),
]


def main() -> None:
    template = f"ws39_s8d_tpl_{TAG}"
    made = [template, f"ws39_s8d_up_{TAG}",
            *(f"ws39_s8d_{r['tag']}_{TAG}" for r in REFUSALS)]
    admin = psycopg.connect(make_url(_server_url()).set(database="postgres")
                            .render_as_string(hide_password=False), autocommit=True)
    try:
        build_template(admin, template)
        scenario_upgrade(admin, template)
        for refusal in REFUSALS:
            scenario_refusal(admin, template, **refusal)
    finally:
        for name in reversed(made):
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()

    width = max(len(n) for n, _, _ in results)
    failed = 0
    for name, ok, detail in results:
        pad = "." * (width - len(name) + 3)
        print(f"  {name} {pad} {'PASS' if ok else 'FAIL: ' + detail}")
        failed += 0 if ok else 1
    print(f"{len(results) - failed}/{len(results)} PASS")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    main()
