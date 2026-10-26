"""WS-61 KC-4a — the readers of a Drive document, hermetic.

Spec: ``project-docs/specs/knowledge_curation.md`` §8, with the defaults OQ1 to
OQ5 that §8 records, and the KC-4a row of §22.

No database and no network. The SQL is proved against a real database in
``test_kc_access_r8.py``. This file proves:

* **The resolver** (``kb_access/resolve.py``, A4): an outside email, a group,
  an ``anyone`` share and each domain rule of OQ1 prove the right members,
  and nothing becomes the literal ``org``.
* **The Google reader map** (``client.reader``, A4): a deleted row and a
  link-only share grant nothing, and ``pendingOwner`` changes nothing.
* **The access read** (``GoogleDriveSource.list_access``): GET only, the
  ``permissions`` field mask on the listing, ``permissions.list`` only for an
  item of a shared drive with no field, the call budget, the resume after the
  last item, and a dead page token. A listing with no permission list writes
  no reader (A7, the adapter half).
* **The two clauses** (``core.py``): ``knowledge:manage`` gives no document
  read (OQ4), and ``visible_documents_clause`` holds both halves of §8.4.
* **No page token in a log** for the new URLs.

Mutations this file catches (R7):

* an outside email maps to a subject -> ``TestTheResolver``;
* a domain maps to the literal ``org`` -> ``TestTheResolver``;
* the reader clause reads ``knowledge:manage`` -> ``TestTheClauses``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("gateway.kb_access.resolve", reason="gateway not installed")

from acb_auth import UserContext, UserRole, build_access
from gateway.kb_access import resolve
from gateway.kb_sources.base import (
    ANYONE,
    AccessCursor,
    AccessReader,
    CursorExpired,
    OAuthClientConfig,
    SourceCredential,
    SourceError,
)
from gateway.kb_sources.google_drive import client as gclient
from gateway.kb_sources.google_drive.client import GoogleDriveSource
from gateway.kb_sources.http import HttpAnswer, redact_page_tokens
from gateway.routes.knowledge import core

ORG = "44444444-4444-4444-8444-444444444444"
MEMBERS = frozenset({"ana@acme.test", "bo@acme.test", "cy@gmail.test"})
CLIENT = OAuthClientConfig(client_id="cid", client_secret="secret",
                           redirect_uri="https://app.test/cb")


def _r(kind: str, principal: str) -> AccessReader:
    return AccessReader(kind=kind, principal=principal, role="reader")


def _proven(*readers: AccessReader, domain: str | None = "acme.test",
            owner: str | None = None) -> frozenset[str]:
    return resolve.proven_readers(readers, members=MEMBERS, org_domain=domain, owner=owner)


# ── The resolver (A4) ───────────────────────────────────────────────────────


class TestTheResolver:
    def test_a_member_email_is_that_member(self) -> None:
        assert _proven(_r("user", "bo@acme.test")) == {"bo@acme.test"}

    def test_an_outside_email_never_becomes_a_subject(self) -> None:
        assert _proven(_r("user", "eve@outside.test")) == frozenset()
        assert resolve.member_subject("eve@outside.test", MEMBERS) is None

    def test_a_user_row_that_is_not_an_email_proves_nobody(self) -> None:
        for principal in ("org", "group:sales", "acme.test", "", "a b@acme.test"):
            assert _proven(_r("user", principal)) == frozenset(), principal

    def test_a_group_proves_nobody(self) -> None:
        assert _proven(_r("group", "sales@acme.test")) == frozenset()

    def test_anyone_proves_nobody(self) -> None:
        """OQ2: an ``anyone`` share proves nobody, also when it is found."""
        assert _proven(_r("anyone", ANYONE)) == frozenset()

    def test_the_org_domain_proves_each_member_in_it_and_never_org(self) -> None:
        """OQ1: a member outside the domain is not proven, and ``org`` is
        never in the answer."""
        got = _proven(_r("domain", "acme.test"))
        assert got == {"ana@acme.test", "bo@acme.test"}
        assert "org" not in got and "cy@gmail.test" not in got

    def test_the_domain_rule_folds_the_case_of_both_sides(self) -> None:
        assert _proven(_r("domain", "acme.test"), domain="  ACME.Test ") == {
            "ana@acme.test", "bo@acme.test"}

    def test_no_org_domain_means_a_domain_share_proves_nobody(self) -> None:
        assert _proven(_r("domain", "acme.test"), domain=None) == frozenset()
        assert _proven(_r("domain", "acme.test"), domain="  ") == frozenset()

    def test_another_domain_proves_nobody(self) -> None:
        assert _proven(_r("domain", "gmail.test")) == frozenset()
        assert _proven(_r("domain", "eu.acme.test")) == frozenset(), "a subdomain counted"

    def test_the_source_owner_is_a_proven_reader(self) -> None:
        """OQ3, and A7: with no row, only the owner is proven."""
        assert _proven(owner="ana@acme.test") == {"ana@acme.test"}
        assert _proven(owner="eve@outside.test") == frozenset(), "an outside owner counted"

    def test_a_node_that_is_not_live_proves_nobody(self) -> None:
        node = resolve.NodeReaders(live=False, owner="ana@acme.test",
                                   readers=(_r("user", "bo@acme.test"),), source_ready=True)
        assert resolve.proven_for(node, members=MEMBERS, org_domain="acme.test") == frozenset()
        assert resolve.proven_for(None, members=MEMBERS, org_domain="acme.test") == frozenset()

    def test_a_source_that_is_not_ready_proves_nobody_the_owner_too(self) -> None:
        """OQ7: each proof needs a ``ready`` source. The SQL twin is R8
        (``test_kc_access_r8.py``)."""
        for ready in (True, False):
            node = resolve.NodeReaders(
                live=True, owner="ana@acme.test",
                readers=(_r("user", "bo@acme.test"), _r("domain", "acme.test")),
                source_ready=ready)
            got = resolve.proven_for(node, members=MEMBERS, org_domain="acme.test")
            assert (got == frozenset()) is (not ready), (ready, got)
            assert ("ana@acme.test" in got) is ready

    def test_the_age_bound_is_24_hours_and_the_clause_reads_the_same_constant(self) -> None:
        assert resolve.ACCESS_MAX_AGE_SECONDS == 4 * 6 * 60 * 60
        sql, _ = core.document_reader_clause(_user_ctx(), "n")
        assert f"make_interval(secs => {resolve.ACCESS_MAX_AGE_SECONDS})" in sql
        assert "kb_src.state = 'ready'" in sql

    def test_the_answer_is_emails_in_the_one_grammar(self) -> None:
        from acb_auth.permissions import normalize_grant_subject

        got = _proven(_r("user", "BO@acme.test"), _r("domain", "acme.test"),
                      _r("group", "sales@acme.test"), _r("anyone", ANYONE),
                      owner="cy@gmail.test")
        assert got and all(normalize_grant_subject(s) == s and "@" in s for s in got)


# ── The Google reader map (A4) ──────────────────────────────────────────────


class TestTheReaderMap:
    def test_a_user_a_group_and_a_found_domain(self) -> None:
        assert gclient.reader({"type": "user", "role": "writer",
                               "emailAddress": "Bo@Acme.Test"}) == AccessReader(
            "user", "bo@acme.test", "writer")
        assert gclient.reader({"type": "group", "role": "reader",
                               "emailAddress": "sales@acme.test"}).kind == "group"
        assert gclient.reader({"type": "domain", "role": "reader", "domain": "Acme.Test",
                               "allowFileDiscovery": True}) == AccessReader(
            "domain", "acme.test", "reader")
        assert gclient.reader({"type": "anyone", "role": "reader",
                               "allowFileDiscovery": True}) == AccessReader(
            "anyone", ANYONE, "reader")

    def test_a_link_only_share_grants_nothing(self) -> None:
        for kind in ("domain", "anyone"):
            for found in (False, None):
                perm = {"type": kind, "role": "reader", "domain": "acme.test",
                        "allowFileDiscovery": found}
                assert gclient.reader(perm) is None, (kind, found)

    def test_a_deleted_row_grants_nothing(self) -> None:
        assert gclient.reader({"type": "user", "role": "reader", "deleted": True,
                               "emailAddress": "bo@acme.test"}) is None

    def test_pending_owner_changes_nothing(self) -> None:
        got = gclient.reader({"type": "user", "role": "writer", "pendingOwner": True,
                              "emailAddress": "bo@acme.test"})
        assert got == AccessReader("user", "bo@acme.test", "writer")

    def test_a_shared_drive_role_comes_from_the_details(self) -> None:
        got = gclient.reader({"type": "user", "emailAddress": "bo@acme.test",
                              "permissionDetails": [{"role": "fileOrganizer",
                                                     "inherited": True}]})
        assert got is not None and got.role == "fileOrganizer"

    def test_a_malformed_row_grants_nothing(self) -> None:
        for perm in ({"type": "user", "role": "reader"},
                     {"type": "user", "role": "reader", "emailAddress": "no-at"},
                     {"type": "user", "role": "x" * 33, "emailAddress": "bo@acme.test"},
                     {"type": "robot", "role": "reader"},
                     {"type": "domain", "role": "reader", "allowFileDiscovery": True,
                      "domain": "bad domain"},
                     "not a mapping"):
            assert gclient.reader(perm) is None, perm

    def test_a_reader_is_kept_once(self) -> None:
        perms = [{"type": "user", "role": "reader", "emailAddress": "bo@acme.test"},
                 {"type": "user", "role": "writer", "emailAddress": "BO@acme.test"}]
        assert [r.role for r in gclient.readers(perms)] == ["reader"]


# ── The access read of the adapter ──────────────────────────────────────────


def _cred() -> SourceCredential:
    return SourceCredential(access_token="at-1", refresh_token="rt-1",
                            expires_at=datetime.now(UTC) + timedelta(hours=1))


class FakeHttp:
    """A scripted Google: each GET pops the answer for its path."""

    def __init__(self, answers: dict[str, list[HttpAnswer]]) -> None:
        self.answers = {k: list(v) for k, v in answers.items()}
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get(self, url: str, *, params: Any = None, headers: Any = None) -> HttpAnswer:
        path = url.split("/drive/v3/", 1)[1]
        self.calls.append((path, dict(params or {})))
        return self.answers[path].pop(0)

    async def post(self, url: str, *, form: Any, headers: Any = None) -> HttpAnswer:
        raise AssertionError("the access read sent a POST")


def _ok(body: dict[str, Any]) -> HttpAnswer:
    return HttpAnswer(status_code=200, body=json.dumps(body).encode())


async def _no_sleep(_s: float) -> None:
    return None


def _src(answers: dict[str, list[HttpAnswer]]) -> tuple[GoogleDriveSource, FakeHttp]:
    http = FakeHttp(answers)
    return GoogleDriveSource(CLIENT, http=http, sleep=_no_sleep), http


def _user(email: str) -> dict[str, Any]:
    return {"id": email, "type": "user", "role": "reader", "emailAddress": email}


def _listing(*files: dict[str, Any], nxt: str | None = None) -> HttpAnswer:
    body: dict[str, Any] = {"files": list(files)}
    if nxt:
        body["nextPageToken"] = nxt
    return _ok(body)


def _read(src: GoogleDriveSource, cursor: AccessCursor | None = None,
          budget: int = 50) -> Any:
    return asyncio.run(src.list_access(_cred(), cursor, budget=budget))


class TestTheAccessRead:
    def test_one_listing_call_reads_the_readers_with_the_field_mask(self) -> None:
        src, http = _src({"files": [_listing(
            {"id": "f1", "permissions": [_user("bo@acme.test")]}, nxt="p2")]})
        page = _read(src)
        [(path, params)] = http.calls
        assert path == "files" and params["fields"] == gclient.ACCESS_FIELDS
        assert params["corpora"] == "allDrives" and params["supportsAllDrives"] == "true"
        assert params["includeItemsFromAllDrives"] == "true" and "pageToken" not in params
        assert "permissions(" in params["fields"] and "allowFileDiscovery" in params["fields"]
        for word in ("export", "content", "alt="):
            assert word not in params["fields"].lower()
        [node] = page.nodes
        assert (node.ext_id, node.listed, node.readers) == (
            "f1", True, (AccessReader("user", "bo@acme.test", "reader"),))
        assert page.next_cursor == AccessCursor(page_token="p2", page_size=gclient.PAGE_SIZE)
        assert page.calls == 1

    def test_permissions_list_only_for_a_shared_drive_item_with_no_field(self) -> None:
        src, http = _src({
            "files": [_listing({"id": "mine"}, {"id": "sd1", "driveId": "drv1"},
                               {"id": "f2", "driveId": "drv1", "permissions": []})],
            "files/sd1/permissions": [_ok({"permissions": [_user("bo@acme.test")],
                                           "nextPageToken": "q2"}),
                                      _ok({"permissions": [_user("cy@gmail.test")]})],
        })
        page = _read(src)
        paths = [p for p, _ in http.calls]
        assert paths == ["files", "files/sd1/permissions", "files/sd1/permissions"]
        listed = http.calls[1][1]
        assert listed["supportsAllDrives"] == "true" and listed["pageSize"] == "100"
        assert listed["fields"] == gclient.PERMISSION_FIELDS
        assert http.calls[2][1]["pageToken"] == "q2"
        nodes = {n.ext_id: n for n in page.nodes}
        # A7, the adapter half: My Drive with no field has no list and no reader.
        assert (nodes["mine"].listed, nodes["mine"].readers) == (False, ())
        assert {r.principal for r in nodes["sd1"].readers} == {"bo@acme.test", "cy@gmail.test"}
        assert (nodes["f2"].listed, nodes["f2"].readers) == (True, ())
        assert page.calls == 3 and page.next_cursor is None

    def test_a_403_of_permissions_list_means_no_list(self) -> None:
        src, _ = _src({"files": [_listing({"id": "sd1", "driveId": "drv1"})],
                       "files/sd1/permissions": [HttpAnswer(403, b"{}")]})
        [node] = _read(src).nodes
        assert (node.listed, node.readers) == (False, ())

    def test_a_throttle_of_permissions_list_is_not_swallowed(self) -> None:
        from gateway.kb_sources.base import RateLimited

        src, _ = _src({"files": [_listing({"id": "sd1", "driveId": "drv1"})],
                       "files/sd1/permissions": [HttpAnswer(429, b"{}")] * 4})
        with pytest.raises(RateLimited):
            _read(src)

    def test_the_budget_stops_before_an_item_it_cannot_finish(self) -> None:
        src, http = _src({"files": [_listing(
            {"id": "a", "permissions": []}, {"id": "sd1", "driveId": "drv1"},
            {"id": "c", "permissions": []}, nxt="p2")]})
        page = _read(src, AccessCursor(page_token="p1"), budget=gclient.MAX_PERMISSION_PAGES)
        assert [n.ext_id for n in page.nodes] == ["a"]
        assert page.next_cursor == AccessCursor(page_token="p1", after="a",
                                                page_size=gclient.PAGE_SIZE)
        assert [p for p, _ in http.calls] == ["files"] and page.calls == 1

    def test_a_resume_reads_the_page_again_and_goes_on_after_the_last_item(self) -> None:
        src, http = _src({"files": [_listing(
            {"id": "a", "permissions": []}, {"id": "b", "permissions": []}, nxt="p2")]})
        page = _read(src, AccessCursor(page_token="p1", after="a"))
        assert [n.ext_id for n in page.nodes] == ["b"]
        assert http.calls[0][1]["pageToken"] == "p1"
        # An item that left the page: the read takes the whole page again.
        src, _ = _src({"files": [_listing({"id": "b", "permissions": []})]})
        assert [n.ext_id for n in _read(src, AccessCursor(after="gone")).nodes] == ["b"]

    def test_an_item_past_the_page_cap_keeps_the_pages_it_read(self) -> None:
        answers = [_ok({"permissions": [_user(f"m{i}@acme.test")], "nextPageToken": f"t{i}"})
                   for i in range(gclient.MAX_PERMISSION_PAGES + 2)]
        src, http = _src({"files": [_listing({"id": "sd1", "driveId": "drv1"})],
                          "files/sd1/permissions": answers})
        [node] = _read(src).nodes
        assert len(node.readers) == gclient.MAX_PERMISSION_PAGES
        assert len(http.calls) == 1 + gclient.MAX_PERMISSION_PAGES

    def test_a_dead_page_token_is_cursor_expired(self) -> None:
        src, _ = _src({"files": [HttpAnswer(410, b"{}")]})
        with pytest.raises(CursorExpired):
            _read(src, AccessCursor(page_token="old"))
        with pytest.raises(CursorExpired):
            _read(GoogleDriveSource(CLIENT, http=FakeHttp({})), AccessCursor(page_token=""))

    def test_a_404_of_the_first_page_is_not_cursor_expired(self) -> None:
        src, _ = _src({"files": [HttpAnswer(404, b"{}")]})
        with pytest.raises(SourceError) as err:
            _read(src)
        assert not isinstance(err.value, CursorExpired)

    def test_a_budget_below_one_is_refused(self) -> None:
        src, http = _src({})
        with pytest.raises(ValueError):
            _read(src, budget=0)
        assert http.calls == []

    def test_a_truncated_page_is_read_again_smaller_inside_the_budget(self) -> None:
        big = HttpAnswer(status_code=200, body=b"{}", truncated=True)
        src, http = _src({"files": [big, _listing({"id": "a", "permissions": []})]})
        page = _read(src)
        assert [p["pageSize"] for _u, p in http.calls] == [
            str(gclient.PAGE_SIZE), str(gclient.PAGE_SIZE // 2)]
        assert page.calls == 2 and [n.ext_id for n in page.nodes] == ["a"]
        # With no call left, the read stops and keeps the smaller size.
        src, _ = _src({"files": [big]})
        page = _read(src, budget=1)
        assert page.nodes == () and page.next_cursor == AccessCursor(
            page_size=gclient.PAGE_SIZE // 2)


    # ── KC-4a fix round 1 ──────────────────────────────────────────────────

    def test_each_drive_call_sends_pretty_print_false(self) -> None:
        """R2a: every Drive GET goes through ``_get``, so discovery and the
        change feed get the compact answer too."""
        src, http = _src({
            "files": [_listing({"id": "sd1", "driveId": "drv1"}, {"id": "f1", "permissions": []})],
            "files/sd1/permissions": [_ok({"permissions": []})]})
        _read(src)
        assert [p for p, _ in http.calls] == ["files", "files/sd1/permissions"]
        assert all(params["prettyPrint"] == "false" for _p, params in http.calls), http.calls

    def test_a_page_too_large_at_the_smallest_size_completes_with_permissions_list(self) -> None:
        """R2b: the readers of ten files pass the cap at MIN_PAGE. The read goes
        on slim, and each item asks for its own list."""
        big = HttpAnswer(status_code=200, body=b"{}", truncated=True)
        src, http = _src({
            "files": [big, _listing({"id": "a"}, {"id": "b", "driveId": "drv1"}, nxt="p2")],
            "files/a/permissions": [_ok({"permissions": [_user("bo@acme.test")]})],
            "files/b/permissions": [_ok({"permissions": [_user("cy@gmail.test")]})],
        })
        page = _read(src, AccessCursor(page_size=gclient.MIN_PAGE))
        assert [q["fields"] for _p, q in http.calls[:2]] == [
            gclient.ACCESS_FIELDS, gclient.SLIM_ACCESS_FIELDS]
        assert [p for p, _ in http.calls] == [
            "files", "files", "files/a/permissions", "files/b/permissions"]
        assert all(q["pageSize"] == str(gclient.MIN_PAGE) for p, q in http.calls if p == "files")
        got = {n.ext_id: (n.listed, {r.principal for r in n.readers}) for n in page.nodes}
        assert got == {"a": (True, {"bo@acme.test"}), "b": (True, {"cy@gmail.test"})}
        assert page.calls == 4
        assert page.next_cursor == AccessCursor(page_token="p2", page_size=gclient.MIN_PAGE)

    def test_the_slim_read_writes_an_item_past_the_budget_as_not_listed(self) -> None:
        big = HttpAnswer(status_code=200, body=b"{}", truncated=True)
        src, http = _src({
            "files": [big, _listing({"id": "a"}, {"id": "b"}, {"id": "c"})],
            "files/a/permissions": [_ok({"permissions": [_user("bo@acme.test")]})],
        })
        # 2 listing calls, 1 call for item a, and none left for b and c.
        page = _read(src, AccessCursor(page_size=gclient.MIN_PAGE), budget=3)
        got = {n.ext_id: (n.listed, n.readers) for n in page.nodes}
        assert got["a"][0] is True and got["b"] == (False, ()) and got["c"] == (False, ())
        assert page.calls == 3 and len(http.calls) == 3
        assert page.next_cursor is None, "the page is done, and the pass goes on"

    def test_a_slim_page_that_is_still_too_large_fails_with_a_code(self) -> None:
        big = HttpAnswer(status_code=200, body=b"{}", truncated=True)
        src, _ = _src({"files": [big, big]})
        with pytest.raises(SourceError) as err:
            _read(src, AccessCursor(page_size=gclient.MIN_PAGE))
        assert getattr(err.value, "code", None) == "page_too_large"

    def test_a_refused_id_does_not_skip_the_items_before_it(self) -> None:
        """R3: ``ids`` holds None for a refused id, and a first read has no
        ``after``. The None must not match."""
        src, _ = _src({"files": [_listing(
            {"id": "f1", "permissions": []}, {"id": "bad id/..", "permissions": []},
            {"id": "f3", "permissions": []})]})
        page = _read(src)
        assert [n.ext_id for n in page.nodes] == ["f1", "f3"]

    def test_a_resume_after_a_refused_id_still_goes_on_after_the_last_item(self) -> None:
        src, _ = _src({"files": [_listing(
            {"id": "f1", "permissions": []}, {"id": "bad id/..", "permissions": []},
            {"id": "f3", "permissions": []})]})
        assert [n.ext_id for n in _read(src, AccessCursor(after="f1")).nodes] == ["f3"]


class TestNoPageTokenInALog:
    def test_the_filter_redacts_the_tokens_of_the_access_urls(self) -> None:
        import httpx

        secret = "AT-SECRET-0815"
        for path in ("files", "files/sd1/permissions"):
            url = httpx.URL(f"https://www.googleapis.com/drive/v3/{path}?pageToken={secret}")
            assert secret not in str(redact_page_tokens(url)), path

    def test_an_httpx_line_of_permissions_list_holds_no_token(self, caplog) -> None:
        import httpx

        secret = "AT-SECRET-0816"
        caplog.set_level(logging.DEBUG, logger="httpx")
        url = httpx.URL("https://www.googleapis.com/drive/v3/files/sd1/permissions"
                        f"?supportsAllDrives=true&pageToken={secret}")
        logging.getLogger("httpx").info('HTTP Request: %s %s "%s %d %s"',
                                        "GET", url, "HTTP/1.1", 200, "OK")
        lines = [r.getMessage() for r in caplog.records if r.name == "httpx"]
        assert lines and all(secret not in line for line in lines), lines


# ── The two clauses (OQ4, §8.4) ─────────────────────────────────────────────


def _user_ctx(*perms: str) -> UserContext:
    return UserContext(email="Ana@Acme.Test", role=UserRole.EMPLOYEE,
                       access=build_access(["feature:knowledge", *perms]),
                       organization_id=ORG)


class TestTheClauses:
    def test_knowledge_manage_gives_no_document_read(self) -> None:
        """OQ4: the reader clause never reads ``kb_manage``."""
        sql, params = core.document_reader_clause(_user_ctx(core.MANAGE_PERMISSION), "n")
        assert ":kb_manage" not in sql and "kb_manage" not in sql
        assert params["kb_viewer"] == "ana@acme.test" and params["kb_org"] == ORG

    def test_the_reader_clause_is_org_bound_and_live_only(self) -> None:
        sql, _ = core.document_reader_clause(_user_ctx(), "nd")
        assert sql.startswith("(nd.organization_id = CAST(:kb_org AS uuid) AND nd.state = 'live'")
        assert "kb_au.status = 'active'" in sql
        assert "principal_kind = 'group'" not in sql and "'anyone'" not in sql
        assert "principal_kind = 'domain'" in sql and "kb_o.domain" in sql
        assert sql.count("(") == sql.count(")")

    def test_the_documents_clause_holds_both_halves(self) -> None:
        sql, params = core.visible_documents_clause(_user_ctx(), "n", "CAST(:space AS uuid)")
        space, _ = core.readable_spaces_clause(_user_ctx(), "kb_ds")
        reader, _ = core.document_reader_clause(_user_ctx(), "n")
        assert space in sql and reader in sql and " AND " + reader in sql
        assert "kb_ds.id = CAST(:space AS uuid)" in sql
        assert set(params) == {"kb_org", "kb_viewer", "kb_manage"}
