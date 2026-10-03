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
    refuses ``.env``, ``.git/`` and every other secret name.

    H-201 part 3 (§21.15, the P1 of the part 2 review): a ref that is refused
    FAILS the send with 422, and a warning names it. The old rule skipped it,
    so the mail went out without the file the member asked for, and nobody
    was told. This fails closed."""
    if not refs:
        return []
    import mimetypes

    from acb_common import get_logger
    from gateway.routes.workspace import (
        _is_blocked_path,
        _member_agent_workspace,
        _safe_resolve,
    )

    log = get_logger("gateway.email.send")
    out: list[dict] = []
    for ref in refs:
        agent = (ref.agent or "email-assistant").strip() or "email-assistant"
        rel = (ref.path or "").strip()
        ws = _member_agent_workspace(agent, user_email) if rel else None
        full = None
        if ws is not None and not _is_blocked_path(rel):
            try:
                full = _safe_resolve(ws, rel)
            except HTTPException:
                full = None
        if full is None or not full.is_file():
            log.warning("email.artifact_ref_refused", agent=agent[:80], path=rel[:200])
            raise HTTPException(
                status_code=422,
                detail=(
                    f"The attachment '{rel[:200]}' is not a file in your own "
                    "workspace, so nothing was sent."
                ),
            )
        mime, _ = mimetypes.guess_type(full.name)
        out.append({
            "filename": ref.name or full.name,
            "content": full.read_bytes(),
            "mime_type": mime or "application/octet-stream",
        })
    return out


@router.post("/send")
async def send_email(
    req: SendEmailRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
):
    """Send a new email from a connected account.

    A reply names the mail it answers in ``reply_to_message_id``, as its
    provider id or as its local id. That mail must be in the sending mailbox,
    or the route answers 404 before any provider call (EM-T8a, D-EM-19,
    MB-6). A local id becomes the provider id here, so a chat reply threads
    (MB-5).
    """
    async with _tenant_session() as db:
        # Ownership check + auth + rotated-cred persist all live in the session
        # helper (401 on auth failure, 404 on a foreign account).
        async with provider_session(
            db, user.email or "anonymous", account_id=req.account_id,
        ) as sess:
            reply_pmid, reply_thread_id = await _reply_target(
                db, req.account_id, req.reply_to_message_id)
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

            # ``_reply_target`` resolved the conversation id of the message
            # being replied to, so the provider threads the reply. Gmail needs
            # the *thread* id (passing a message id as threadId fails to
            # thread — the "separate email" bug), while Outlook still replies
            # via the message id. Both go to the provider, which uses whichever
            # it needs.
            msg_id = await sess.provider.send_message(
                to=req.to,
                subject=req.subject,
                body_text=send_text,
                body_html=send_html,
                cc=req.cc,
                bcc=req.bcc,
                reply_to_message_id=reply_pmid,
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


async def _reply_target(
    db, account_id: str, reply_to: str | None,
) -> tuple[str | None, str | None]:
    """The provider id and the thread id of the mail a send answers.

    ``reply_to`` is the provider id or the local id of a mail IN THE SENDING
    MAILBOX. Anything else answers 404, so a reply never asks the provider to
    answer a mail of another mailbox (EM-T8a, D-EM-19, MB-6). A new message
    has no ``reply_to`` and gets ``(None, None)``.
    """
    if not reply_to:
        return None, None
    row = (await db.execute(text(
        "SELECT provider_message_id, thread_id FROM email_messages "
        "WHERE account_id = CAST(:aid AS uuid) "
        "AND (provider_message_id = :rid OR id::text = :rid) "
        "ORDER BY (provider_message_id = :rid) DESC LIMIT 1"
    ), {"aid": account_id, "rid": reply_to})).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "The mail this reply answers is not in the sending mailbox. "
                "Reply from the mailbox that received it."
            ),
        )
    return row.provider_message_id, row.thread_id


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
