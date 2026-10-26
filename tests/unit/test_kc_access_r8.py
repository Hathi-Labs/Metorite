"""R8 — the readers of a Drive document on a real database (WS-61 KC-4a).

Spec: ``project-docs/specs/knowledge_curation.md`` §7.2 step 6, §8, §16, §19
and the KC-4a row of §22.

What this proves against a REAL Postgres, as the NON-privileged role of the
phase-4 rehearsal (no superuser, no BYPASSRLS), on a database with the whole
ladder, the four generated phases and two organizations. It reuses the
fixtures of ``test_kc_sources_r8.py``: a source is connected through the real
routes, and discovery runs on the fake Google there. The access pass runs the
REAL Google adapter (``list_access``) against :class:`Drive`, which answers
the access listing and ``permissions.list`` as Google's own JSON.

The acceptance items of the KC-4a contract:

* A1 — a member who reads space S but is no proven reader of node N gets no
  row from ``visible_documents_clause``. A proven reader with a read of S gets
  N. A proven reader with no read of S gets nothing. A holder of
  ``knowledge:manage`` who is no reader gets nothing (OQ4);
* A2 — a node shared with four members does not fit ``company``, and
  ``unproven`` names the others. A space whose readers are those four, or
  fewer, fits. A group grant and the space owner count as readers;
* A3 — a pass in which the provider drops reader R deletes R's row, and N is
  then not visible to R. A node that the listing no longer shows loses its
  rows at the end of a full pass;
* A5 — org B reads and writes no ``kb_node_access`` row of org A, and the
  table FORCEs row level security;
* A7 — a listing with no permission list writes zero rows, and only the
  source owner is a proven reader (OQ3);
* OQ7 (fix round 1, R1) — a row proves its reader only while it is younger
  than ``ACCESS_MAX_AGE_SECONDS``, and each proof, the owner's too, needs a
  ``ready`` source. The clause and the resolver agree on both bounds;
* A9 — an access page and a disconnect at the same time, in both orders, and
  neither fails with DeadlockDetected.

And the edges: the domain rule of OQ1 and ``anyone`` (OQ2) on the database,
the SQL clause agrees with the Python resolver for each member, the call cap
leaves a cursor and the next run goes on from it, the due read of the
scheduler, a revoked token takes the KC-3 path, the widened CHECKs, and the
migration replays.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_kb_pages_r8 import (  # noqa: F401
    CAPABILITY,
    _add_group,
    _add_member,
    _app_dsn,
    _assert_non_priv,
    _principal,
    ns,
)
from tests.unit.test_kc_sources_r8 import FakeGoogle, _connect, kc  # noqa: F401
from tests.unit.test_kc_sync_r8 import (
    _deadlocks,
    _disconnect,
    _overlap,
    _Pause,
    _pause,
    _run_errors,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"
DOMAIN = "kc4-r8.test"

#: The ``app_user.id`` of each member that :func:`_member` made, by email.
_IDS: dict[str, str] = {}


def _access_migration() -> Path:
    """The migration that creates ``kb_node_access``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS kb_node_access" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


class Drive:
    """The REAL Google adapter on the fake Google of one member, with the
    readers of each file. Each GET goes through :meth:`get`."""

    provider = "google_drive"

    def __init__(self, google: FakeGoogle) -> None:
        from gateway.kb_sources.google_drive import GoogleDriveSource, auth

        self.google = google
        #: The ``permissions`` field of a file in the listing.
        self.perms: dict[str, list[dict[str, Any]]] = {}
        #: The ``permissions.list`` answer of an item of a shared drive.
        self.shared: dict[str, list[dict[str, Any]]] = {}
        #: The files that the listing no longer shows.
        self.hidden: set[str] = set()
        self.calls: list[tuple[str, Any]] = []
        self.gate: Any = None
        self.access_error: BaseException | None = None
        self.refresh_error: BaseException | None = None
        self.inner = GoogleDriveSource(auth.OAuthClientConfig(
            client_id="cid.apps.googleusercontent.com", client_secret="r8-secret",
            redirect_uri="https://app.test/cb"), http=self)

    async def get(self, url: str, *, params: Any = None, headers: Any = None) -> Any:
        from gateway.kb_sources.google_drive import client as gc
        from gateway.kb_sources.http import HttpAnswer

        params = dict(params or {})
        assert "alt" not in params and "export" not in url, "the access pass read a document"
        if url.endswith("/files") and params.get("fields") == gc.ACCESS_FIELDS:
            self.calls.append(("access", params.get("pageToken")))
            items = []
            for f in self.google.files:
                if f["id"] in self.hidden:
                    continue
                item: dict[str, Any] = {"id": f["id"]}
                if f.get("driveId"):
                    item["driveId"] = f["driveId"]
                if f["id"] in self.perms:
                    item["permissions"] = self.perms[f["id"]]
                items.append(item)
            size = int(params["pageSize"])
            start = int((params.get("pageToken") or "o0")[1:])
            body: dict[str, Any] = {"files": items[start:start + size]}
            if start + size < len(items):
                body["nextPageToken"] = f"o{start + size}"
            return HttpAnswer(status_code=200, body=json.dumps(body).encode())
        if url.endswith("/permissions"):
            fid = url.rsplit("/files/", 1)[1].split("/", 1)[0]
            self.calls.append(("permissions", fid))
            return HttpAnswer(status_code=200, body=json.dumps(
                {"permissions": self.shared.get(fid, [])}).encode())
        return await self.google.get(url, params=params, headers=headers)

    async def post(self, url: str, *, form: Any, headers: Any = None) -> Any:
        return await self.google.post(url, form=form, headers=headers)

    async def refresh(self, cred: Any) -> Any:
        self.calls.append(("refresh", None))
        if self.refresh_error is not None:
            raise self.refresh_error
        return await self.inner.refresh(cred)

    async def list_access(self, cred: Any, cursor: Any, *, budget: int) -> Any:
        if self.gate is not None:
            await self.gate.wait()
        if self.access_error is not None:
            raise self.access_error
        return await self.inner.list_access(cred, cursor, budget=budget)

    async def fetch_text(self, cred: Any, node: Any) -> str:
        raise AssertionError("the access pass read the text of a document")

    async def fetch_bytes(self, cred: Any, node: Any) -> bytes:
        raise AssertionError("the access pass read the bytes of a document")

    def reads(self) -> list[Any]:
        return [c for verb, c in self.calls if verb == "access"]


def _user(email: str, role: str = "reader") -> dict[str, Any]:
    return {"id": f"p-{email}", "type": "user", "role": role, "emailAddress": email}


def _found(kind: str, domain: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"id": f"p-{kind}", "type": kind, "role": "reader",
                           "allowFileDiscovery": True}
    if domain:
        out["domain"] = domain
    return out


def _member(p: Any, tag: str, *perms: str, domain: str = DOMAIN,
            org: str | None = None) -> Any:
    """A NEW active member of org A (or *org*), and its principal."""
    email = f"{tag}.{uuid.uuid4().hex[:8]}@{domain}"
    org = org or p.org_a
    _IDS[email] = _add_member(p, org, email)
    return _principal(email, org, *perms)


async def _source(p: Any, account: str) -> tuple[Any, str, Drive]:
    """Connect Drive for a new active member of org A. Returns the owner,
    the source and the drive."""
    owner = _member(p, "owner")
    sid = await _connect(p, owner, email=f"own-{account.lower()}@kc4-g.test",
                         account_id=account)
    return owner, sid, Drive(p.fakes[owner.email])


async def _pass(p: Any, sid: str, drive: Drive, org: str | None = None) -> Any:
    from gateway.kb_sync import access

    async with tenant_engine_scope(_app_dsn(p)):
        return await access.run_access(org or p.org_a, sid, source=drive)


def _node_id(p: Any, sid: str, ext: str) -> str:
    with p.admin_engine.connect() as c:
        return str(c.execute(text(
            "SELECT id FROM kb_source_nodes WHERE source_id = CAST(:s AS uuid) "
            "AND ext_id = :e"), {"s": sid, "e": ext}).scalar())


def _rows(p: Any, sid: str) -> set[tuple[str, str, str]]:
    with p.admin_engine.connect() as c:
        return {(r.ext_id, r.principal_kind, r.principal) for r in c.execute(text(
            "SELECT n.ext_id, a.principal_kind, a.principal FROM kb_node_access a "
            "JOIN kb_source_nodes n ON n.id = a.node_id "
            "WHERE n.source_id = CAST(:s AS uuid)"), {"s": sid})}


def _space(p: Any, *, grants: list[str], owner: str | None = None, kind: str = "team",
           slug: str | None = None, org: str | None = None) -> str:
    org = org or p.org_a
    with p.admin_engine.begin() as c:
        space = str(c.execute(text(
            "INSERT INTO kb_spaces (organization_id, slug, name, kind, owner_email) "
            "VALUES (CAST(:o AS uuid), :s, :s, :k, :ow) RETURNING id"),
            {"o": org, "s": slug or f"kc4-{uuid.uuid4().hex[:8]}", "k": kind,
             "ow": owner}).scalar())
        for subject in grants:
            c.execute(text(
                "INSERT INTO kb_space_grants (organization_id, space_id, subject, level, "
                "created_by) VALUES (CAST(:o AS uuid), CAST(:sp AS uuid), :g, 'read', 'r8')"),
                {"o": org, "sp": space, "g": subject})
    return space


async def _sees(p: Any, user: Any, node: str, space: str) -> bool:
    """Does *user* see *node* filed in *space*, through the ONE clause?"""
    from gateway.db import tenant_session
    from gateway.routes.knowledge import core

    vis, params = core.visible_documents_clause(user, "n", "CAST(:kc_space AS uuid)")
    async with tenant_engine_scope(_app_dsn(p)), tenant_session(user.organization_id) as db:
        row = (await db.execute(text(
            f"SELECT n.id FROM kb_source_nodes n WHERE n.id = CAST(:kc_node AS uuid) "
            f"AND {vis}"), {**params, "kc_node": node, "kc_space": space})).first()
    return row is not None


async def _proven_by_clause(p: Any, node: str, emails: list[str]) -> set[str]:
    """Each email that ``document_reader_clause`` proves on *node*."""
    from gateway.db import tenant_session
    from gateway.routes.knowledge import core

    out: set[str] = set()
    async with tenant_engine_scope(_app_dsn(p)), tenant_session(p.org_a) as db:
        for email in emails:
            sql, params = core.document_reader_clause(_principal(email, p.org_a), "n")
            row = (await db.execute(text(
                f"SELECT 1 FROM kb_source_nodes n WHERE n.id = CAST(:kc_node AS uuid) "
                f"AND {sql}"), {**params, "kc_node": node})).first()
            if row is not None:
                out.add(email.lower())
    return out


async def _fits(p: Any, user: Any, node: str, space: str) -> Any:
    from gateway.db import tenant_session
    from gateway.kb_access.filing import document_fits_space

    async with tenant_engine_scope(_app_dsn(p)), tenant_session(user.organization_id) as db:
        return await document_fits_space(db, user, node, space)


def _active_members(p: Any, org: str) -> set[str]:
    with p.admin_engine.connect() as c:
        return {str(r[0]) for r in c.execute(text(
            "SELECT lower(email) FROM app_user WHERE organization_id = CAST(:o AS uuid) "
            "AND status = 'active'"), {"o": org})}


def _set_domain(p: Any, domain: str | None) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE organization SET domain = :d WHERE id = CAST(:o AS uuid)"),
                  {"d": domain, "o": p.org_a})


@pytest.fixture
def domain(kc):  # noqa: F811 — a fixture
    """Org A with no domain, and its old domain back after the test."""
    with kc.admin_engine.connect() as c:
        old = c.execute(text("SELECT domain FROM organization WHERE id = CAST(:o AS uuid)"),
                        {"o": kc.org_a}).scalar()
    _set_domain(kc, None)
    yield kc
    _set_domain(kc, old)


# ── A1: the space AND the provider ──────────────────────────────────────────

async def test_a1_a_space_reader_who_is_no_provider_reader_sees_no_document(
    kc, domain, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    _owner, sid, drive = await _source(kc, "A1")
    m = _member(kc, "m")
    pr = _member(kc, "p")
    q = _member(kc, "q")
    boss = _member(kc, "boss", CAPABILITY)
    drive.perms["file-A1"] = [_user(pr.email), _user(q.email), _user("eve@outside.test")]
    out = await _pass(kc, sid, drive)
    assert (out.status, out.counts["complete"]) == ("ok", True), out
    node = _node_id(kc, sid, "file-A1")
    s = _space(kc, grants=[m.email, pr.email])
    other = _space(kc, grants=[m.email])
    assert not await _sees(kc, m, node, s), "a space reader saw a document the provider hides"
    assert await _sees(kc, pr, node, s), "a proven reader with a read of S saw nothing"
    assert not await _sees(kc, pr, node, other), "a proven reader saw it with no read of S"
    assert not await _sees(kc, q, node, s), "a proven reader with no read of S saw it"
    # OQ4: knowledge:manage opens every space and proves no document.
    assert not await _sees(kc, boss, node, s), "knowledge:manage read a document"
    # The outside address is stored and never becomes a subject.
    assert ("file-A1", "user", "eve@outside.test") in _rows(kc, sid)
    outsider = _principal("eve@outside.test", kc.org_a)
    assert not await _sees(kc, outsider, node, s)


# ── A2: the filing rule ─────────────────────────────────────────────────────

async def test_a2_four_readers_fit_their_space_and_not_company(
    kc, domain, app_engine,  # noqa: F811
) -> None:
    owner, sid, drive = await _source(kc, "A2")
    four = [_member(kc, t) for t in ("a", "b", "c", "d")]
    e, f = _member(kc, "e"), _member(kc, "f")
    boss = _member(kc, "boss", CAPABILITY)
    drive.perms["file-A2"] = [_user(u.email) for u in four]
    assert (await _pass(kc, sid, drive)).counts["complete"] is True
    node = _node_id(kc, sid, "file-A2")
    emails = sorted(u.email.lower() for u in four)
    company = _space(kc, grants=["org"], kind="company", slug="company")
    got = await _fits(kc, boss, node, company)
    proven = set(emails) | {owner.email.lower()}
    assert got.fits is False
    assert got.unproven == tuple(sorted(_active_members(kc, kc.org_a) - proven)), got
    assert {e.email.lower(), f.email.lower(), boss.email.lower()} <= set(got.unproven)
    assert not set(emails) & set(got.unproven)
    # Exactly the four, or fewer, fits.
    assert (await _fits(kc, boss, node, _space(kc, grants=emails))).fits is True
    assert (await _fits(kc, boss, node, _space(kc, grants=emails[:2]))).fits is True
    mixed = await _fits(kc, boss, node, _space(kc, grants=[emails[0], e.email.lower()]))
    assert (mixed.fits, mixed.unproven) == (False, (e.email.lower(),))
    # A group grant is its members. The space owner is a reader.
    _add_group(kc, kc.org_a, "kc4-two", [_IDS[four[0].email], _IDS[four[1].email]])
    _add_group(kc, kc.org_a, "kc4-mix", [_IDS[four[0].email], _IDS[f.email]])
    assert (await _fits(kc, boss, node, _space(kc, grants=["group:kc4-two"]))).fits is True
    by_group = await _fits(kc, boss, node, _space(kc, grants=["group:kc4-mix"]))
    assert by_group.unproven == (f.email.lower(),)
    owned = await _fits(kc, boss, node, _space(kc, grants=[emails[0]], owner=e.email.lower()))
    assert owned.unproven == (e.email.lower(),)
    # OQ4: a space whose only extra reader is a manager by permission fits.
    assert (await _fits(kc, boss, node, _space(kc, grants=emails[:1]))).fits is True
    # Org B holds no such node and no such space.
    boss_b = _principal("boss-b@kc4.test", kc.org_b, CAPABILITY)
    assert (await _fits(kc, boss_b, node, company)).fits is False


# ── A3: a narrowed share leaves within one pass ─────────────────────────────

async def test_a3_a_dropped_reader_loses_the_row_and_the_document(
    kc, domain, app_engine,  # noqa: F811
) -> None:
    _owner, sid, drive = await _source(kc, "A3")
    r, s_ = _member(kc, "r"), _member(kc, "s")
    drive.perms["file-A3"] = [_user(r.email), _user(s_.email)]
    drive.perms["orph-A3"] = [_user(r.email)]
    assert (await _pass(kc, sid, drive)).counts["complete"] is True
    node = _node_id(kc, sid, "file-A3")
    space = _space(kc, grants=["org"])
    assert await _sees(kc, r, node, space)
    before = _rows(kc, sid)
    assert ("file-A3", "user", r.email.lower()) in before
    # At the provider: R loses the file, and the orphan leaves the listing.
    drive.perms["file-A3"] = [_user(s_.email)]
    drive.hidden.add("orph-A3")
    assert (await _pass(kc, sid, drive)).counts["complete"] is True
    after = _rows(kc, sid)
    assert ("file-A3", "user", r.email.lower()) not in after, "the dropped reader kept a row"
    assert ("file-A3", "user", s_.email.lower()) in after
    assert not {row for row in after if row[0] == "orph-A3"}, "an unlisted node kept a reader"
    assert not await _sees(kc, r, node, space), "a dropped reader still saw the document"
    assert await _sees(kc, s_, node, space)


# ── A5: tenancy ─────────────────────────────────────────────────────────────

async def test_a5_org_b_reads_and_writes_no_access_row_of_org_a(
    kc, domain, app_engine,  # noqa: F811
) -> None:
    from gateway.db import tenant_session

    _assert_non_priv(app_engine)
    _owner, sid, drive = await _source(kc, "A5")
    reader = _member(kc, "r")
    drive.perms["file-A5"] = [_user(reader.email)]
    assert (await _pass(kc, sid, drive)).status == "ok"
    node = _node_id(kc, sid, "file-A5")

    def count(org: str) -> int:
        with app_engine.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
            return int(c.execute(text("SELECT count(*) FROM kb_node_access")).scalar())

    assert count(kc.org_b) == 0 and count(kc.org_a) >= 1
    async with tenant_engine_scope(_app_dsn(kc)), tenant_session(kc.org_b) as db:
        changed = (await db.execute(text(
            "UPDATE kb_node_access SET role = 'owner' WHERE node_id = CAST(:n AS uuid)"),
            {"n": node})).rowcount
        gone = (await db.execute(text(
            "DELETE FROM kb_node_access WHERE node_id = CAST(:n AS uuid)"), {"n": node})).rowcount
    assert (changed, gone) == (0, 0)
    assert ("file-A5", "user", reader.email.lower()) in _rows(kc, sid)
    # WITH CHECK: bound to org B, a row that names org A is refused, and back.
    for bound, named in ((kc.org_b, kc.org_a), (kc.org_a, kc.org_b)):
        with pytest.raises(DBAPIError), app_engine.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": bound})
            c.execute(text(
                "INSERT INTO kb_node_access (organization_id, node_id, principal_kind, "
                "principal, role) VALUES (CAST(:o AS uuid), CAST(:n AS uuid), 'user', "
                "'x@kc4.test', 'reader')"), {"o": named, "n": node})
    # Org B runs no pass of a source of org A: the source is not visible.
    calls = len(drive.calls)
    out = await _pass(kc, sid, drive, org=kc.org_b)
    assert out.status == "skipped" and len(drive.calls) == calls
    # And org B's members see no document of org A through the clause.
    b_member = _principal(f"b.{uuid.uuid4().hex[:6]}@{DOMAIN}", kc.org_b)
    assert not await _sees(kc, b_member, node, _space(kc, grants=["org"], org=kc.org_b))


def test_a5_the_table_forces_rls_and_the_unbound_session_reads_nothing(
    kc, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    with kc.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity, pg_get_userbyid(relowner) AS owner "
            "FROM pg_class WHERE relname = 'kb_node_access'")).one()
        [policy] = c.execute(text(
            "SELECT qual, with_check FROM pg_policies WHERE tablename = 'kb_node_access'"
        )).fetchall()
    assert row.relrowsecurity and row.relforcerowsecurity and row.owner != _APP_ROLE
    assert "app.tenant_id" in policy.qual and "app.tenant_id" in policy.with_check
    with app_engine.connect() as c, c.begin():
        assert c.execute(text("SELECT count(*) FROM kb_node_access")).scalar() == 0


# ── A7: no permission list proves only the owner ────────────────────────────

async def test_a7_a_listing_with_no_permission_list_proves_only_the_owner(
    kc, domain, app_engine,  # noqa: F811
) -> None:
    owner, sid, drive = await _source(kc, "A7")
    other = _member(kc, "o")
    out = await _pass(kc, sid, drive)  # no file carries a permissions field
    assert out.status == "ok" and out.counts["rows"] == 0, out
    assert _rows(kc, sid) == set(), "a listing with no permission list wrote a row"
    # The shared-drive item asked permissions.list once, and nothing else did.
    assert [c for verb, c in drive.calls if verb == "permissions"] == ["deck-A7"]
    space = _space(kc, grants=["org"])
    node = _node_id(kc, sid, "orph-A7")
    assert await _sees(kc, owner, node, space), "the source owner is not a proven reader"
    assert not await _sees(kc, other, node, space)
    assert await _proven_by_clause(kc, node, [owner.email, other.email]) == {
        owner.email.lower()}


# ── OQ1 and OQ2 on the database ─────────────────────────────────────────────

async def test_the_domain_rule_and_anyone_on_the_database(
    kc, domain, app_engine,  # noqa: F811
) -> None:
    owner, sid, drive = await _source(kc, "D1")
    inside = _member(kc, "in")
    outside = _member(kc, "out", domain="kc4-elsewhere.test")
    drive.perms["file-D1"] = [_found("domain", DOMAIN)]
    drive.perms["orph-D1"] = [_found("anyone")]
    drive.perms["fold-D1"] = [{**_found("domain", DOMAIN), "allowFileDiscovery": False}]
    assert (await _pass(kc, sid, drive)).status == "ok"
    rows = _rows(kc, sid)
    assert ("file-D1", "domain", DOMAIN) in rows and ("orph-D1", "anyone", "*") in rows
    assert not {r for r in rows if r[0] == "fold-D1"}, "a link-only share wrote a row"
    space = _space(kc, grants=["org"])
    file_node, anyone_node = _node_id(kc, sid, "file-D1"), _node_id(kc, sid, "orph-D1")
    # organization.domain NULL: a domain share proves nobody.
    assert not await _sees(kc, inside, file_node, space)
    _set_domain(kc, DOMAIN.upper())
    assert await _sees(kc, inside, file_node, space), "the org's own domain proved nobody"
    assert not await _sees(kc, outside, file_node, space), "a member outside the domain saw it"
    _set_domain(kc, "kc4-elsewhere.test")
    assert not await _sees(kc, inside, file_node, space), "another domain proved a member"
    # OQ2: anyone proves nobody but the owner.
    assert not await _sees(kc, inside, anyone_node, space)
    assert await _sees(kc, owner, anyone_node, space)


async def test_the_clause_and_the_resolver_agree_for_each_member(
    kc, domain, app_engine,  # noqa: F811
) -> None:
    """The SQL twin and the Python twin of the rule give one answer."""
    from gateway.db import tenant_session
    from gateway.kb_access import resolve

    owner, sid, drive = await _source(kc, "C1")
    a, b = _member(kc, "a"), _member(kc, "b")
    far = _member(kc, "far", domain="kc4-elsewhere.test")
    drive.perms["file-C1"] = [_user(a.email), _user("eve@outside.test"),
                              {"id": "g", "type": "group", "role": "reader",
                               "emailAddress": f"team@{DOMAIN}"}]
    drive.perms["orph-C1"] = [_found("domain", DOMAIN), _found("anyone")]
    drive.shared["deck-C1"] = [_user(far.email), _user(b.email, "commenter")]
    assert (await _pass(kc, sid, drive)).status == "ok"
    _set_domain(kc, DOMAIN)
    everyone = sorted(_active_members(kc, kc.org_a) | {"eve@outside.test"})
    for ext in ("file-C1", "orph-C1", "deck-C1", "fold-C1", "root-C1"):
        node = _node_id(kc, sid, ext)
        by_sql = await _proven_by_clause(kc, node, everyone)
        async with tenant_engine_scope(_app_dsn(kc)), tenant_session(kc.org_a) as db:
            members = await resolve.load_members(db, kc.org_a)
            org_domain = await resolve.load_org_domain(db, kc.org_a)
            loaded = await resolve.load_node(db, kc.org_a, node)
        by_py = resolve.proven_for(loaded, members=members, org_domain=org_domain)
        assert by_sql == set(by_py), (ext, by_sql, by_py)
        assert owner.email.lower() in by_sql, (ext, "the owner is not proven")
        assert "org" not in by_py and "eve@outside.test" not in by_sql
    assert a.email.lower() in await _proven_by_clause(kc, _node_id(kc, sid, "file-C1"),
                                                      [a.email])


# ── OQ7: the age of a row and the state of the source ──────────────────────

def _age_rows(p: Any, sid: str, hours: float, ext: str | None = None) -> None:
    """Move ``synced_at`` of the rows of a source (or of one node) into the past."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE kb_node_access a SET synced_at = now() - make_interval(secs => :h * 3600) "
            "FROM kb_source_nodes n WHERE n.id = a.node_id "
            "AND n.source_id = CAST(:s AS uuid) AND (CAST(:e AS text) IS NULL OR n.ext_id = :e)"),
            {"h": hours, "s": sid, "e": ext})


def _set_source_state(p: Any, sid: str, state: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE kb_sources SET state = :st WHERE id = CAST(:s AS uuid)"),
                  {"st": state, "s": sid})


async def _both(p: Any, node: str, emails: list[str]) -> tuple[set[str], set[str]]:
    """The proven readers of *node* by the SQL clause and by the resolver."""
    from gateway.db import tenant_session
    from gateway.kb_access import resolve

    by_sql = await _proven_by_clause(p, node, emails)
    async with tenant_engine_scope(_app_dsn(p)), tenant_session(p.org_a) as db:
        members = await resolve.load_members(db, p.org_a)
        org_domain = await resolve.load_org_domain(db, p.org_a)
        loaded = await resolve.load_node(db, p.org_a, node)
    by_py = set(resolve.proven_for(loaded, members=members, org_domain=org_domain))
    return by_sql, by_py & {e.lower() for e in emails}


async def test_an_aged_row_loses_the_reader_and_the_clause_and_the_resolver_agree(
    kc, domain, app_engine,  # noqa: F811
) -> None:
    from gateway.kb_access import resolve

    owner, sid, drive = await _source(kc, "G1")
    a, b = _member(kc, "a"), _member(kc, "b")
    drive.perms["file-G1"] = [_user(a.email), _found("domain", DOMAIN)]
    drive.perms["orph-G1"] = [_user(b.email)]
    assert (await _pass(kc, sid, drive)).counts["complete"] is True
    _set_domain(kc, DOMAIN)
    everyone = sorted(_active_members(kc, kc.org_a))
    file_node, orph_node = _node_id(kc, sid, "file-G1"), _node_id(kc, sid, "orph-G1")
    space = _space(kc, grants=["org"])
    sql, py = await _both(kc, file_node, everyone)
    assert sql == py and {a.email.lower(), b.email.lower()} <= sql, (sql, py)  # the domain row
    # Just inside the bound: still proven. Just past it: the row proves nobody.
    bound = resolve.ACCESS_MAX_AGE_SECONDS / 3600
    _age_rows(kc, sid, bound - 1)
    assert await _sees(kc, a, file_node, space), "a row inside the bound lost its reader"
    _age_rows(kc, sid, bound + 1, ext="file-G1")
    sql, py = await _both(kc, file_node, everyone)
    assert sql == py == {owner.email.lower()}, (sql, py)
    assert not await _sees(kc, a, file_node, space), "an aged user row kept its reader"
    assert not await _sees(kc, b, file_node, space), "an aged domain row kept its readers"
    assert await _sees(kc, owner, file_node, space), "the age bound removed the owner"
    # The other node is still inside the bound, and agrees too.
    sql, py = await _both(kc, orph_node, everyone)
    assert sql == py and b.email.lower() in sql, (sql, py)
    # A new pass writes fresh rows, and the reader is back.
    assert (await _pass(kc, sid, drive)).counts["complete"] is True
    assert await _sees(kc, a, file_node, space), "a fresh pass did not bring the reader back"


@pytest.mark.parametrize("state", ["needs_reconnect", "failed", "discovering", "disconnected"])
async def test_a_source_that_is_not_ready_proves_nobody_the_owner_too(
    kc, domain, app_engine, state: str,  # noqa: F811
) -> None:
    owner, sid, drive = await _source(kc, f"S{state[:2].upper()}")
    reader = _member(kc, "r")
    ext = f"file-S{state[:2].upper()}"
    drive.perms[ext] = [_user(reader.email)]
    assert (await _pass(kc, sid, drive)).counts["complete"] is True
    node = _node_id(kc, sid, ext)
    space = _space(kc, grants=["org"])
    everyone = sorted(_active_members(kc, kc.org_a))
    assert await _sees(kc, reader, node, space) and await _sees(kc, owner, node, space)
    _set_source_state(kc, sid, state)
    sql, py = await _both(kc, node, everyone)
    assert sql == py == set(), (state, sql, py)
    assert not await _sees(kc, reader, node, space), f"a {state} source proved its reader"
    assert not await _sees(kc, owner, node, space), f"a {state} source proved its owner"
    _set_source_state(kc, sid, "ready")
    assert await _sees(kc, owner, node, space), "a ready source did not prove its owner again"


# ── The call cap, the cursor and the due read ───────────────────────────────

async def test_the_call_cap_leaves_a_cursor_and_the_next_run_goes_on(
    kc, domain, app_engine, monkeypatch,  # noqa: F811
) -> None:
    from gateway.db import tenant_session
    from gateway.kb_sources.google_drive import client as gc
    from gateway.kb_sync import access, runs, scheduler

    monkeypatch.setattr(gc, "PAGE_SIZE", 2)
    monkeypatch.setattr(access, "ACCESS_CALLS_PER_TICK", access.MIN_BUDGET)
    _owner, sid, drive = await _source(kc, "K1")
    reader = _member(kc, "r")
    for ext in ("file-K1", "orph-K1"):
        drive.perms[ext] = [_user(reader.email)]

    async def due() -> Any:
        async with tenant_engine_scope(_app_dsn(kc)), tenant_session(kc.org_a) as db:
            [row] = [r for r in await runs.due_rows(db, kc.org_a) if r.source_id == sid]
        return row

    assert scheduler.access_due(await due()), "a source with no pass is not due"
    first = await _pass(kc, sid, drive)
    assert (first.status, first.counts["complete"]) == ("ok", False), first
    with kc.admin_engine.connect() as c:
        state, read_at = c.execute(text(
            "SELECT c.cursor_state, s.access_read_at FROM kb_sync_cursors c "
            "JOIN kb_sources s ON s.id = c.source_id WHERE c.source_id = CAST(:s AS uuid) "
            "AND c.kind = 'access'"), {"s": sid}).one()
    state = json.loads(state) if isinstance(state, str) else state
    assert state["page_token"] and read_at is None, (state, read_at)
    row = await due()
    assert (row.access_status, row.access_read_at) == ("ok", None)
    assert scheduler.access_due(row), "a pass that stopped at its cap was not due"
    runs_made = 1
    while True:
        out = await _pass(kc, sid, drive)
        runs_made += 1
        assert out.status == "ok", out
        if out.counts["complete"]:
            break
        assert runs_made < 10, "the pass did not move forward"
    # Two pages: the first run read page one, and the second went on at page two.
    assert (drive.reads(), runs_made) == ([None, "o2"], 2), (drive.reads(), runs_made)
    row = await due()
    assert row.access_read_at is not None and not scheduler.access_due(row)
    assert {r[0] for r in _rows(kc, sid)} == {"file-K1", "orph-K1"}


async def test_a_revoked_token_takes_the_needs_reconnect_path(
    kc, domain, app_engine,  # noqa: F811
) -> None:
    from gateway.kb_sources.base import NeedsReconnect, SourceError

    _owner, sid, drive = await _source(kc, "N1")
    refused = SourceError("Google refused the Drive token")
    refused.code = "unauthorized"
    drive.access_error = refused
    dead = NeedsReconnect("Google refused the token call (invalid_grant)")
    dead.code = "invalid_grant"
    drive.refresh_error = dead
    out = await _pass(kc, sid, drive)
    assert (out.status, out.error) == ("stopped", "invalid_grant"), out
    with kc.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT s.state, ic.status AS conn FROM kb_sources s "
            "JOIN integration_connections ic ON ic.id = s.connection_id "
            "WHERE s.id = CAST(:s AS uuid)"), {"s": sid}).one()
    assert (row.state, row.conn) == ("needs_reconnect", "needs_reconnect")
    made = len(drive.calls)
    assert (await _pass(kc, sid, drive)).status == "skipped"
    assert len(drive.calls) == made, "a source that needs a reconnect got a provider call"


# ── A9: the lock order, an access page and a disconnect at once ─────────────

@pytest.mark.parametrize("first", ["page", "disconnect"])
async def test_a9_the_lock_order_an_access_page_and_a_disconnect(
    kc, domain, app_engine, monkeypatch, first: str,  # noqa: F811
) -> None:
    """The page takes the source row first (the guard), then the rows of
    ``kb_node_access``. The disconnect takes the connection row, then the
    source row ``FOR UPDATE``, then deletes the nodes. Without the guard
    first, the page holds the node rows and asks for the source row that the
    disconnect holds, while the disconnect waits on the node rows."""
    from gateway.connections import store
    from gateway.kb_sources.google_drive import client as gc
    from gateway.kb_sync import runs

    _assert_non_priv(app_engine)
    monkeypatch.setattr(gc, "PAGE_SIZE", 3)  # two pages: the first one is not the last
    owner, sid, drive = await _source(kc, f"L{first[0]}")
    reader = _member(kc, "r")
    for ext in ("file", "orph", "fold"):
        drive.perms[f"{ext}-L{first[0]}"] = [_user(reader.email)]
    errors = _run_errors(monkeypatch)

    def page() -> Any:
        from gateway.kb_sync import access

        async def go() -> Any:
            return await access.run_access(kc.org_a, sid, source=drive)
        return go

    if first == "page":
        # The first page holds the guard and the rows, before its cursor.
        hold = _pause(monkeypatch, runs, "write_cursor")
        got, gone = await _overlap(kc, page(), hold, _disconnect(owner, sid))
    else:
        # The disconnect holds its locks. The pass has started its read.
        hold = _pause(monkeypatch, store, "disconnect")
        drive.gate = _Pause()
        gone, got = await _overlap(kc, _disconnect(owner, sid), hold, page(),
                                   start=drive.gate)
    assert _deadlocks(got, gone, *errors) == [], "DeadlockDetected (40P01)"
    assert isinstance(gone, dict) and gone["state"] == "disconnected", gone
    assert not isinstance(got, BaseException), got
    assert got.status == "stopped" and got.error in ("disconnected", "no_connection"), (
        got, errors)
    with kc.admin_engine.connect() as c:
        left = c.execute(text(
            "SELECT (SELECT count(*) FROM kb_source_nodes WHERE source_id = CAST(:s AS uuid)), "
            "(SELECT count(*) FROM kb_sync_cursors WHERE source_id = CAST(:s AS uuid)), "
            "(SELECT state FROM kb_sources WHERE id = CAST(:s AS uuid))"), {"s": sid}).one()
    assert tuple(left) == (0, 0, "disconnected"), left
    assert _rows(kc, sid) == set(), "an access row outlived the disconnect"
    with kc.admin_engine.connect() as c:
        kinds = [r[0] for r in c.execute(text(
            "SELECT kind FROM kb_sync_runs WHERE source_id = CAST(:s AS uuid) "
            "ORDER BY started_at"), {"s": sid})]
    assert kinds == ["discover", "access"], "a run row went"


# ── The migration ───────────────────────────────────────────────────────────

async def test_the_widened_kinds_take_access_and_keep_the_old_values(
    kc, app_engine,  # noqa: F811
) -> None:
    _owner, sid, _drive = await _source(kc, "W1")
    with kc.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT conname, pg_get_constraintdef(oid), convalidated FROM pg_constraint "
            "WHERE conname IN ('kb_sync_runs_kind_check', 'kb_sync_cursors_kind_check')"
        )).fetchall()
    defs = {r[0]: r[1] for r in rows}
    valid = {r[0]: r[2] for r in rows}
    for kind in ("discover", "changes", "reconcile", "access"):
        assert f"'{kind}'" in defs["kb_sync_runs_kind_check"], kind
    for kind in ("changes", "reconcile", "access"):
        assert f"'{kind}'" in defs["kb_sync_cursors_kind_check"], kind
    assert all(valid.values()), valid
    ids = {"o": kc.org_a, "s": sid}
    # The new kind is legal in both tables, as the app role, under the tenant.
    with app_engine.connect() as c, c.begin():
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": kc.org_a})
        c.execute(text("INSERT INTO kb_sync_cursors (organization_id, source_id, kind) "
                       "VALUES (CAST(:o AS uuid), CAST(:s AS uuid), 'access')"), ids)
        c.execute(text("INSERT INTO kb_sync_runs (organization_id, source_id, kind, status) "
                       "VALUES (CAST(:o AS uuid), CAST(:s AS uuid), 'access', 'ok')"), ids)
    for table in ("kb_sync_runs", "kb_sync_cursors"):
        with pytest.raises(DBAPIError, match="kind_check"), kc.admin_engine.begin() as c:
            c.execute(text(f"INSERT INTO {table} (organization_id, source_id, kind) "
                           "VALUES (CAST(:o AS uuid), CAST(:s AS uuid), 'bogus')"), ids)


def _shape(c: Any) -> dict[str, Any]:
    return {
        "columns": sorted(tuple(r) for r in c.execute(text(
            "SELECT table_name, column_name, data_type, is_nullable FROM "
            "information_schema.columns WHERE table_name IN ('kb_node_access', "
            "'kb_sources')"))),
        "indexes": sorted(tuple(r) for r in c.execute(text(
            "SELECT tablename, indexname, indexdef FROM pg_indexes "
            "WHERE tablename = 'kb_node_access'"))),
        "policies": sorted(tuple(r) for r in c.execute(text(
            "SELECT tablename, policyname, qual, with_check FROM pg_policies "
            "WHERE tablename = 'kb_node_access'"))),
        "checks": sorted(tuple(r) for r in c.execute(text(
            "SELECT conname, pg_get_constraintdef(oid), convalidated FROM pg_constraint "
            "WHERE conrelid = ANY(ARRAY['kb_node_access'::regclass, "
            "'kb_sync_runs'::regclass, 'kb_sync_cursors'::regclass])"))),
    }


def test_the_migration_replays_with_no_change(kc) -> None:  # noqa: F811
    from tests.unit._tenant_ladder import _exec_file

    with kc.admin_engine.connect() as c:
        before = _shape(c)
    assert ("kb_sources", "access_read_at", "timestamp with time zone", "YES") in (
        before["columns"])
    assert any(i[1] == "kb_node_access_principal_idx" for i in before["indexes"])
    for _ in range(2):
        with kc.admin_engine.begin() as c:
            _exec_file(c, str(_access_migration()))
    with kc.admin_engine.connect() as c:
        after = _shape(c)
    assert after == before
