"""The Activity surface — WS-31 **CP-12f**.

Spec: ``project-docs/specs/operator_identity_and_access.md`` §8.1 done-whens
25-26 · **D64.5**.

Run::

    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform
    uv run pytest tests/unit/test_operator_activity.py
"""
from __future__ import annotations

import os
import pathlib
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import apply_ladder

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

_ROOT = pathlib.Path(__file__).resolve().parents[2]
SHARED = "test-operator-token"
INTERNAL = "test-internal-token"


@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def eng():
    e = create_engine(_URL, future=True)
    yield e
    e.dispose()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", SHARED)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", INTERNAL)
    from customer_console.main import app
    return TestClient(app)


def _make(eng, role: str) -> tuple[str, str, str]:
    from customer_console import operator_sessions, store

    email = f"{role}-{uuid.uuid4().hex[:8]}@fracktal.in"
    now = datetime.now(UTC)
    issued = operator_sessions.issue(now=now)
    with eng.begin() as conn:
        row = conn.execute(
            text("INSERT INTO operator (email, role) VALUES (:e, :r) "
                 "RETURNING id"),
            {"e": email, "r": role},
        ).first()
        store.operator_session_insert(
            conn, operator_id=str(row[0]), prefix=issued.prefix,
            key_hash=issued.key_hash, expires_at=now + timedelta(hours=12),
        )
    return str(row[0]), email, issued.token


def _auth(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _org(eng, slug: str | None = None) -> tuple[str, str]:
    slug = slug or f"acme-{uuid.uuid4().hex[:8]}"
    with eng.begin() as conn:
        oid = conn.execute(
            text("INSERT INTO organization (slug, name) VALUES (:s, :n) "
                 "RETURNING id"),
            {"s": slug, "n": f"Org {slug}"},
        ).scalar()
    return str(oid), slug


def _audit(eng, *, org_id, actor, action, detail="{}", created_at=None):
    """Write one row directly. The route bodies are tested elsewhere."""
    with eng.begin() as conn:
        return str(conn.execute(
            text(
                "INSERT INTO control_audit "
                "(organization_id, actor, action, detail, created_at) "
                "VALUES (CAST(:o AS UUID), :a, :ac, CAST(:d AS jsonb), "
                "COALESCE(CAST(:t AS TIMESTAMPTZ), now())) RETURNING id"
            ),
            {"o": org_id, "a": actor, "ac": action, "d": detail,
             "t": created_at},
        ).scalar())


def _read(client, token, **params):
    return client.get("/activity", headers=_auth(token), params=params)


# ── Done-when 25: cross-org, and a viewer may read it ──────────────────────


def test_a_viewer_may_read_the_activity_trail(client, eng):
    """Done-when 25, second half.

    A record of who did what to our customers is worth nothing if seeing it
    needs a privilege. That is the same argument `GET /operators` makes.
    """
    _, _, viewer = _make(eng, "viewer")
    assert _read(client, viewer).status_code == 200


def test_the_read_spans_every_company(client, eng):
    """Done-when 25, first half. One read, two companies, both present."""
    _, _, viewer = _make(eng, "viewer")
    action = f"probe.{uuid.uuid4().hex[:8]}"
    id_a, slug_a = _org(eng)
    id_b, slug_b = _org(eng)
    _audit(eng, org_id=id_a, actor="a@x.io", action=action)
    _audit(eng, org_id=id_b, actor="b@x.io", action=action)

    rows = _read(client, viewer, action=action).json()["activity"]
    assert {r["org_slug"] for r in rows} == {slug_a, slug_b}


def test_the_company_name_travels_with_the_row(client, eng):
    """A slug is an identifier. An operator reads a name."""
    _, _, viewer = _make(eng, "viewer")
    action = f"probe.{uuid.uuid4().hex[:8]}"
    org_id, slug = _org(eng)
    _audit(eng, org_id=org_id, actor="a@x.io", action=action)
    row = _read(client, viewer, action=action).json()["activity"][0]
    assert row["org_slug"] == slug and row["org_name"] == f"Org {slug}"


# ── The LEFT JOIN, which is the easiest thing here to get silently wrong ───


def test_a_row_with_no_company_is_still_returned(client, eng):
    """⚠️ An INNER JOIN would compile and hide every `operator.*` row.

    Adding a colleague names no company, so `organization_id` is NULL. Those
    are precisely the rows an investigation into operator behaviour needs.
    """
    _, _, viewer = _make(eng, "viewer")
    action = f"operator.probe.{uuid.uuid4().hex[:8]}"
    _audit(eng, org_id=None, actor="admin@x.io", action=action)

    rows = _read(client, viewer, action=action).json()["activity"]
    assert len(rows) == 1
    assert rows[0]["org_slug"] is None and rows[0]["org_name"] is None


def test_history_survives_the_company_being_purged(client, eng):
    """Done-when 19's other half, read from this surface.

    `organization_id` is `ON DELETE SET NULL`, so purging a customer must
    leave the record of what we did to them readable.
    """
    _, _, viewer = _make(eng, "viewer")
    action = f"probe.{uuid.uuid4().hex[:8]}"
    org_id, _ = _org(eng)
    _audit(eng, org_id=org_id, actor="a@x.io", action=action)

    with eng.begin() as conn:
        conn.execute(text("DELETE FROM organization WHERE id = CAST(:o AS UUID)"),
                     {"o": org_id})

    rows = _read(client, viewer, action=action).json()["activity"]
    assert len(rows) == 1, "purging the customer deleted the evidence"
    assert rows[0]["org_slug"] is None


# ── The filters ────────────────────────────────────────────────────────────


def test_each_filter_narrows_and_they_combine(client, eng):
    _, _, viewer = _make(eng, "viewer")
    action = f"probe.{uuid.uuid4().hex[:8]}"
    other = f"probe.{uuid.uuid4().hex[:8]}"
    id_a, slug_a = _org(eng)
    id_b, _ = _org(eng)
    _audit(eng, org_id=id_a, actor="ann@x.io", action=action)
    _audit(eng, org_id=id_b, actor="bob@x.io", action=action)
    _audit(eng, org_id=id_a, actor="ann@x.io", action=other)

    by_actor = _read(client, viewer, actor="ann@x.io", action=action).json()
    assert len(by_actor["activity"]) == 1

    by_org = _read(client, viewer, org_slug=slug_a).json()["activity"]
    assert {r["action"] for r in by_org} == {action, other}

    both = _read(client, viewer, org_slug=slug_a, action=action).json()
    assert len(both["activity"]) == 1


def test_an_unknown_filter_value_is_an_empty_page_not_a_404(client, eng):
    """⚠️ A 404 here would be an oracle.

    CP-12c spent a whole slice making a refusal say nothing about which
    companies exist. A `404` for an unknown slug would hand that back.
    """
    _, _, viewer = _make(eng, "viewer")
    r = _read(client, viewer, org_slug=f"no-such-{uuid.uuid4().hex[:8]}")
    assert r.status_code == 200
    assert r.json()["activity"] == [] and r.json()["next_cursor"] is None


def test_the_action_list_offers_only_actions_that_occurred(client, eng):
    _, _, viewer = _make(eng, "viewer")
    action = f"probe.{uuid.uuid4().hex[:8]}"
    _audit(eng, org_id=None, actor="a@x.io", action=action)
    body = client.get("/activity/actions", headers=_auth(viewer)).json()
    assert action in body["actions"]
    assert f"probe.{uuid.uuid4().hex[:8]}" not in body["actions"]


# ── Done-when 26: the keyset is EXACT ──────────────────────────────────────


def _drain(client, token, *, limit, **params):
    """Page to the end. Returns the ids in the order they were served."""
    seen, cursor, pages = [], None, 0
    while True:
        body = _read(client, token, limit=limit, cursor=cursor,
                     **params).json()
        seen.extend(r["id"] for r in body["activity"])
        cursor = body["next_cursor"]
        pages += 1
        if cursor is None:
            return seen, pages
        assert pages < 50, "the cursor never terminated"


def test_paging_returns_every_row_exactly_once(client, eng):
    """Done-when 26. Newest first, no duplicate, no gap."""
    _, _, viewer = _make(eng, "viewer")
    action = f"probe.{uuid.uuid4().hex[:8]}"
    org_id, _ = _org(eng)
    base = datetime.now(UTC)
    written = [
        _audit(eng, org_id=org_id, actor="a@x.io", action=action,
               created_at=base - timedelta(seconds=i))
        for i in range(7)
    ]

    served, pages = _drain(client, viewer, limit=2, action=action)
    assert pages > 1, "the test did not actually paginate"
    assert served == written, "order, duplicate or gap"
    assert len(set(served)) == len(served)


def test_the_id_tiebreak_holds_when_timestamps_are_identical(client, eng):
    """⚠️ The reason the cursor is `(created_at, id)` and not `created_at`.

    One request can write several audit rows, and `now()` gives them all the
    SAME transaction-start stamp. A cursor on the timestamp alone would either
    repeat the whole tied group on the next page or skip past it.
    """
    _, _, viewer = _make(eng, "viewer")
    action = f"probe.{uuid.uuid4().hex[:8]}"
    org_id, _ = _org(eng)
    stamp = datetime.now(UTC)
    written = {
        _audit(eng, org_id=org_id, actor="a@x.io", action=action,
               created_at=stamp)
        for _ in range(6)
    }

    served, pages = _drain(client, viewer, limit=2, action=action)
    assert pages > 1
    assert len(served) == len(set(served)) == 6, "a tie duplicated or dropped"
    assert set(served) == written


def test_a_short_page_ends_the_scroll(client, eng):
    """A cursor on a short page costs every client one empty round-trip."""
    _, _, viewer = _make(eng, "viewer")
    action = f"probe.{uuid.uuid4().hex[:8]}"
    _audit(eng, org_id=None, actor="a@x.io", action=action)
    assert _read(client, viewer, limit=50,
                 action=action).json()["next_cursor"] is None


def test_the_filter_survives_the_cursor(client, eng):
    """⚠️ A filter dropped on page 2 leaks every other company's rows."""
    _, _, viewer = _make(eng, "viewer")
    action = f"probe.{uuid.uuid4().hex[:8]}"
    org_id, slug = _org(eng)
    other_id, _ = _org(eng)
    for i in range(4):
        _audit(eng, org_id=org_id, actor="a@x.io", action=action,
               created_at=datetime.now(UTC) - timedelta(seconds=i))
    _audit(eng, org_id=other_id, actor="a@x.io", action=action)

    served, _ = _drain(client, viewer, limit=2, action=action, org_slug=slug)
    assert len(served) == 4, "the org filter was lost after page 1"


# ── The cursor is opaque, and a bad one is the CALLER's error ──────────────


@pytest.mark.parametrize("bad", [
    "not-base64!!",
    "YWJj",                                    # decodes, but has no separator
    "MjAyNi0wMS0wMXxub3QtYS11dWlk",            # valid date, id is not a UUID
    "fGFiYw",                                  # empty timestamp half
])
def test_a_malformed_cursor_is_400_and_never_500(client, eng, bad):
    """⚠️ And never a silent restart from the top.

    Falling back to page 1 turns a hand-edited URL into an infinite scroll,
    which presents as a hung console rather than as an error.
    """
    _, _, viewer = _make(eng, "viewer")
    assert _read(client, viewer, cursor=bad).status_code == 400


def test_the_cursor_does_not_publish_the_ordering_key(client, eng):
    """Opaque so the ordering stays changeable. H-7 may yet force a change."""
    _, _, viewer = _make(eng, "viewer")
    action = f"probe.{uuid.uuid4().hex[:8]}"
    for _ in range(2):
        _audit(eng, org_id=None, actor="a@x.io", action=action)
    cursor = _read(client, viewer, limit=1,
                   action=action).json()["next_cursor"]
    assert cursor and "|" not in cursor and ":" not in cursor


def test_the_limit_is_clamped_rather_than_refused(client, eng):
    from customer_console import operator_activity

    _, _, viewer = _make(eng, "viewer")
    assert _read(client, viewer, limit=10_000).status_code == 200
    assert operator_activity.clamp_limit(10_000) == \
        operator_activity.MAX_LIMIT
    # Zero would be an empty page WITH a cursor, which never terminates.
    for silly in (0, -1, None):
        assert operator_activity.clamp_limit(silly) == \
            operator_activity.DEFAULT_LIMIT


# ── H-7, reproduced rather than asserted ───────────────────────────────────


def test_a_late_commit_can_be_missed_by_a_scroll(client, eng):
    """⚠️ **H-7 on this surface, measured — and why it is payable here.**

    `now()` is the TRANSACTION-START timestamp. A transaction that opens early
    and commits late stamps a time EARLIER than a row already committed by a
    newer transaction. When that stamp lands inside a window the reader has
    already scrolled past, the row is never served to THAT scroll.

    This test reproduces the miss AND the recovery. The recovery is the whole
    argument: migration 168's delta feed persists its cursor and advances it
    forever, so a row stamped behind the high-water mark is lost permanently.
    This cursor is discarded when the reader stops, and a fresh read starts at
    the newest row — so the loss is bounded to one scroll.

    **If this test ever fails because the row is now served, that is good news
    and the ordering has been fixed. Read `CURSOR_IS_EPHEMERAL` and delete the
    caveat rather than the test.**
    """
    _, _, viewer = _make(eng, "viewer")
    action = f"probe.{uuid.uuid4().hex[:8]}"
    org_id, _ = _org(eng)

    _audit(eng, org_id=org_id, actor="a@x.io", action=action)   # old-2
    time.sleep(0.02)
    _audit(eng, org_id=org_id, actor="a@x.io", action=action)   # old-1
    time.sleep(0.02)

    slow = eng.connect()
    slow.execute(text("BEGIN"))
    slow.execute(text("SELECT now()"))          # pins the transaction stamp
    late = str(slow.execute(
        text("INSERT INTO control_audit (organization_id, actor, action) "
             "VALUES (CAST(:o AS UUID), 'slow@x.io', :a) RETURNING id"),
        {"o": org_id, "a": action},
    ).scalar())
    time.sleep(0.02)
    _audit(eng, org_id=org_id, actor="a@x.io", action=action)   # new-1

    # Three rows are VISIBLE, so a page of three ends BELOW the in-flight
    # stamp. That is the shape that produces the miss.
    served, _ = _drain(client, viewer, limit=3, action=action)
    assert len(served) == 3, "the scroll setup did not reproduce"

    slow.execute(text("COMMIT"))
    slow.close()

    with eng.begin() as conn:
        order = [str(r[0]) for r in conn.execute(
            text("SELECT id FROM control_audit WHERE action = :a "
                 "ORDER BY created_at"), {"a": action})]
    assert order.index(late) < len(order) - 1, (
        "the backwards stamp did not occur, so this test proved nothing"
    )

    assert late not in served, (
        "the scroll served the late row — if the ordering was fixed, see the "
        "docstring"
    )

    fresh = _read(client, viewer, limit=50, action=action).json()["activity"]
    assert late in [r["id"] for r in fresh], (
        "⚠️ the late row is missing from a FRESH read too — the loss is no "
        "longer bounded to one scroll, and H-7 now bites this surface as "
        "hard as it bites migration 168"
    )


def test_the_cursor_is_declared_ephemeral_in_the_module(client):
    """R7 — the rule names its fence, and the fence is this constant.

    A durable feed of `control_audit` needs a different ordering guarantee.
    Flipping this to False without answering H-7 is the mistake to catch.
    """
    from customer_console import operator_activity

    assert operator_activity.CURSOR_IS_EPHEMERAL is True


# ── What the trail must never disclose ─────────────────────────────────────


def test_no_activity_row_carries_a_usable_credential(client, eng):
    """⚠️ The fence for a future call site that audits a token by mistake.

    `key.issue` and `discount.issue` record the PREFIX only, deliberately. This
    surface shows `detail` to a VIEWER, so a call site that ever passed the
    minted token instead would publish a working credential to everybody.
    """
    import json as _json

    from customer_console.keys import split_key

    _, _, viewer = _make(eng, "viewer")
    rows = _read(client, viewer, limit=200).json()["activity"]
    assert rows, "no audit rows exist, so this fence proved nothing"

    for row in rows:
        for value in _json.loads(_json.dumps(row["detail"])).values():
            if not isinstance(value, str):
                continue
            parsed = split_key(value)
            assert parsed is None or not parsed[1], (
                f"{row['action']} audited a full key: {value[:16]}..."
            )


def test_the_trail_carries_no_tenant_content(client, eng):
    """D64.5 — the commercial record only.

    `control_audit` holds OUR acts against a tenant. It is not a window into
    the tenant's own data, and this surface must not become one.
    """
    with eng.begin() as conn:
        columns = {
            r[0] for r in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'control_audit'"))
        }
    assert columns == {"id", "organization_id", "actor", "action", "detail",
                       "created_at"}, (
        "control_audit grew a column — check it is still commercial record"
    )


# ── The role gate ──────────────────────────────────────────────────────────


def test_both_activity_routes_are_named_in_the_matrix(client):
    """The matrix fails CLOSED, so an unnamed route is refused in CI, not
    discovered in production. This states the expected rows out loud."""
    from customer_console import operator_roles
    from customer_console.operators import VIEWER

    for route in ("/activity", "/activity/actions"):
        rule = operator_roles.rule_for("GET", route)
        assert rule is not None and rule.min_role == VIEWER
        assert rule.elevated is False


def test_an_unauthenticated_caller_reads_nothing(client):
    assert client.get("/activity").status_code == 401


# ── The R8 gate cannot silently disarm ─────────────────────────────────────


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(
        encoding="utf-8"
    )
    assert "tests/unit/test_operator_activity.py" in workflow


def test_this_suite_is_named_in_the_spec_verification_block() -> None:
    spec = (
        _ROOT / "project-docs" / "specs" / "operator_identity_and_access.md"
    ).read_text(encoding="utf-8")
    assert "tests/unit/test_operator_activity.py" in spec
