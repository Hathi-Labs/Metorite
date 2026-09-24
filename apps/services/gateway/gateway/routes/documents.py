"""``POST /documents/pdf`` — HTML the app formatted, as a PDF (WS-27bm S8).

Spec: ``project-docs/specs/projects_ai_chat.md`` §14.

The one caller today is the Reports download. The browser fetches a saved
report's render, formats it ONCE with ``lib/reportEmail.ts``'s
``reportDocument``, and posts that HTML here. The route lays it out through
``gateway.pdf_render`` and sends the file back. There is no second formatter in
Python, so the email, the Markdown file and the PDF cannot disagree.

Four guards, each tested in ``tests/unit/test_documents_pdf_route.py``:

1. **An identity.** The app-wide ``require_authenticated`` covers this route
   like every other. The route also refuses an anonymous context itself, so
   the refusal does not depend on how the app was assembled.
2. **``Content-Type: text/html`` only** — 415 for anything else. The body is
   the HTML itself, not JSON, so there is one thing to size and one thing to
   sanitize.
3. **A size cap** — ``pdf_render.MAX_SOURCE_BYTES``, checked on the declared
   length and again while the body streams in, so a lying header buys nothing.
4. **No fetch.** ``pdf_render`` keeps only text tags and gives the renderer no
   archive. A posted ``<img src="http://169.254.169.254/…">`` loads nothing.

The route reads no table and writes none (R5): it is a pure transform.
"""
from __future__ import annotations

import asyncio

from acb_auth import UserContext, get_current_user
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from gateway.pdf_render import (
    MAX_SOURCE_BYTES,
    PdfRenderError,
    attachment_disposition,
    html_to_pdf,
    pdf_filename,
)

router = APIRouter(prefix="/documents", tags=["documents"])


async def _read_capped(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_SOURCE_BYTES:
        raise HTTPException(status_code=413, detail="The document is too large for a PDF.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_SOURCE_BYTES:
            raise HTTPException(status_code=413, detail="The document is too large for a PDF.")
    return bytes(body)


@router.post("/pdf", response_model=None)
async def html_document_to_pdf(
    request: Request,
    filename: str = Query("document.pdf", max_length=200),
    user: UserContext = Depends(get_current_user),
) -> Response:
    """Lay out posted HTML as a PDF download. See the module docstring."""
    if not user.email:
        raise HTTPException(status_code=401, detail="Authentication required")
    media = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if media != "text/html":
        raise HTTPException(status_code=415, detail="Send the document as text/html.")
    raw = await _read_capped(request)
    source = raw.decode("utf-8", errors="replace")
    if not source.strip():
        raise HTTPException(status_code=422, detail="The document is empty.")
    try:
        pdf = await asyncio.to_thread(html_to_pdf, source)
    except PdfRenderError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": attachment_disposition(pdf_filename(filename))},
    )
