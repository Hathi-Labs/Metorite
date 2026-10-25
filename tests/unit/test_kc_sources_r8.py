"""R8 — Knowledge sources on a real database (WS-61 KC-1a).

Spec: ``project-docs/specs/knowledge_curation.md`` §7.2 step 1, §7.4, §16,
§19 and the KC-1 row of §22. ``knowledge_base.md`` §7.1, §7.2 and §7.4.

What this proves against a REAL Postgres, through the real ``/knowledge``
router, as the NON-privileged role of the phase-4 rehearsal (no superuser, no
BYPASSRLS), on a database with the whole ladder, the four generated phases
and two organizations. Google is a fake: no test reaches the network.

- after the callback, discovery stores the tree, and
  ``GET /knowledge/sources/{id}/tree`` answers the nodes with no ``text``,
  ``content`` or ``body`` key (the KC-1 done-when);
- org B reads no source and no node of org A, through the routes and as raw
  SQL under ``tenant_session``;
- a member sees only their own source, and a ``knowledge:manage`` holder
  sees every source;
- the node upsert is idempotent, and a node the provider no longer lists
  becomes ``gone``;
- both tables FORCE row level security, and WITH CHECK refuses a write for
  another org;
- ``kb_google_drive_grant_shared`` and the replaced ``email_gmail_grant_shared``
  answer true, false and NULL as designed, in both directions;
- a Drive disconnect next to a live Gmail mailbox revokes nothing, keeps
  the Gmail row, and deletes the Drive blob and the nodes;
- the migration replays.

A hermetic fake agrees with any SQL it is handed, so none of these could be
proved without the database. ``tests/unit/test_kc_sources_google_grant.py``
holds the gateway half with no database.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _APP_ROLE,
    _DB_GATE,
    _URL,
    app_engine,
    promoted,
)
from tests.unit.test_kb_pages_r8 import (  # noqa: F401
    CAPABILITY,
    _address,
    _app_dsn,
    _assert_non_priv,
    _call,
    _ok,
    _principal,
    ns,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"
NIL = "00000000-0000-0000-0000-000000000000"
_FORBIDDEN_KEYS = {"text", "content", "body", "body_md"}


def _sources_migration() -> Path:
    """The migration that creates ``kb_sources``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS kb_sources" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


class FakeGoogle:
    """The faked Google client of one Google account. Records each call."""

    def __init__(self, email: str, account_id: str, files: list[dict[str, Any]],
                 *, pages: int = 2) -> None:
        self.email, self.account_id = email, account_id
        self.files, self.pages = files, pages
        self.calls: list[tuple[str, str]] = []

    async def get(self, url: str, *, params: Any = None, headers: Any = None) -> Any:
        import json

        from gateway.kb_sources.http import HttpAnswer

        params = dict(params or {})
        self.calls.append(("GET", url))
        assert "alt" not in params and "export" not in url, "discovery read a document"
        assert (headers or {}).get("Authorization", "").startswith("Bearer ")
        if url.endswith("/about"):
            body: dict[str, Any] = {"user": {"emailAddress": self.email,
                                             "permissionId": self.account_id,
                                             "displayName": "Ana"}}
        elif url.endswith("/files/root"):
            body = {"id": f"root-{self.account_id}", "name": "My Drive"}
        elif url.endswith("/drives"):
            body = {"drives": [{"id": f"sd-{self.account_id}", "name": "Sales"}]}
        elif url.endswith("/files"):
            assert params["corpora"] == "allDrives"
            assert params["supportsAllDrives"] == "true"
            assert params["includeItemsFromAllDrives"] == "true"
            assert "fields" in params
            half = (len(self.files) + 1) // 2 if self.pages == 2 else len(self.files)
            folder = re.fullmatch(r"'([A-Za-z0-9_-]+)' in parents and trashed = false",
                                  params["q"])
            if folder is not None:
                # The level walk: the children of one folder, one page.
                body = {"files": [f for f in self.files
                                  if (f.get("parents") or [None])[0] == folder.group(1)]}
            elif params.get("pageToken") == "p2":
                body = {"files": self.files[half:]}
            else:
                body = {"files": self.files[:half]}
                if self.pages == 2:
                    body["nextPageToken"] = "p2"
        else:
            raise AssertionError(f"an unknown Google read: {url}")
        return HttpAnswer(status_code=200, body=json.dumps(body).encode())

    async def post(self, url: str, *, form: Any, headers: Any = None) -> Any:
        import json

        from gateway.kb_sources.google_drive import auth
        from gateway.kb_sources.http import HttpAnswer

        self.calls.append(("POST", url))
        if url == auth.TOKEN_URL:
            body = {"access_token": f"at-{self.account_id}", "expires_in": 3600,
                    "refresh_token": f"rt-{self.account_id}",
                    "scope": f"openid https://www.googleapis.com/auth/userinfo.email "
                             f"{auth.DRIVE_READONLY}"}
            return HttpAnswer(status_code=200, body=json.dumps(body).encode())
        if url == auth.REVOKE_URL:
            return HttpAnswer(status_code=200, body=b"{}")
        raise AssertionError(f"an unknown Google POST: {url}")

    def revokes(self) -> int:
        from gateway.kb_sources.google_drive import auth

        return sum(1 for verb, url in self.calls if verb == "POST" and url == auth.REVOKE_URL)


def _tree(account_id: str) -> list[dict[str, Any]]:
    root, sd = f"root-{account_id}", f"sd-{account_id}"
    return [
        {"id": f"fold-{account_id}", "name": "Contracts", "parents": [root],
         "mimeType": "application/vnd.google-apps.folder",
         "webViewLink": f"https://drive.google.com/drive/folders/fold-{account_id}"},
        {"id": f"file-{account_id}", "name": "MSA.pdf", "parents": [f"fold-{account_id}"],
         "mimeType": "application/pdf", "size": "1234",
         "modifiedTime": "2026-10-20T08:00:00.000Z",
         "webViewLink": f"https://drive.google.com/file/d/file-{account_id}/view"},
        {"id": f"deck-{account_id}", "name": "Pitch", "parents": [sd], "driveId": sd,
         "mimeType": "application/vnd.google-apps.presentation"},
        {"id": f"orph-{account_id}", "name": "Shared with me.docx",
         "parents": ["not-visible"], "mimeType": "application/msword"},
    ]


@pytest.fixture
def kc(ns, monkeypatch):  # noqa: F811 — a fixture
    """The KB-1 fixture with the source flags on, a fake Google per member."""
    import acb_llm.key_store as key_store
    from acb_common import get_settings
    from gateway.kb_sources.google_drive import GoogleDriveSource, auth
    from gateway.routes.knowledge import sources

    settings = get_settings()
    for name, value in {
        "knowledge_sources": "*",
        "knowledge_source_google": "*",
        "gateway_session_secret": "kc1a-r8-session-secret-not-a-default-0123",
        "acb_master_key": "kc1a-r8-master-key-for-the-blob",
    }.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    monkeypatch.setattr(key_store, "_store", None)
    monkeypatch.setattr(sources, "google_client", lambda: auth.OAuthClientConfig(
        client_id="cid.apps.googleusercontent.com", client_secret="r8-secret",
        redirect_uri="https://app.test/api/knowledge/sources/google_drive/callback"))
    fakes: dict[str, FakeGoogle] = {}
    current: dict[str, str] = {}

    def _make(client: Any) -> Any:
        return GoogleDriveSource(client, http=fakes[current["member"]])

    monkeypatch.setattr(sources, "make_source", _make)
    ns.fakes, ns.current = fakes, current
    yield ns
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb_source_nodes"))
        c.execute(text("DELETE FROM kb_sources"))
        c.execute(text("DELETE FROM integration_connections WHERE owning_app = 'knowledge'"))
        c.execute(text("DELETE FROM email_accounts WHERE email_address LIKE '%@kc1a-g.test'"))


async def _connect(p: Any, user: Any, *, email: str, account_id: str,
                   files: list[dict[str, Any]] | None = None) -> str:
    """Connect Drive as *user* through the two routes. Returns the source id."""
    p.fakes[user.email] = FakeGoogle(email, account_id, files if files is not None
                                     else _tree(account_id))
    p.current["member"] = user.email
    started = await _ok(p, user, "POST", "/knowledge/sources/google_drive/connect")
    state = parse_qs(urlsplit(started["url"]).query)["state"][0]
    back = await _call(p, user, "GET",
                       f"/knowledge/sources/google_drive/callback?code=c-{account_id}&state={state}")
    assert back.status_code == 302, back.text
    assert back.headers["location"] == "/knowledge?source_connected=google_drive"
    listed = await _ok(p, user, "GET", "/knowledge/sources")
    mine = [s for s in listed["sources"] if s["account_email"] == email.lower()]
    assert len(mine) == 1, listed
    return mine[0]["id"]


def _keys(obj: Any) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


# ── The KC-1 done-when: the tree shows with no text ─────────────────────────

async def test_the_tree_shows_after_the_callback_with_no_text(kc, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    ana = _principal(_address("ana"), kc.org_a)
    sid = await _connect(kc, ana, email="Ana@kc1a-g.test", account_id="A1")
    listed = await _ok(kc, ana, "GET", "/knowledge/sources")
    [src] = listed["sources"]
    assert src["state"] == "ready" and src["node_count"] == 6, src
    assert src["provider"] == "google_drive" and src["kind"] == "member"
    roots = await _ok(kc, ana, "GET", f"/knowledge/sources/{sid}/tree")
    names = [n["name"] for n in roots["nodes"]]
    # Folders first: the two drives, then the orphan shared with the member.
    assert names == ["My Drive", "Sales", "Shared with me.docx"], names
    contracts = await _ok(kc, ana, "GET", f"/knowledge/sources/{sid}/tree?parent=root-A1")
    assert [n["name"] for n in contracts["nodes"]] == ["Contracts"]
    assert contracts["nodes"][0]["has_children"] is True
    files = await _ok(kc, ana, "GET", f"/knowledge/sources/{sid}/tree?parent=fold-A1")
    [msa] = files["nodes"]
    assert msa["size"] == 1234 and msa["mime"] == "application/pdf"
    for answer in (listed, roots, contracts, files):
        assert not (_keys(answer) & _FORBIDDEN_KEYS), _keys(answer) & _FORBIDDEN_KEYS
    # The token sits in the blob only: no route answer carries it.
    for answer in (listed, roots, contracts, files):
        assert "rt-A1" not in str(answer) and "at-A1" not in str(answer)
    with kc.admin_engine.connect() as c:
        meta = c.execute(text(
            "SELECT provider_meta, credentials_encrypted, scope_kind, member_email "
            "FROM integration_connections WHERE owning_app = 'knowledge'")).one()
    assert meta.provider_meta["account_email"] == "ana@kc1a-g.test"
    assert "rt-A1" not in str(meta.credentials_encrypted), "the blob is not sealed"
    assert meta.scope_kind == "member" and meta.member_email == ana.email.lower()


# ── Tenancy: org B sees no node of org A ────────────────────────────────────

async def test_org_b_reads_no_source_and_no_node_of_org_a(kc, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    ana = _principal(_address("ana"), kc.org_a)
    sid = await _connect(kc, ana, email="ana@kc1a-g.test", account_id="A2")
    boss_b = _principal(_address("boss-b"), kc.org_b, CAPABILITY)
    assert (await _ok(kc, boss_b, "GET", "/knowledge/sources"))["sources"] == []
    assert (await _call(kc, boss_b, "GET", f"/knowledge/sources/{sid}/tree")).status_code == 404
    assert (await _call(kc, boss_b, "DELETE", f"/knowledge/sources/{sid}")).status_code == 404
    with app_engine.connect() as c, c.begin():
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": kc.org_b})
        assert c.execute(text("SELECT count(*) FROM kb_sources")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM kb_source_nodes")).scalar() == 0
    with app_engine.connect() as c, c.begin():
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": kc.org_a})
        assert c.execute(text("SELECT count(*) FROM kb_source_nodes")).scalar() == 6


async def test_raw_sql_under_tenant_session_sees_one_org(kc, app_engine) -> None:  # noqa: F811
    from gateway.db import tenant_session

    ana = _principal(_address("ana"), kc.org_a)
    await _connect(kc, ana, email="ana@kc1a-g.test", account_id="A3")
    async with tenant_engine_scope(_app_dsn(kc)):
        async with tenant_session(kc.org_b) as db:
            n = (await db.execute(text("SELECT count(*) FROM kb_source_nodes"))).scalar()
            s = (await db.execute(text("SELECT count(*) FROM kb_sources"))).scalar()
        async with tenant_session(kc.org_a) as db:
            a = (await db.execute(text("SELECT count(*) FROM kb_source_nodes"))).scalar()
    assert (n, s, a) == (0, 0, 6)


# ── Visibility: my own source, or every source for a manager ────────────────

async def test_a_member_sees_their_own_and_a_manager_sees_all(kc, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    ana = _principal(_address("ana"), kc.org_a)
    bo = _principal(_address("bo"), kc.org_a)
    boss = _principal(_address("boss"), kc.org_a, CAPABILITY)
    a = await _connect(kc, ana, email="ana@kc1a-g.test", account_id="A4")
    b = await _connect(kc, bo, email="bo@kc1a-g.test", account_id="B4")
    assert [s["id"] for s in (await _ok(kc, ana, "GET", "/knowledge/sources"))["sources"]] == [a]
    assert [s["id"] for s in (await _ok(kc, bo, "GET", "/knowledge/sources"))["sources"]] == [b]
    assert (await _call(kc, ana, "GET", f"/knowledge/sources/{b}/tree")).status_code == 404
    assert (await _call(kc, ana, "DELETE", f"/knowledge/sources/{b}")).status_code == 404
    seen = {s["id"] for s in (await _ok(kc, boss, "GET", "/knowledge/sources"))["sources"]}
    assert seen == {a, b}
    assert (await _call(kc, boss, "GET", f"/knowledge/sources/{b}/tree")).status_code == 200


# ── The upsert is idempotent, and a vanished node is gone ───────────────────

async def test_the_upsert_is_idempotent_and_a_vanished_node_is_gone(kc, app_engine) -> None:  # noqa: F811
    from gateway.kb_sync import discovery
    from gateway.routes.knowledge import sources

    ana = _principal(_address("ana"), kc.org_a)
    sid = await _connect(kc, ana, email="ana@kc1a-g.test", account_id="A5")
    source = sources.make_source(sources.google_client())
    async with tenant_engine_scope(_app_dsn(kc)):
        assert await discovery.discover_source(kc.org_a, sid, source) == "ready"
        assert await discovery.discover_source(kc.org_a, sid, source) == "ready"
    with kc.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT ext_id, count(*) AS n FROM kb_source_nodes WHERE source_id = CAST(:s AS uuid) "
            "GROUP BY ext_id"), {"s": sid}).fetchall()
    assert len(rows) == 6 and all(r.n == 1 for r in rows), rows
    # The member deletes the deck at Google. The next discovery marks it gone.
    kc.fakes[ana.email].files = [f for f in kc.fakes[ana.email].files
                                 if not f["id"].startswith("deck-")]
    async with tenant_engine_scope(_app_dsn(kc)):
        assert await discovery.discover_source(kc.org_a, sid, source) == "ready"
    with kc.admin_engine.connect() as c:
        state = c.execute(text(
            "SELECT state FROM kb_source_nodes WHERE ext_id = 'deck-A5'")).scalar()
        count = c.execute(text(
            "SELECT node_count FROM kb_sources WHERE id = CAST(:s AS uuid)"), {"s": sid}).scalar()
    assert state == "gone" and count == 5
    shown = await _ok(kc, ana, "GET", f"/knowledge/sources/{sid}/tree?parent=sd-A5")
    assert shown["nodes"] == [], "a gone node is still in the tree"


# ── The cap: the level walk and the unexplored folders ──────────────────────

@pytest.mark.parametrize(("cap", "levels", "kept", "unexplored"), [
    # Three levels fit, the cap stops the walk: the folders it did not list.
    (3, 3, {"root-C1", "sd-C1", "fold-C1"}, {"sd-C1", "fold-C1"}),
    # One level: a folder on the last level keeps explored = false.
    (5, 1, {"root-C1", "sd-C1", "fold-C1", "deck-C1"}, {"fold-C1"}),
])
async def test_a_tree_past_the_cap_keeps_the_first_levels(
    kc, app_engine, monkeypatch, cap: int, levels: int, kept: set[str],  # noqa: F811
    unexplored: set[str],
) -> None:
    from gateway.kb_sync import discovery

    monkeypatch.setattr(discovery, "NODE_CAP", cap)
    monkeypatch.setattr(discovery, "LEVELS", levels)
    ana = _principal(_address("ana"), kc.org_a)
    sid = await _connect(kc, ana, email="ana-cap@kc1a-g.test", account_id="C1")
    with kc.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT ext_id, explored FROM kb_source_nodes WHERE source_id = CAST(:s AS uuid)"),
            {"s": sid}).fetchall()
        state = c.execute(text("SELECT state, node_count FROM kb_sources "
                               "WHERE id = CAST(:s AS uuid)"), {"s": sid}).one()
    assert {r.ext_id for r in rows} == kept, rows
    assert {r.ext_id for r in rows if not r.explored} == unexplored, rows
    assert state.state == "ready" and state.node_count == len(kept)
    folder = await _ok(kc, ana, "GET", f"/knowledge/sources/{sid}/tree?parent=root-C1")
    assert {n["ext_id"]: n["explored"] for n in folder["nodes"]}["fold-C1"] is False


# ── A disconnect racing discovery (review P2) ───────────────────────────────

async def test_an_upsert_that_waits_on_a_disconnect_writes_no_node(kc, app_engine) -> None:  # noqa: F811
    """The disconnect holds the source row FOR UPDATE. A page upsert in
    flight must wait for it, see ``disconnected`` and write nothing. Without
    the row lock in the upsert's guard, it reads the old committed state and
    leaves orphan nodes behind the disconnect."""
    import json
    import threading
    import time

    from gateway.kb_sync import discovery

    _assert_non_priv(app_engine)
    ana = _principal(_address("ana"), kc.org_a)
    sid = await _connect(kc, ana, email="ana-race@kc1a-g.test", account_id="R1")
    rows = json.dumps([{"ext_id": "late-R1", "parent_ext_id": None, "drive_id": None,
                        "kind": "file", "name": "Late.pdf", "mime": None, "size": None,
                        "modified_at": None, "web_url": None, "explored": True}])
    done: dict[str, Any] = {}

    def _upsert() -> None:
        with app_engine.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": kc.org_a})
            c.execute(text("SET LOCAL lock_timeout = '20s'"))
            c.execute(text(discovery.UPSERT_NODES_SQL),
                      {"org": kc.org_a, "source": sid, "rows": rows})
        done["at"] = time.monotonic()

    with app_engine.connect() as hold, hold.begin():
        hold.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": kc.org_a})
        hold.execute(text("SELECT 1 FROM kb_sources WHERE id = CAST(:s AS uuid) FOR UPDATE"),
                     {"s": sid})
        hold.execute(text("DELETE FROM kb_source_nodes WHERE source_id = CAST(:s AS uuid)"),
                     {"s": sid})
        hold.execute(text("UPDATE kb_sources SET state = 'disconnected' "
                          "WHERE id = CAST(:s AS uuid)"), {"s": sid})
        worker = threading.Thread(target=_upsert)
        worker.start()
        time.sleep(1.5)
        assert "at" not in done, "the upsert did not wait for the disconnect's row lock"
        released = time.monotonic()
    worker.join(30)
    assert done.get("at", 0) >= released
    with kc.admin_engine.connect() as c:
        left = c.execute(text("SELECT count(*) FROM kb_source_nodes "
                              "WHERE source_id = CAST(:s AS uuid)"), {"s": sid}).scalar()
    assert left == 0, "a page upsert left orphan nodes behind a disconnect"


async def test_the_tree_of_a_disconnected_source_is_empty(kc, app_engine) -> None:  # noqa: F811
    """A node that outlived its source (an orphan) never reaches a tree answer."""
    ana = _principal(_address("ana"), kc.org_a)
    sid = await _connect(kc, ana, email="ana-gone@kc1a-g.test", account_id="R2")
    await _ok(kc, ana, "DELETE", f"/knowledge/sources/{sid}")
    with kc.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO kb_source_nodes (organization_id, source_id, ext_id, kind, name) "
            "VALUES (CAST(:o AS uuid), CAST(:s AS uuid), 'orphan-R2', 'file', 'Orphan')"),
            {"o": kc.org_a, "s": sid})
    roots = await _ok(kc, ana, "GET", f"/knowledge/sources/{sid}/tree")
    assert roots["nodes"] == [] and roots["source"]["state"] == "disconnected"
    kids = await _ok(kc, ana, "GET", f"/knowledge/sources/{sid}/tree?parent=root-R2")
    assert kids["nodes"] == []


# ── FORCE RLS, for a role that is not the owner and not BYPASSRLS ───────────

def test_both_tables_force_rls_and_refuse_another_org(kc, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    with kc.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname IN ('kb_sources', 'kb_source_nodes')")).fetchall()
        owner = c.execute(text(
            "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relname = 'kb_sources'")).scalar()
    assert {r.relname for r in rows} == {"kb_sources", "kb_source_nodes"}
    assert all(r.relrowsecurity and r.relforcerowsecurity for r in rows), rows
    assert owner != _APP_ROLE
    with kc.admin_engine.begin() as c:
        conn_id = c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app, "
            "scope_kind, member_email) VALUES (CAST(:o AS uuid), 'google_drive', 'knowledge', "
            "'member', 'x@kc1a.test') RETURNING id"), {"o": kc.org_b}).scalar()
    with pytest.raises(DBAPIError), app_engine.connect() as c, c.begin():
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": kc.org_a})
        c.execute(text(
            "INSERT INTO kb_sources (organization_id, connection_id, provider, owner_email) "
            "VALUES (CAST(:o AS uuid), CAST(:c AS uuid), 'google_drive', 'x@kc1a.test')"),
            {"o": kc.org_b, "c": str(conn_id)})


# ── The grant-share functions, both directions ──────────────────────────────

def _grant_both_to_app_role(admin: Any) -> None:
    with admin.begin() as c:
        for fn in ("email_gmail_grant_shared", "kb_google_drive_grant_shared"):
            c.execute(text(f"GRANT EXECUTE ON FUNCTION public.{fn}(text, uuid) TO {_APP_ROLE}"))


def _seed_gmail(admin: Any, org: str, email: str) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, organization_id) VALUES (:u, 'gmail', :e, 'gmail-blob', "
            "CAST(:o AS uuid)) RETURNING id"),
            {"u": f"u-{uuid.uuid4().hex[:6]}@kc1a.test", "e": email, "o": org}).scalar())


def _seed_drive(admin: Any, org: str, email: str, status: str = "connected") -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app, "
            "scope_kind, member_email, status, provider_meta, credentials_encrypted) "
            "VALUES (CAST(:o AS uuid), 'google_drive', 'knowledge', 'member', :m, :s, "
            "CAST(:meta AS jsonb), 'drive-blob') RETURNING id"),
            {"o": org, "m": f"m-{uuid.uuid4().hex[:6]}@kc1a.test", "s": status,
             "meta": f'{{"account_email": "{email}"}}'}).scalar())


def _ask(app_engine: Any, org: str, fn: str, email: str, exclude: str | None) -> Any:  # noqa: F811
    with app_engine.connect() as c, c.begin():
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
        return c.execute(text(f"SELECT public.{fn}(:e, CAST(:x AS uuid))"),
                         {"e": email, "x": exclude}).scalar()


def test_the_grant_functions_answer_true_false_and_null(kc, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _grant_both_to_app_role(kc.admin_engine)
    with kc.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT proname, prosecdef, prorettype::regtype::text, proconfig, "
            "proacl::text, pg_get_function_identity_arguments(oid) AS args FROM pg_proc "
            "WHERE proname IN ('email_gmail_grant_shared', 'kb_google_drive_grant_shared')"
        )).fetchall()
    assert {r.proname for r in rows} == {"email_gmail_grant_shared", "kb_google_drive_grant_shared"}
    for r in rows:
        assert r.prosecdef is True and r[2] == "boolean"
        assert r.args == "p_email text, p_exclude uuid", "the signature changed (R6)"
        assert any(c.startswith("search_path=pg_catalog") for c in r.proconfig or [])
        assert not [e for e in (r.proacl or "").strip("{}").split(",") if e.startswith("=")]
    address = f"one-{uuid.uuid4().hex[:6]}@kc1a-g.test"
    gmail_a = _seed_gmail(kc.admin_engine, kc.org_a, address)
    drive_b = _seed_drive(kc.admin_engine, kc.org_b, address)
    gmail, drive = "email_gmail_grant_shared", "kb_google_drive_grant_shared"
    # Bound to org B, which sees no row of org A: the functions still tell.
    assert _ask(app_engine, kc.org_b, gmail, address.upper(), NIL) is True
    # Drive direction: org B's Drive is the only Drive row, so it is free of Drive...
    assert _ask(app_engine, kc.org_b, drive, address, drive_b) is False
    # ...and a second Drive row of the address, in org A, shares the grant.
    drive_a = _seed_drive(kc.admin_engine, kc.org_a, address)
    assert _ask(app_engine, kc.org_b, drive, address, drive_b) is True
    # A disconnected Drive row holds no grant.
    with kc.admin_engine.begin() as c:
        c.execute(text("UPDATE integration_connections SET status = 'disconnected' "
                       "WHERE id = CAST(:i AS uuid)"), {"i": drive_a})
    assert _ask(app_engine, kc.org_b, drive, address, drive_b) is False
    # The reverse hole: with the Gmail row excluded, the live Drive row of org B
    # still answers true, so a Gmail disconnect does not revoke it.
    assert _ask(app_engine, kc.org_a, gmail, address, gmail_a) is True
    with kc.admin_engine.begin() as c:
        c.execute(text("UPDATE integration_connections SET status = 'disconnected' "
                       "WHERE id = CAST(:i AS uuid)"), {"i": drive_b})
    assert _ask(app_engine, kc.org_a, gmail, address, gmail_a) is False
    # No address is "cannot tell".
    assert _ask(app_engine, kc.org_a, gmail, "  ", NIL) is None
    assert _ask(app_engine, kc.org_a, drive, "", NIL) is None


def test_a_blind_owner_answers_null_for_both(kc, app_engine) -> None:  # noqa: F811
    """Owned by a role that cannot bypass RLS, each function answers NULL."""
    _assert_non_priv(app_engine)
    _grant_both_to_app_role(kc.admin_engine)
    address = f"blind-{uuid.uuid4().hex[:6]}@kc1a-g.test"
    _seed_drive(kc.admin_engine, kc.org_a, address)
    with kc.admin_engine.connect() as c:
        prior = c.execute(text(
            "SELECT pg_get_userbyid(proowner) FROM pg_proc "
            "WHERE proname = 'kb_google_drive_grant_shared'")).scalar()
    fns = ("email_gmail_grant_shared", "kb_google_drive_grant_shared")
    with kc.admin_engine.begin() as c:
        for fn in fns:
            c.execute(text(f"ALTER FUNCTION public.{fn}(text, uuid) OWNER TO {_APP_ROLE}"))
    try:
        for fn in fns:
            assert _ask(app_engine, kc.org_a, fn, address, NIL) is None, fn
    finally:
        with kc.admin_engine.begin() as c:
            for fn in fns:
                c.execute(text(f'ALTER FUNCTION public.{fn}(text, uuid) OWNER TO "{prior}"'))


# ── The Drive disconnect on the real database ───────────────────────────────

async def test_a_drive_disconnect_leaves_gmail_connected(kc, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _grant_both_to_app_role(kc.admin_engine)
    ana = _principal(_address("ana"), kc.org_a)
    address = "ana-both@kc1a-g.test"
    gmail = _seed_gmail(kc.admin_engine, kc.org_a, address)
    sid = await _connect(kc, ana, email=address, account_id="A6")
    gone = await _ok(kc, ana, "DELETE", f"/knowledge/sources/{sid}")
    assert gone["state"] == "disconnected" and gone["node_count"] == 0
    assert kc.fakes[ana.email].revokes() == 0, "Drive revoked the grant of a live Gmail"
    with kc.admin_engine.connect() as c:
        g = c.execute(text("SELECT credentials_encrypted FROM email_accounts "
                           "WHERE id = CAST(:i AS uuid)"), {"i": gmail}).one()
        d = c.execute(text("SELECT status, credentials_encrypted, provider_meta "
                           "FROM integration_connections WHERE owning_app = 'knowledge'")).one()
        nodes = c.execute(text("SELECT count(*) FROM kb_source_nodes WHERE "
                               "source_id = CAST(:s AS uuid)"), {"s": sid}).scalar()
    assert g.credentials_encrypted == "gmail-blob", "the Gmail blob changed"
    assert d.status == "disconnected" and d.credentials_encrypted is None
    assert d.provider_meta["account_email"] == address, "the store lift dropped the address"
    assert nodes == 0, "the nodes of a disconnected source stay"


async def test_a_lone_drive_grant_is_revoked_once(kc, app_engine) -> None:  # noqa: F811
    _grant_both_to_app_role(kc.admin_engine)
    ana = _principal(_address("ana"), kc.org_a)
    sid = await _connect(kc, ana, email="ana-lone@kc1a-g.test", account_id="A7")
    await _ok(kc, ana, "DELETE", f"/knowledge/sources/{sid}")
    await _ok(kc, ana, "DELETE", f"/knowledge/sources/{sid}")
    assert kc.fakes[ana.email].revokes() == 1


# ── The migration replays ───────────────────────────────────────────────────

def test_the_migration_replays(kc) -> None:
    from tests.unit._tenant_ladder import _exec_file

    with kc.admin_engine.begin() as c:
        _exec_file(c, str(_sources_migration()))
    with kc.admin_engine.begin() as c:
        _exec_file(c, str(_sources_migration()))
    with kc.admin_engine.connect() as c:
        policies = c.execute(text(
            "SELECT count(*) FROM pg_policies WHERE tablename IN "
            "('kb_sources', 'kb_source_nodes')")).scalar()
    assert policies == 2
