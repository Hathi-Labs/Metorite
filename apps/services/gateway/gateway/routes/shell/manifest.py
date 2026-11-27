"""``GET /shell/manifest``: the resolved shell manifest of the viewer (WS-PKG
PKG-5b part 2).

Spec: ``project-docs/specs/package_host.md`` §5.2. The resolve lives in
``gateway/package_host/resolve.py``, and this module runs no SQL of its own.

**Dark.** The first line of the handler reads
``package_host.flags.package_host_allowed``. While ``PACKAGE_HOST_ORGS``
leaves the org out, the route answers 404 and opens no session. No caller
reads the route yet. PKG-6 makes the workbench read it.

**The viewer comes from the session** (R5, R11). The org is the bound tenant,
and the member is ``user.email``. The request names no member and no org.

**The version is the ETag.** The answer carries ``ETag: "<version>"``. A
request whose ``If-None-Match`` names it gets 304 with no body. The answer is
private to the viewer, so ``Cache-Control`` is ``private, no-cache``.

Fences: ``tests/unit/test_package_host_flag.py`` (the 404 and the order of
the checks), ``tests/unit/test_package_host_resolve.py`` (the 304) and
``tests/unit/test_package_host_resolve_r8.py`` (a real database).
"""

from __future__ import annotations

from typing import Annotated

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from gateway.db import tenant_session as _tenant_session
from gateway.package_host import resolve
from gateway.package_host.flags import package_host_allowed
from gateway.routes.shell.search import router

#: The answer is for one viewer only. A shared cache must not keep it.
CACHE_CONTROL = "private, no-cache"


@router.get("/manifest", summary="The resolved shell manifest of the viewer (PKG-5b)")
async def shell_manifest(
    request: Request,
    user: Annotated[UserContext, Depends(get_current_user)],
) -> Response:
    """The entries of each point that the viewer sees, and their version."""
    if not package_host_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail="Not found")
    async with _tenant_session() as db:
        body = await resolve.resolve_for(db, viewer=user.email, can_use=user.can_use_feature)
    headers = {"ETag": f'"{body["version"]}"', "Cache-Control": CACHE_CONTROL}
    if resolve.etag_matches(request.headers.get("if-none-match"), body["version"]):
        return Response(status_code=304, headers=headers)
    return JSONResponse(body, headers=headers)
