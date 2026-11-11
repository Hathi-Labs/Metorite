"""R8 — WS-KG KG-3b, the vector half of Email on a REAL Postgres.

``semantic_messages`` reads ``email_embeddings`` joined to ``email_messages``
under FORCE RLS, with the account scope of Email. The embedder is a FAKE:
no test here calls a model.

* (a) An org with no vector: the answer is ``[]`` and the embedder is NOT called.
* (b) The nearest mail of the member is found. Mail below the floor is not.
* (c) Org B holds a vector identical to the query, and org A never sees it.
* (d) A member who does not own the mailbox does not find its mail.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, clear_tenant, release_tenant
from gateway.routes.email.transport.search import semantic_messages
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_keep_separate import _assert_non_priv
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:6]
OWNER_A = f"owner-a-{TAG}@kg3b.test"
OWNER_B = f"owner-b-{TAG}@kg3b.test"
STRANGER = f"stranger-{TAG}@kg3b.test"
DIM = 1536


def _vec(*lead: float) -> str:
    return "[" + ",".join(str(x) for x in [*lead, *([0.0] * (DIM - len(lead)))]) + "]"


def _user(email: str, org: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access(["feature:email"]))


def _seed(admin: Any, org: str, owner: str, subject: str, vector: str | None) -> tuple[str, str]:
    with admin.begin() as c:
        acct = str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, organization_id) VALUES (:u, 'microsoft', :m, 'x', "
            "CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "m": f"{TAG}-{uuid.uuid4().hex[:6]}@kg3b.test", "o": org}).scalar_one())
        msg = str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, folder, "
            "from_address, to_addresses, subject, body_text, received_at, organization_id) "
            "VALUES (CAST(:a AS uuid), :p, 'inbox', "
            "'{\"name\": \"S\", \"email\": \"s@x.test\"}'::jsonb, '[]'::jsonb, :s, 'body', now(), "
            "CAST(:o AS uuid)) RETURNING id"),
            {"a": acct, "p": f"{TAG}-{uuid.uuid4().hex[:6]}", "s": subject, "o": org}).scalar_one())
        if vector is not None:
            c.execute(text(
                "INSERT INTO email_embeddings (message_id, account_id, embedding, model, "
                "content_hash, organization_id) VALUES (CAST(:m AS uuid), CAST(:a AS uuid), "
                "CAST(:v AS vector), 'm', 'h', CAST(:o AS uuid))"),
                {"m": msg, "a": acct, "v": vector, "o": org})
    return acct, msg


def _purge(admin: Any, accts: list[str]) -> None:
    with admin.begin() as c:
        for a in accts:
            for t in ("email_embeddings", "email_messages"):
                c.execute(text(f"DELETE FROM {t} WHERE account_id = CAST(:a AS uuid)"), {"a": a})
            c.execute(text("DELETE FROM email_accounts WHERE id = CAST(:a AS uuid)"), {"a": a})


class _Embedder:
    def __init__(self, vector: list[float] | None) -> None:
        self.vector, self.calls = vector, 0

    async def __call__(self, _words: str) -> list[float] | None:
        self.calls += 1
        return self.vector


async def _ask(user: UserContext, embed: _Embedder) -> list[dict]:
    bind_tenant(user.organization_id)
    return await semantic_messages(user=user, words="the bill", k=5, received_after=None,
                                   embed=embed)


@pytest.fixture
async def world(promoted, app_engine):  # noqa: F811
    _assert_non_priv(app_engine)
    p = promoted
    near = _seed(p.admin_engine, p.org_a, OWNER_A, "Invoice March", _vec(1.0))
    far = _seed(p.admin_engine, p.org_a, OWNER_A, "Lunch menu", _vec(0.0, 1.0))
    theirs = _seed(p.admin_engine, p.org_b, OWNER_B, "Invoice March B", _vec(1.0))
    bare = _seed(p.admin_engine, p.org_b, OWNER_B, "No vector yet", None)
    token = clear_tenant()
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            yield {"p": p, "near": near, "far": far, "theirs": theirs, "bare": bare}
    finally:
        release_tenant(token)
        _purge(p.admin_engine, [near[0], far[0], theirs[0], bare[0]])


async def test_the_nearest_mail_is_found_and_the_far_one_is_not(world) -> None:
    p = world["p"]
    rows = await _ask(_user(OWNER_A, p.org_a), _Embedder([1.0] + [0.0] * (DIM - 1)))
    assert [r["id"] for r in rows] == [world["near"][1]]
    assert rows[0]["sim"] == pytest.approx(1.0)


async def test_another_org_never_appears_though_its_vector_is_identical(world) -> None:
    p = world["p"]
    rows = await _ask(_user(OWNER_A, p.org_a), _Embedder([1.0] + [0.0] * (DIM - 1)))
    assert world["theirs"][1] not in {r["id"] for r in rows}
    rows_b = await _ask(_user(OWNER_B, p.org_b), _Embedder([1.0] + [0.0] * (DIM - 1)))
    assert [r["id"] for r in rows_b] == [world["theirs"][1]]


async def test_a_member_who_does_not_own_the_mailbox_finds_nothing(world) -> None:
    p = world["p"]
    embed = _Embedder([1.0] + [0.0] * (DIM - 1))
    assert await _ask(_user(STRANGER, p.org_a), embed) == []


async def test_an_org_with_no_vector_costs_no_embed_call(world) -> None:
    p = world["p"]
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM email_embeddings WHERE account_id = CAST(:a AS uuid)"),
                  {"a": world["theirs"][0]})
    embed = _Embedder([1.0] + [0.0] * (DIM - 1))
    assert await _ask(_user(OWNER_B, p.org_b), embed) == []
    assert embed.calls == 0


async def test_a_query_that_cannot_embed_gives_nothing(world) -> None:
    p = world["p"]
    embed = _Embedder(None)
    assert await _ask(_user(OWNER_A, p.org_a), embed) == []
    assert embed.calls == 1
