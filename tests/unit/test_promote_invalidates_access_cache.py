"""OB-0d (task 1350): the promotion of an invited member drops their cached access.

``promote_invited_member`` turns an ``invited`` member active at sign-in. An
earlier resolve in the same process may have cached them as refused for 60 s,
and the first ``/auth/me`` then read that entry. This file needs no database,
so it runs on a PC with no Postgres. The R8 twin, with real rows, is
``TestTheFirstSignInReadsFresh`` in ``test_invited_member_promotion.py``.
"""
from __future__ import annotations

import uuid

import pytest


class TestTheCacheDropNeedsNoDatabase:
    """The fence with no SQL: the drop does not depend on the
    promotion's outcome, so it runs where Postgres is absent."""

    @pytest.mark.parametrize("outcome", ["no_rows", "database_error"])
    async def test_the_member_entry_is_dropped_in_every_outcome(
        self, monkeypatch, outcome,
    ):
        import time

        from acb_auth import access

        class _Result:
            def mappings(self):
                return self

            def all(self):
                return []

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, *_a, **_k):
                if outcome == "database_error":
                    raise RuntimeError("database down")
                return _Result()

        monkeypatch.setattr(access, "_get_session_factory", lambda: (lambda: _Session()))
        email = f"Cache-{uuid.uuid4().hex[:8]}@x.example"
        other = f"other-{uuid.uuid4().hex[:8]}@x.example"
        access._cache[email.lower()] = (time.monotonic() + 60, object())
        access._cache[other] = (time.monotonic() + 60, object())
        try:
            await access.promote_invited_member(email=email)
            assert email.lower() not in access._cache
            assert other in access._cache
        finally:
            access._cache.pop(email.lower(), None)
            access._cache.pop(other, None)
