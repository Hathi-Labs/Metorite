"""WS-61 KB-AI-4 PR A — verified pages, hermetic.

Spec: ``project-docs/specs/knowledge_base.md`` §22.3. A page is verified when
it has an owner AND a review date in the future. These tests read the text of
the one SQL predicate, the PATCH model, the publish SET and the two new
routes. The SQL itself runs in ``test_kb_verified_r8.py``.
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("gateway.routes.knowledge", reason="gateway not installed")

from acb_auth import UserContext, UserRole, build_access
from fastapi import HTTPException
from gateway.routes.knowledge import core, maintenance, pages, proposals
from pydantic import ValidationError

ORG = "11111111-1111-1111-1111-111111111111"


def _user(*perms: str) -> UserContext:
    return UserContext(
        email="m@kb.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:knowledge", *perms]), organization_id=ORG,
    )


class TestTheOnePredicate:
    def test_the_text(self):
        assert core.VERIFIED_SQL("p") == "p.owner_email IS NOT NULL AND p.review_at > now()"
        assert core.VERIFIED_SQL("x").startswith("x.owner_email")

    def test_the_stale_window(self):
        assert core.STALE_DAYS == 365

    def test_the_columns_carry_the_owner_and_the_interval(self):
        assert "p.owner_email" in core.PAGE_COLUMNS
        assert "p.review_every_days" in core.PAGE_COLUMNS

    def test_page_dict_reads_a_row_without_the_columns(self):
        row = SimpleNamespace(
            id="i", space_id="s", parent_id=None, position=0, title="T", icon=None,
            state="published", author_email="a@x.test", reviewers=[], review_at=None,
            source_ref=None, published_at=None, archived_at=None, history_from=1,
            archived_from_state=None, updated_at=None, created_at=None, body_md="",
            origin=None,
        )
        out = core.page_dict(row)
        assert out["owner_email"] is None and out["review_every_days"] is None


class TestThePatch:
    def test_a_bad_address_is_refused(self):
        with pytest.raises(ValidationError):
            pages.PagePatch(owner_email="not an address")
        with pytest.raises(ValidationError):
            pages.PagePatch(owner_email="")

    def test_the_address_is_folded_and_null_clears(self):
        assert pages.PagePatch(owner_email="  A@X.Test ").owner_email == "a@x.test"
        patch = pages.PagePatch(owner_email=None)
        assert patch.owner_email is None and "owner_email" in patch.model_fields_set

    @pytest.mark.parametrize("days", [0, 3651, -1])
    def test_the_interval_is_one_to_3650(self, days):
        with pytest.raises(ValidationError):
            pages.PagePatch(review_every_days=days)

    def test_the_bounds_pass(self):
        assert pages.PagePatch(review_every_days=1).review_every_days == 1
        assert pages.PagePatch(review_every_days=3650).review_every_days == 3650


class TestThePublishSet:
    def test_it_uses_the_interval_and_claims_the_owner(self):
        assert "COALESCE(p.review_every_days, 180)" in pages.PUBLISH_SETS
        assert "owner_email = COALESCE(p.owner_email, p.author_email)" in pages.PUBLISH_SETS

    def test_the_proposals_accept_shares_the_constant(self):
        assert "pages.PUBLISH_SETS" in inspect.getsource(proposals)

    def test_the_verify_set_does_not_touch_updated_at(self):
        assert "updated_at" not in pages.VERIFY_SETS
        assert "COALESCE(p.review_every_days, 180)" in pages.VERIFY_SETS


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None


def _verify(monkeypatch: pytest.MonkeyPatch, state: str | None, level: str | None) -> int:
    page = None if state is None else SimpleNamespace(id="p1", space_id="s1", state=state)

    async def _load(db, user, page_id, lock=False):
        return page

    async def _space_of(db, user, p):
        return SimpleNamespace(id="s1")

    async def _level(db, user, space):
        return level

    monkeypatch.setattr(core, "_tenant_session", lambda: _Session())
    monkeypatch.setattr(core, "load_page", _load)
    monkeypatch.setattr(pages, "_space_of", _space_of)
    monkeypatch.setattr(core, "space_level", _level)
    with pytest.raises(HTTPException) as err:
        asyncio.run(pages.verify_page("p1", _user()))
    return err.value.status_code


class TestTheVerifyRoute:
    def test_a_draft_is_409(self, monkeypatch):
        assert _verify(monkeypatch, "draft", "write") == 409

    def test_no_write_is_403(self, monkeypatch):
        assert _verify(monkeypatch, "published", "read") == 403

    def test_a_page_the_caller_cannot_see_is_404(self, monkeypatch):
        assert _verify(monkeypatch, None, "write") == 404


class TestTheOwnerMustBeInTheOrg:
    def _patch(self, monkeypatch: pytest.MonkeyPatch, member: bool) -> list[str]:
        import gateway.routes.admin.assistant_profile_people as people

        asked: list[str] = []

        async def _in_org(db, email):
            asked.append(email)
            return member

        monkeypatch.setattr(people, "email_in_org", _in_org)
        return asked

    def test_an_outside_address_is_422(self, monkeypatch):
        asked = self._patch(monkeypatch, False)
        body = pages.PagePatch(owner_email="x@elsewhere.test")
        with pytest.raises(HTTPException) as err:
            asyncio.run(pages._check_owner(None, body, body.model_fields_set))
        assert err.value.status_code == 422 and asked == ["x@elsewhere.test"]

    def test_a_member_passes_and_null_needs_no_check(self, monkeypatch):
        asked = self._patch(monkeypatch, True)
        body = pages.PagePatch(owner_email="a@x.test")
        asyncio.run(pages._check_owner(None, body, body.model_fields_set))
        clear = pages.PagePatch(owner_email=None)
        asyncio.run(pages._check_owner(None, clear, clear.model_fields_set))
        other = pages.PagePatch(review_every_days=30)
        asyncio.run(pages._check_owner(None, other, other.model_fields_set))
        assert asked == ["a@x.test"]

    def test_it_uses_the_helper_of_the_assistant_not_a_copy(self):
        assert "email_in_org" in inspect.getsource(pages._check_owner)
        assert "SELECT" not in inspect.getsource(pages._check_owner)


class TestAVerifierNeedsAnAddress:
    def test_no_email_is_403_before_any_write(self, monkeypatch):
        user = UserContext(
            email="", role=UserRole.EMPLOYEE,
            access=build_access(["feature:knowledge"]), organization_id=ORG,
        )

        def _boom():
            raise AssertionError("opened a session")

        monkeypatch.setattr(core, "_tenant_session", _boom)
        with pytest.raises(HTTPException) as err:
            asyncio.run(pages.verify_page("p1", user))
        assert err.value.status_code == 403


class TestTheMaintenanceRoute:
    def test_a_member_is_403(self):
        with pytest.raises(HTTPException) as err:
            asyncio.run(maintenance.list_maintenance(reason="all", limit=10, user=_user()))
        assert err.value.status_code == 403

    def test_an_unknown_reason_is_422(self):
        with pytest.raises(HTTPException) as err:
            asyncio.run(maintenance.list_maintenance(
                reason="nope", limit=10, user=_user("knowledge:manage")))
        assert err.value.status_code == 422

    def test_each_reason_is_a_boolean_column_of_one_select(self):
        src = inspect.getsource(maintenance.list_maintenance)
        assert "is_expired" in src and "is_unowned" in src and "is_stale" in src
        assert "UNION" not in src
