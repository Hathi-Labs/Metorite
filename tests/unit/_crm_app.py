"""One shared ``/crm`` app for the R8 suites (CIV-9, task 1637).

Each R8 helper used to run ``FastAPI()`` and ``include_router(crm_package.router)``
on EVERY request. That rebuilt hundreds of pydantic fields each time: 11 s of a
38 s test (profiled 2026-11-07). The app holds no per-request state except the
``get_current_user`` override, which every helper sets on each call. Tests run one
after the other, so one app per process is safe.

⚠️ One app means one ``get_current_user`` override at a time. Two requests as
DIFFERENT users at once (``asyncio.gather``) would race on it. No suite does
that today; await such calls one after the other.

Do not use it for a test that overrides another dependency: it would leak into
the next test. Build your own app there.
"""

from __future__ import annotations

from typing import Any

_APP: Any = None


def crm_app() -> Any:
    global _APP
    if _APP is None:
        from fastapi import FastAPI
        from gateway.routes import crm as crm_package

        app = FastAPI()
        app.include_router(crm_package.router)
        _APP = app
    return _APP
