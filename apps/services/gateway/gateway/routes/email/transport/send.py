"""Transport · send — outbound send (and the learn-from-sent hook into the
automation layer via a deferred import)."""

from __future__ import annotations

from acb_auth import UserContext, get_current_user
from fastapi import BackgroundTasks, Depends, HTTPException
from gateway.routes.email.core import (
    _tenant_session,
    provider_session,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text


class SendAttachment(BaseModel):
    filename: str
    mime_type: str = "application/octet-stream"
    content_b64: str  # base64-encoded file content


class ArtifactAttachment(BaseModel):
    """Attach a file from the sender's own agent workspace by path.

    Lets the email-assistant attach files it produced, and lets the compose UI
    attach the files the member uploaded. Resolved server-side. H-201 part 2:
    only the sender's own ``personal`` workspace is a source
    (``workspace._member_agent_workspace``). A shared agent's clone holds the
    run output of every tenant, so it is never a source."""
    path: str  # workspace-relative path (e.g. "outputs/quote.pdf")
    name: str | None = None  # display filename (defaults to the file's name)
    agent: str | None = None  # source agent workspace (defaults to email-assistant)


class SendEmailRequest(BaseModel):
    account_id: str
    to: list[str]
    subject: str
    body_text: str
    body_html: str | None = None
    cc: list[str] | None = None
    bcc: list[str] | None = None
    reply_to_message_id: str | None = None
    attachments: list[SendAttachment] | None = None
    # Workspace artifacts to attach (resolved to bytes server-side).
    artifacts: list[ArtifactAttachment] | None = None


def load_artifact_attachments(
    refs: "list[ArtifactAttachment] | None",
    user_email: str | None = None,
) -> list[dict]:
    """Resolve workspace-artifact references to ``[{filename, content,
    mime_type}]`` for a provider. Reads each ref from the sending member's OWN
    workspace of its source agent (default ``email-assistant``).

    H-201 part 2 (``projects_ai_chat.md`` §21.14). The source is only
    ``workspace._member_agent_workspace``, so a shared agent's clone is never
    read. The path goes through ``_safe_resolve``, and ``_is_blocked_path``
    refuses ``.env``, ``.git/`` and every other secret name. A ref that fails
    is skipped, and a warning names it. Best-effort: a bad ref never fails
    the send."""
    if not refs:
        return []
    out: list[dict] = []
    try:
        import mimetypes

        from acb_common import get_logger
        from gateway.routes.workspace import (
            _is_blocked_path,
            _member_agent_workspace,
            _safe_resolve,
        )

        log = get_logger("gateway.email.send")
        for ref in refs:
            agent = (ref.agent or "email-assistant").strip() or "email-assistant"
            rel = (ref.path or "").strip()
            if not rel:
                continue
            ws = _member_agent_workspace(agent, user_email)
            full = None
            if ws is not None and not _is_blocked_path(rel):
                try:
                    full = _safe_resolve(ws, rel)
                except HTTPException:
                    full = None
            if full is None or not full.is_file():
                log.warning("email.artifact_ref_refused", agent=agent[:80], path=rel[:200])
                continue
            mime, _ = mimetypes.guess_type(full.name)
            out.append({
                "filename": ref.name or full.name,
                "content": full.read_bytes(),
                "mime_type": mime or "application/octet-stream",
            })
    except Exception:
        pass
    return out


@router.post("/send")
async def send_email(
    req: SendEmailRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
):
    """Send a new email from a connected account."""
    async with _tenant_session() as db:
        # Ownership check + auth + rotated-cred persist all live in the session
        # helper (401 on auth failure, 404 on a foreign account).
        async with provider_session(
            db, user.email or "anonymous", account_id=req.account_id,
        ) as sess:
            attachments: list[dict] | None = None
            if req.attachments:
                import base64 as _b64
                try:
                    attachments = [
                        {
                            "filename": a.filename,
                            "mime_type": a.mime_type,
                            "content": _b64.b64decode(a.content_b64),
                        }
                        for a in req.attachments
                    ]
                except Exception as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid attachment encoding: {exc}",
                    ) from exc

            # Resolve any workspace-artifact attachments (agent-created files)
            # to bytes and merge them in alongside base64 attachments.
            artifact_atts = load_artifact_attachments(req.artifacts, user.email)
            if artifact_atts:
                attachments = (attachments or []) + artifact_atts

            # Append the account's HTML signature once, here at the single send
            # choke point (with a plain-text fallback), so every reply / new
            # message carries it. The drafter no longer bakes it into the body.
            from gateway.routes.email.signature import build_signed_bodies
            sig_row = (await db.execute(text(
                "SELECT signature FROM email_assistant_settings "
                "WHERE account_id = :aid"
            ), {"aid": req.account_id})).fetchone()
            send_text, send_html = build_signed_bodies(
                (sig_row.signature if sig_row else "") or "",
                req.body_text, req.body_html)

            # Resolve the conversation id of the message being replied to so the
            # provider threads the reply. ``reply_to_message_id`` from the client
            # is a provider *message* id; Gmail needs the *thread* id (passing a
            # message id as threadId fails to thread — the "separate email"
            # bug), while Outlook still replies via the message id. Look it up
            # once and hand both to the provider, which uses whichever it needs.
            reply_thread_id: str | None = None
            if req.reply_to_message_id:
                trow0 = (await db.execute(text(
                    "SELECT thread_id FROM email_messages "
                    "WHERE account_id = :aid AND provider_message_id = :pmid"
                ), {"aid": req.account_id,
                    "pmid": req.reply_to_message_id})).fetchone()
                reply_thread_id = trow0.thread_id if trow0 else None

            msg_id = await sess.provider.send_message(
                to=req.to,
                subject=req.subject,
                body_text=send_text,
                body_html=send_html,
                cc=req.cc,
                bcc=req.bcc,
                reply_to_message_id=req.reply_to_message_id,
                attachments=attachments,
                thread_id=reply_thread_id,
            )

        # Commit the rotated-cred persist the session wrote on clean exit.

        # If this was a reply, learn from how the user edited the AI's draft.
        if req.reply_to_message_id and req.body_text and reply_thread_id:
            try:
                from gateway.routes.email.automation import (
                    _cleanup_thread_drafts,
                    _learn_from_sent,
                    _mark_thread_replied,
                )
                background.add_task(
                    _learn_from_sent, req.account_id, reply_thread_id,
                    req.body_text)
                # Move the thread out of "Reply" → Awaiting Reply /
                # Done. Pass the just-sent reply so the AI judges the
                # thread WITH it (the send isn't mirrored locally yet).
                background.add_task(
                    _mark_thread_replied, req.account_id, reply_thread_id,
                    req.body_text, req.subject)
                # Trash leftover drafts in the thread (AI draft / auto-save).
                background.add_task(
                    _cleanup_thread_drafts, req.account_id, reply_thread_id)
            except Exception:
                pass

        return {"id": msg_id, "ok": True}


class ImportArtifactRequest(BaseModel):
    source_agent: str
    source_path: str
    name: str | None = None


@router.post("/artifacts/import")
async def import_artifact(
    req: ImportArtifactRequest,
    user: UserContext = Depends(get_current_user),
):
    """Copy a file from the caller's own workspace of another agent into the
    email-assistant workspace (``agent-data/``) so it can be attached to
    emails and browsed / downloaded in the email artifact picker. Returns the
    new email-assistant-relative path.

    H-201 part 2 (``projects_ai_chat.md`` §21.14). Both ends resolve only
    through ``workspace._member_agent_workspace``, so a shared agent's clone
    is never a source. The path goes through ``_safe_resolve``, and
    ``_is_blocked_path`` refuses every secret name. A refusal is 404."""
    import shutil
    from pathlib import Path

    from gateway.routes.workspace import (
        _is_blocked_path,
        _member_agent_workspace,
        _safe_resolve,
    )

    src_ws = _member_agent_workspace(req.source_agent, user.email)
    dst_ws = _member_agent_workspace("email-assistant", user.email)
    if not src_ws or not dst_ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    rel = (req.source_path or "").strip()
    if not rel or _is_blocked_path(rel):
        raise HTTPException(status_code=404, detail="Source artifact not found")
    try:
        src = _safe_resolve(src_ws, rel)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Source artifact not found") from None
    if not src.is_file():
        raise HTTPException(status_code=404, detail="Source artifact not found")

    dest_dir = (dst_ws / "agent-data").resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Sanitise to a bare filename (no directory traversal in the chosen name).
    fname = Path(req.name or src.name).name or src.name
    dest = dest_dir / fname
    # Avoid clobbering an existing file — suffix " (1)", " (2)", …
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        i = 1
        while (dest_dir / f"{stem} ({i}){suffix}").exists():
            i += 1
        dest = dest_dir / f"{stem} ({i}){suffix}"
    shutil.copy2(src, dest)

    rel = str(dest.relative_to(dst_ws.resolve())).replace("\\", "/")
    return {"path": rel, "name": dest.name}
