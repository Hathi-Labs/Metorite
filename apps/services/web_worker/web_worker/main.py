"""The HTTP API of the web worker (WEB-6, ``web_research.md`` section 5.1).

Only the orchestrator calls it, with the internal token. Start it with
``uvicorn --factory web_worker.main:create_app``.

* :func:`create_app` refuses to start when ``WEB_WORKER_TOKEN`` is empty.
  The meeting bot skips its check with no token. This worker never does.
* Each route but ``/health`` compares the bearer in constant time.
* ``POST /render`` takes ``{url, org_id, job_id}`` and answers with Markdown.
* ``GET /jobs/{job_id}/files`` and ``GET /jobs/{job_id}/files/{name}`` read
  the files of a running job. A job of another organization gives ``404``.

Fence: ``tests/unit/test_web_worker.py``.
"""

from __future__ import annotations

import asyncio
import hmac
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from web_worker.egress import host_of
from web_worker.jobs import BadId, Job, JobExists, JobNotFound, JobStore, NoSlot
from web_worker.limits import JOB_TIMEOUT_S
from web_worker.render import PageRefused

#: The root of the job directories. Each job makes its own directory under it.
DEFAULT_JOB_ROOT = "/tmp/web-worker-jobs"

Renderer = Callable[[str, Job], Awaitable[Any]]


class EmptyToken(RuntimeError):
    """``WEB_WORKER_TOKEN`` is empty, so the worker does not start."""


class RenderRequest(BaseModel):
    url: str
    org_id: str
    job_id: str


def _token(value: str | None) -> str:
    token = (value if value is not None else os.environ.get("WEB_WORKER_TOKEN", "")).strip()
    if not token:
        raise EmptyToken("WEB_WORKER_TOKEN is empty. The web worker refuses to start.")
    return token


async def _render(jobs: JobStore, renderer: Renderer, body: RenderRequest) -> Any:
    """One job: a slot, a directory, the render, then the end of the job."""
    try:
        with jobs.open(body.org_id, body.job_id) as job:
            async with asyncio.timeout(JOB_TIMEOUT_S):
                result = await renderer(body.url, job)
    except BadId:
        return JSONResponse({"error": "bad id"}, status_code=422)
    except JobExists:
        return JSONResponse({"error": "job exists"}, status_code=409)
    except NoSlot as exc:
        return JSONResponse({"error": "no slot", "scope": str(exc)}, status_code=429)
    except PageRefused as exc:
        return JSONResponse(
            {"error": "refused", "host": host_of(body.url), "reason": exc.reason},
            status_code=422,
        )
    except TimeoutError:
        return JSONResponse({"error": "timeout"}, status_code=504)
    except Exception:  # a page fault never names the path
        return JSONResponse({"error": "render failed", "host": host_of(body.url)}, status_code=502)
    return {
        "job_id": body.job_id,
        "url": result.final_url,
        "title": result.title,
        "markdown": result.markdown,
        "refused": len(result.egress.refused),
    }


def create_app(
    token: str | None = None,
    *,
    renderer: Renderer | None = None,
    store: JobStore | None = None,
) -> FastAPI:
    """The app. ``token`` defaults to ``WEB_WORKER_TOKEN``. A test passes a
    fake ``renderer``. With none, the app starts Chromium in its lifespan."""
    secret = _token(token).encode("utf-8")
    jobs = store or JobStore(os.environ.get("WEB_WORKER_JOB_ROOT") or DEFAULT_JOB_ROOT)
    chromium = None
    if renderer is None:
        from web_worker.render import ChromiumRenderer

        chromium = ChromiumRenderer()
        renderer = chromium

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if chromium is not None:
            await chromium.start()
        try:
            yield
        finally:
            if chromium is not None:
                await chromium.stop()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.jobs = jobs

    def auth(authorization: str | None = Header(default=None)) -> None:
        given = (authorization or "").encode("utf-8")
        if not hmac.compare_digest(given, b"Bearer " + secret):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/render", dependencies=[Depends(auth)])
    async def render(body: RenderRequest) -> Any:
        return await _render(jobs, renderer, body)

    @app.get("/jobs/{job_id}/files", dependencies=[Depends(auth)])
    async def list_files(job_id: str, org_id: str) -> dict[str, list[str]]:
        try:
            return {"files": jobs.files(org_id, job_id)}
        except JobNotFound:
            raise HTTPException(status_code=404, detail="not found") from None

    @app.get("/jobs/{job_id}/files/{name}", dependencies=[Depends(auth)])
    async def read_file(job_id: str, name: str, org_id: str) -> Response:
        try:
            data = jobs.read(org_id, job_id, name)
        except JobNotFound:
            raise HTTPException(status_code=404, detail="not found") from None
        return Response(data, media_type="application/octet-stream")

    @app.exception_handler(HTTPException)
    async def _plain(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return app
