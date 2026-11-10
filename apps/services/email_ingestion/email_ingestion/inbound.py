"""aiosmtpd inbound email receiver.

Runs a lightweight async SMTP server that accepts inbound emails and persists
them directly to the email_messages table.  No external provider needed —
this is the free/open-source alternative to Brevo / SendGrid inbound parse.

Usage (from gateway startup):
    from email_ingestion.inbound import start_inbound_server, stop_inbound_server
    await start_inbound_server(host="0.0.0.0", port=10025, account_id=...)

Environment:
    EMAIL_INBOUND_PORT (default 10025)
    EMAIL_INBOUND_HOST (default "127.0.0.1")
    EMAIL_INBOUND_ACCOUNT_ID — UUID of the email_accounts row to file mail under
"""

from __future__ import annotations

import asyncio
import email as email_lib
import logging
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Any
from uuid import uuid4

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP, Envelope, Session

from .persist import upsert_message
from .providers.base import Attachment, EmailAddress, EmailMessage

logger = logging.getLogger(__name__)

# Singleton controller reference for lifecycle management
_controller: Controller | None = None
_inbound_account_id: str | None = None


# -- Header decoding helpers --------------------------------------------------


def _decode_header_value(value: str) -> str:
    """Decode RFC 2047 encoded header value."""
    if not value:
        return ""
    try:
        parts = decode_header(value)
        result = ""
        for part, charset in parts:
            if isinstance(part, bytes):
                result += part.decode(charset or "utf-8", errors="replace")
            else:
                result += str(part)
        return result
    except Exception:
        return value


def _parse_address(raw: str) -> EmailAddress | None:
    """Parse an email address from a header value."""
    if not raw:
        return None
    decoded = _decode_header_value(raw)
    if "<" in decoded and ">" in decoded:
        name = decoded.split("<")[0].strip().strip('"')
        email = decoded.split("<")[1].split(">")[0].strip()
        return EmailAddress(name=name, email=email)
    return EmailAddress(name="", email=decoded.strip())


def _parse_address_list(raw: str) -> list[EmailAddress]:
    """Parse a comma-separated list of email addresses."""
    if not raw:
        return []
    decoded = _decode_header_value(raw)
    addresses: list[EmailAddress] = []
    current: list[str] = []
    in_quote = False
    for ch in decoded:
        if ch == '"':
            in_quote = not in_quote
            current.append(ch)
        elif ch == "," and not in_quote:
            addr = _parse_address("".join(current).strip())
            if addr and addr.email:
                addresses.append(addr)
            current = []
        else:
            current.append(ch)
    if current:
        addr = _parse_address("".join(current).strip())
        if addr and addr.email:
            addresses.append(addr)
    return addresses


def _parse_date(value: str) -> datetime | None:
    """Parse an email Date header into a timezone-aware datetime."""
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


# -- aiosmtpd handler ---------------------------------------------------------


class InboundHandler:
    """SMTP handler that parses and persists inbound emails."""

    async def handle_DATA(
        self, server: SMTP, session: Session, envelope: Envelope
    ) -> str:
        """Called when a complete email message has been received."""
        try:
            msg = await self._parse_and_persist(envelope)
            if msg:
                logger.info(
                    "Inbound email persisted: %s from %s",
                    msg.get("subject", "(no subject)"),
                    envelope.mail_from,
                )
        except Exception:
            logger.exception("Failed to persist inbound email from %s", envelope.mail_from)

        return "250 OK"

    async def _parse_and_persist(self, envelope: Envelope) -> dict | None:
        """Parse the raw SMTP message and persist to email_messages."""
        global _inbound_account_id
        if not _inbound_account_id:
            logger.warning("No inbound account ID configured - skipping persistence")
            return None

        raw_content = envelope.original_content
        if isinstance(raw_content, str):
            raw_content = raw_content.encode("utf-8")

        try:
            parsed = email_lib.message_from_bytes(
                raw_content, policy=email_lib.policy.default
            )
        except Exception:
            logger.exception("Failed to parse inbound email")
            return None

        # Headers
        subject = _decode_header_value(str(parsed.get("Subject", "(no subject)")))
        from_addr = _parse_address(str(parsed.get("From", envelope.mail_from)))
        to_raw = str(parsed.get("To", "")) or ", ".join(envelope.rcpt_tos)
        to_addrs = _parse_address_list(to_raw)
        cc_addrs = _parse_address_list(str(parsed.get("Cc", "")))
        bcc_addrs = _parse_address_list(str(parsed.get("Bcc", "")))
        thread_id = str(parsed.get("Message-ID", "")).strip() or None
        in_reply_to = str(parsed.get("In-Reply-To", "")).strip() or None
        # The server arrival time, never the Date header: a sender writes the
        # Date header, and the held card trusts the first received mail of a
        # thread (``held_card.trusted_thread_senders``, §21.12).
        received_at = datetime.now(timezone.utc)

        # Body + attachments
        body_text = ""
        body_html = None
        attachments: list[Attachment] = []

        if parsed.is_multipart():
            att_idx = 0
            for part in parsed.walk():
                content_type = part.get_content_type()
                disposition = str(part.get("Content-Disposition", ""))

                if "attachment" in disposition or (
                    content_type not in ("text/plain", "text/html")
                    and "inline" not in disposition
                    and part.get_filename()
                ):
                    filename = part.get_filename() or f"attachment-{att_idx}"
                    payload = part.get_payload(decode=True)
                    size = len(payload) if payload else 0
                    mime = part.get_content_type()
                    att_idx += 1
                    attachments.append(
                        Attachment(
                            id=f"inbound_{uuid4().hex[:12]}_{att_idx}",
                            filename=filename,
                            mime_type=mime,
                            size_bytes=size,
                            provider_attachment_id=str(att_idx),
                        )
                    )
                elif content_type == "text/plain" and "attachment" not in disposition:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        try:
                            body_text = payload.decode(charset, errors="replace")
                        except Exception:
                            body_text = payload.decode("utf-8", errors="replace")
                elif content_type == "text/html" and "attachment" not in disposition:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        try:
                            body_html = payload.decode(charset, errors="replace")
                        except Exception:
                            body_html = payload.decode("utf-8", errors="replace")
        else:
            content_type = parsed.get_content_type()
            payload = parsed.get_payload(decode=True)
            if payload:
                charset = parsed.get_content_charset() or "utf-8"
                try:
                    decoded = payload.decode(charset, errors="replace")
                except Exception:
                    decoded = payload.decode("utf-8", errors="replace")
                if content_type == "text/html":
                    body_html = decoded
                else:
                    body_text = decoded

        # Snippet
        snippet = body_text[:200] if body_text else (body_html[:200] if body_html else "")

        # Build EmailMessage
        email_msg = EmailMessage(
            provider_message_id=f"inbound_{uuid4().hex}",
            thread_id=thread_id or in_reply_to,
            folder="INBOX",
            labels=[],
            from_address=from_addr,
            to_addresses=to_addrs,
            cc_addresses=cc_addrs,
            bcc_addresses=bcc_addrs,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            snippet=snippet,
            has_attachments=len(attachments) > 0,
            attachments=attachments,
            is_read=False,
            is_starred=False,
            is_flagged=False,
            received_at=received_at,
            raw={},
        )

        # Persist to DB
        await _persist_message(email_msg)
        return {"subject": subject, "from": envelope.mail_from}


# -- DB persistence -----------------------------------------------------------


async def _persist_message(msg: EmailMessage) -> None:
    """Insert the parsed EmailMessage into the email_messages table."""
    global _inbound_account_id
    if not _inbound_account_id:
        return

    db_url = _get_db_url()
    if not db_url:
        logger.warning("No DATABASE_URL configured - skipping persistence")
        return

    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(
            db_url, echo=False, connect_args=_connect_args()
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as db:
            # ONE shared ingest upsert (message + attachments). Inbound mail is
            # insert-only — the sync paths reconcile any later edits — so pass
            # on_conflict="nothing". See email_ingestion.persist.upsert_message.
            await upsert_message(
                db, _inbound_account_id, msg, on_conflict="nothing"
            )

            await db.commit()

        await engine.dispose()

    except Exception:
        logger.exception("Failed to persist inbound message %s", msg.provider_message_id)


def _get_db_url() -> str | None:
    """Get the asyncpg database URL from environment."""
    import os

    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        return db_url

    # Fallback: construct from settings
    try:
        from acb_common.settings import get_settings

        settings = get_settings()
        return (
            f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
            f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
        )
    except Exception:
        return None


def _connect_timeout() -> int:
    """Seconds to bound the asyncpg CONNECT phase so a slow/unreachable DB
    fails fast instead of stalling inbound persistence (settings.db_connect_timeout)."""
    try:
        from acb_common.settings import get_settings
        return get_settings().db_connect_timeout
    except Exception:
        return 10


def _connect_args() -> dict:
    """Connect args for this module's engines.

    :func:`_connect_timeout` bounds getting IN; the server-side
    ``idle_in_transaction_session_timeout`` bounds staying in, so a wedged
    persist cannot hold its locks for the life of the process. Mirrors
    ``scheduler._connect_args`` and ``gateway.db.engine_connect_args`` — see the
    2026-08-06 write-up for why an unbounded one took the app down."""
    return {
        "timeout": _connect_timeout(),
        "server_settings": {
            "idle_in_transaction_session_timeout": "600000",  # 10 min
        },
    }


# -- Lifecycle management -----------------------------------------------------


async def start_inbound_server(
    host: str | None = None,
    port: int | None = None,
    account_id: str | None = None,
) -> Controller:
    """Start the aiosmtpd inbound email server.

    Args:
        host: Bind address (default: EMAIL_INBOUND_HOST env or "127.0.0.1").
        port: SMTP port (default: EMAIL_INBOUND_PORT env or 10025).
        account_id: UUID of the email_accounts row to file inbound mail under
                    (default: EMAIL_INBOUND_ACCOUNT_ID env).

    Returns:
        The running Controller instance.
    """
    import os

    global _controller, _inbound_account_id

    if _controller is not None:
        logger.warning("Inbound server already running")
        return _controller

    if host is None:
        host = os.environ.get("EMAIL_INBOUND_HOST", "127.0.0.1")
    if port is None:
        port = int(os.environ.get("EMAIL_INBOUND_PORT", "10025"))
    if account_id is None:
        account_id = os.environ.get("EMAIL_INBOUND_ACCOUNT_ID", "")
    if not account_id:
        raise ValueError(
            "EMAIL_INBOUND_ACCOUNT_ID env var or account_id parameter is required"
        )

    _inbound_account_id = account_id

    handler = InboundHandler()
    _controller = Controller(
        handler,
        hostname=host,
        port=port,
        ready_timeout=10.0,
    )
    _controller.start()
    logger.info("Inbound SMTP server started on %s:%s (account %s)", host, port, account_id)
    return _controller


async def stop_inbound_server() -> None:
    """Stop the aiosmtpd inbound email server gracefully."""
    global _controller

    if _controller is None:
        return

    _controller.stop()
    _controller = None
    logger.info("Inbound SMTP server stopped")


def get_inbound_server() -> Controller | None:
    """Return the current inbound server controller, or None if not running."""
    return _controller
