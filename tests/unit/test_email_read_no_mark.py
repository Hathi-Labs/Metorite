"""A read of one mail with no change to its read state. WS-48 N2.

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §9 N2, build notes.
The READ step of ``narrow_and_read`` reads up to 25 kept mails in the
background. That is not the member opening them, so it must not mark them
read. ``GET /email/messages/{id}`` gains ``mark_read`` (default ``true``), and
the email adapter sends ``mark_read=false``.

R8: these tests run the REAL route function against a REAL Postgres, under
FORCE RLS, as a non-privileged role (``promoted`` / ``app_engine`` of
``test_h3_rls_promotion_rehearsal.py``). With no
``TENANT_LADDER_DATABASE_URL`` they skip, and a skip is not a pass.

Mutations this file catches (R7), each one run red before the change:

* the route marks the mail read with ``mark_read=false`` (the ``if`` goes) ->
  ``test_mark_read_false_leaves_is_read_alone``;
* the default stops marking the mail read, so the app changes ->
  ``test_the_default_still_marks_the_mail_read``;
* ``mark_read=false`` skips the owner scope -> ``test_mark_read_false_keeps_the_owner_scope``.
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from fastapi import HTTPException
from gateway.routes.email.transport import messages as messages_mod
from sqlalchemy import text

from tests.unit.test_email_keep_separate import (
    _account,
    _as_member,
    _assert_non_priv,
    _mail,
    _purge,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)


def _is_read(admin, message_id: str) -> bool:
    with admin.connect() as c:
        return bool(c.execute(text(
            "SELECT is_read FROM email_messages WHERE id = CAST(:m AS uuid)"),
            {"m": message_id}).scalar_one())


def _setup(p) -> tuple[str, str, UserContext]:
    tag = uuid.uuid4().hex[:8]
    owner = f"member-{tag}@t8g1.test"
    box = _account(p.admin_engine, org=p.org_b, owner=owner, default=True)
    message_id = _mail(p.admin_engine, org=p.org_b, account_id=box,
                       sender=f"s-{tag}@sender.test", body="the body of the mail")
    me = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
    return tag, message_id, me


@_DB_GATE
class TestAReadWithNoMark:

    async def test_mark_read_false_leaves_is_read_alone(
        self, promoted, app_engine,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        tag, message_id, me = _setup(p)
        try:
            assert _is_read(p.admin_engine, message_id) is False
            async with _as_member(p, p.org_b):
                got = await messages_mod.get_message(message_id, user=me, mark_read=False)
            # The read itself is whole: the body comes back.
            assert str(got.id) == message_id
            assert got.body_text == "the body of the mail"
            assert _is_read(p.admin_engine, message_id) is False
        finally:
            _purge(p.admin_engine, f"%-{tag}@t8g1.test")

    async def test_the_default_still_marks_the_mail_read(
        self, promoted, app_engine,  # noqa: F811
    ):
        """The app sends no ``mark_read``. A direct Python call gets the
        ``Query`` object as the value, and that must read as the default too."""
        _assert_non_priv(app_engine)
        p = promoted
        tag, message_id, me = _setup(p)
        tag2, message_id2, me2 = _setup(p)
        try:
            async with _as_member(p, p.org_b):
                await messages_mod.get_message(message_id, user=me, mark_read=True)
                await messages_mod.get_message(message_id2, user=me2)
            assert _is_read(p.admin_engine, message_id) is True
            assert _is_read(p.admin_engine, message_id2) is True
        finally:
            _purge(p.admin_engine, f"%-{tag}@t8g1.test")
            _purge(p.admin_engine, f"%-{tag2}@t8g1.test")

    async def test_mark_read_false_keeps_the_owner_scope(
        self, promoted, app_engine,  # noqa: F811
    ):
        """Another member of the same org, and a member of another org, get a
        404 and change nothing."""
        _assert_non_priv(app_engine)
        p = promoted
        tag, message_id, _me = _setup(p)
        same_org = UserContext(email=f"other-{tag}@t8g1.test", role=UserRole.EMPLOYEE,
                               organization_id=p.org_b)
        try:
            async with _as_member(p, p.org_b):
                with pytest.raises(HTTPException) as caught:
                    await messages_mod.get_message(message_id, user=same_org, mark_read=False)
            assert caught.value.status_code == 404
            other_org = UserContext(email=_me.email, role=UserRole.EMPLOYEE,
                                    organization_id=p.org_a)
            async with _as_member(p, p.org_a):
                with pytest.raises(HTTPException) as caught:
                    await messages_mod.get_message(message_id, user=other_org, mark_read=False)
            assert caught.value.status_code == 404
            assert _is_read(p.admin_engine, message_id) is False
        finally:
            _purge(p.admin_engine, f"%-{tag}@t8g1.test")
