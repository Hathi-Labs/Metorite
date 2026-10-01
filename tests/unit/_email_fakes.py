"""Seam double for the converted ``gateway.routes.email`` handlers (H2).

Converted request handlers acquire their session via the package's
``_tenant_session`` alias — ``acb_common.db.tenant_session``, an async context
manager that begins a transaction, binds the tenant GUC and commits on clean
exit. ``bind_db`` mirrors only that SHAPE over any test double: it yields the
fake, then ``await``s its ``commit`` (if it has one) on a clean exit — exactly
like the real wrapper, which commits reads too — and when the body raised it
commits nothing and awaits the fake's ``rollback``, as the real wrapper does
(EM-T1b-2 fix round 1). Patch it over the SUT module's ``_tenant_session`` BY NAME
(each submodule imports the alias from ``core``, so patch the module you call).

Not named ``test_*``, so pytest imports it without collecting it. The GUC
plumbing itself is pinned by ``test_tenant_session.py``, not mirrored here.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any


def bind_db(db: Any):
    """A ``_tenant_session`` double bound to ``db``.

    The returned factory records each entry in ``.calls`` so a test can assert
    "no session was opened" (the old ``get_db.assert_not_awaited()`` proxy),
    and each block that raised in ``.rolled_back``. On a raise it awaits the
    fake's ``rollback`` (when it has one) and commits nothing, as the real
    seam does.
    """
    calls: list[int] = []
    rolled_back: list[int] = []

    @asynccontextmanager
    async def _tenant_session():
        calls.append(1)
        try:
            yield db
        except BaseException:
            # The real seam rolls back and re-raises. Mirror it, so a body
            # that raised can never look committed: a fake that keeps a
            # journal drops the writes of this block in its ``rollback``.
            rollback = getattr(db, "rollback", None)
            if rollback is not None:
                await rollback()
            rolled_back.append(1)
            raise
        commit = getattr(db, "commit", None)
        if commit is not None:
            await commit()

    _tenant_session.calls = calls  # type: ignore[attr-defined]
    _tenant_session.rolled_back = rolled_back  # type: ignore[attr-defined]
    _tenant_session.db = db  # type: ignore[attr-defined]
    return _tenant_session
