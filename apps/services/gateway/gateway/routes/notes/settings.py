"""Note Taker settings — one row per user, one endpoint.

The same shape the task manager and email apps use: everything configurable
about the app lives in a single settings row, read and written whole, so the UI
is one form rather than a scatter of one-off modals.

What lives here and why:

- **Copilot behaviour.** Standing instructions (prepended to every run), whether
  it starts on, and how readily it interrupts.
- **Per-meeting-type instructions.** What deserves an interruption in a sales
  call (a dropped objection) is not what deserves one in a 1:1 (an unspoken
  concern) — a sales nudge in a 1:1 is actively wrong. Sensible defaults ship in
  ``templates.py``; only *overrides* are stored, so improved defaults still reach
  anyone who hasn't customised that type.
- **Defaults for new meetings** — notes template, and the name the bot joins
  under (consent-relevant: people should be able to tell who is recording them).

Sensitivity is three named levels rather than raw numbers because the underlying
controls (minimum gap between interjections, per-meeting cap) invite nonsense
combinations when exposed directly.
"""

from __future__ import annotations

import json
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.notes.core import _get_db, _log, _tenant_session, router
from gateway.routes.notes.templates import TEMPLATES
from pydantic import BaseModel
from sqlalchemy import text

_SENSITIVITIES = ("low", "normal", "high")

# (min seconds between interjections, max per meeting). "low" means a copilot
# that rarely speaks up; "high" one that chimes in readily.
SENSITIVITY_LEVELS: dict[str, tuple[float, int]] = {
    "low": (120.0, 5),
    "normal": (45.0, 12),
    "high": (20.0, 25),
}

_MAX_INSTRUCTIONS = 2000


_LIVE_MODES = ("auto", "always", "never")


def wants_live_transcription(mode: str, copilot_on: bool) -> bool:
    """Whether to pay for streaming ASR on this meeting.

    Streaming speech-to-text is billed per minute and runs the whole meeting.
    With the copilot off nothing reads it in real time — the recording still
    goes through the batch pipeline afterwards and yields the same transcript at
    batch prices. So `auto` follows the copilot, which is where the saving is.

    Pure, and defensive about an unknown mode: an unrecognised value falls back
    to `auto` rather than to `always`, because the failure that silently spends
    money is worse than the one that silently doesn't.
    """
    if mode == "always":
        return True
    if mode == "never":
        return False
    return bool(copilot_on)


class NotesSettings(BaseModel):
    copilot_instructions: str = ""
    copilot_default_on: bool = False
    copilot_sensitivity: str = "normal"
    #: auto | always | never — see wants_live_transcription().
    live_transcription: str = "auto"
    # template key → instructions. Only user overrides; the effective value
    # falls back to the template's own default.
    template_instructions: dict[str, str] = {}
    default_template: str | None = None
    bot_name: str | None = None
    # Auto-dispatch of confident action items after notes generation (per
    # kind). Emails are additionally recipient-gated at dispatch time: an
    # unresolvable recipient always downgrades a send to a provider draft.
    auto_dispatch_tasks: bool = True
    auto_dispatch_emails: bool = True
    auto_dispatch_docs: bool = True


class TemplateInfo(BaseModel):
    key: str
    label: str
    #: The default copilot guidance shipped for this meeting type.
    copilot_default: str = ""
    agenda_hint: str = ""


def template_catalogue() -> list[TemplateInfo]:
    """Every meeting type with its shipped copilot guidance, so the settings UI
    can show what it would do before you override it."""
    return [
        TemplateInfo(
            key=t.key, label=t.label,
            copilot_default=getattr(t, "copilot", ""),
            agenda_hint=getattr(t, "agenda_hint", ""),
        )
        for t in TEMPLATES.values()
    ]


def effective_instructions(
    settings: NotesSettings, template_key: str | None
) -> str:
    """The instruction block for a run: the user's standing instructions plus
    whatever applies to THIS meeting type.

    Pure, so the precedence is testable: a user override for the type wins over
    the shipped default; both are additive to the standing instructions rather
    than replacing them (they answer different questions — 'who am I' vs 'what
    matters in this kind of meeting')."""
    parts: list[str] = []
    if settings.copilot_instructions.strip():
        parts.append(settings.copilot_instructions.strip())
    key = (template_key or "").strip()
    if key:
        override = (settings.template_instructions or {}).get(key, "").strip()
        if override:
            parts.append(override)
        else:
            tpl = TEMPLATES.get(key)
            shipped = getattr(tpl, "copilot", "") if tpl else ""
            if shipped:
                parts.append(shipped)
    return "\n".join(parts)


def copilot_should_run(
    per_meeting: bool | None, account_default: bool
) -> bool:
    """Whether the copilot starts for this meeting.

    Three states, not two: a meeting can say *on*, say *off*, or say nothing.
    Only the last defers to the account default — otherwise turning the copilot
    off for one sensitive meeting would be silently undone by the global
    setting, which is the opposite of what "off" means. Pure, so the precedence
    is testable without a session.
    """
    return account_default if per_meeting is None else per_meeting


def _row_to_settings(row) -> NotesSettings:
    raw = row.template_instructions
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = {}
    return NotesSettings(
        copilot_instructions=(row.instructions or ""),
        copilot_default_on=bool(row.copilot_default_on),
        copilot_sensitivity=row.copilot_sensitivity or "normal",
        template_instructions={
            str(k): str(v) for k, v in (raw or {}).items() if v
        },
        default_template=row.default_template,
        bot_name=row.bot_name,
        live_transcription=getattr(row, "live_transcription", None) or "auto",
        auto_dispatch_tasks=bool(getattr(row, "auto_dispatch_tasks", True)),
        auto_dispatch_emails=bool(getattr(row, "auto_dispatch_emails", True)),
        auto_dispatch_docs=bool(getattr(row, "auto_dispatch_docs", True)),
    )


_SETTINGS_SQL = "SELECT * FROM copilot_config WHERE owner_email = :e"
_MEETING_CFG_SQL = (
    "SELECT owner_email, template_key FROM meeting WHERE id = CAST(:id AS UUID)"
)


async def load(owner_email: str, db: Any = None) -> NotesSettings:
    """Settings for a user — defaults when they've never saved any. Never
    raises: a settings read must not be able to break a meeting.

    A request passes its tenant-bound session ``db`` (task 3966). With no
    ``db`` it reads as before."""
    if not owner_email:
        return NotesSettings()
    try:
        if db is not None:
            row = (await db.execute(text(_SETTINGS_SQL), {"e": owner_email})).fetchone()
            return _row_to_settings(row) if row is not None else NotesSettings()
        # H4: shared helper also consumed by background paths (`auto_dispatch`
        # from the summary pipeline task, `load_for_meeting` below) — no
        # ambient tenant there; derive it from the owner's app_user/meeting row.
        async with await _get_db() as db:
            row = (
                await db.execute(text(_SETTINGS_SQL), {"e": owner_email})
            ).fetchone()
        return _row_to_settings(row) if row is not None else NotesSettings()
    except Exception as exc:
        _log.warning("notes.settings_load_failed", error=str(exc)[:200])
        return NotesSettings()


async def load_for_meeting(
    meeting_id: str, db: Any = None
) -> tuple[NotesSettings, str | None]:
    """Settings of whoever owns this meeting, plus its template key.

    A request passes its tenant-bound session ``db`` (task 3966). With no
    ``db`` it reads as before."""
    try:
        if db is not None:
            row = (
                await db.execute(text(_MEETING_CFG_SQL), {"id": meeting_id})
            ).fetchone()
        else:
            # H4: consumed by the copilot orchestrator task and the
            # meeting-bot worker's `/live/wanted` (service identity) — no
            # ambient tenant; derive it from the meeting row.
            async with await _get_db() as unbound:
                row = (
                    await unbound.execute(text(_MEETING_CFG_SQL), {"id": meeting_id})
                ).fetchone()
        if row is None:
            return NotesSettings(), None
        return await load(row.owner_email or "", db), row.template_key
    except Exception:
        return NotesSettings(), None


# ── API ─────────────────────────────────────────────────────────────────────

@router.get("/settings")
async def get_settings(
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Everything configurable about the Note Taker, plus the meeting-type
    catalogue so the UI can show shipped defaults next to any overrides.
    The read runs in the bound session of this request (task 3966)."""
    async with _tenant_session() as db:
        settings = await load((getattr(user, "email", "") or "").strip(), db)
    return {
        "settings": settings.model_dump(),
        "templates": [t.model_dump() for t in template_catalogue()],
        "sensitivities": list(_SENSITIVITIES),
    }


@router.put("/settings")
async def put_settings(
    body: NotesSettings,
    user: UserContext = Depends(get_current_user),
) -> dict:
    email = (getattr(user, "email", "") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="no user")
    if body.copilot_sensitivity not in _SENSITIVITIES:
        raise HTTPException(
            status_code=400, detail=f"sensitivity must be one of {_SENSITIVITIES}"
        )
    if body.live_transcription not in _LIVE_MODES:
        raise HTTPException(
            status_code=400, detail=f"live_transcription must be one of {_LIVE_MODES}"
        )
    if body.default_template and body.default_template not in TEMPLATES:
        raise HTTPException(status_code=400, detail="unknown template")
    # Keep only overrides for templates we actually ship, trimmed.
    overrides = {
        k: v.strip()[:_MAX_INSTRUCTIONS]
        for k, v in (body.template_instructions or {}).items()
        if k in TEMPLATES and (v or "").strip()
    }
    async with _tenant_session() as db:
        await db.execute(
            text(
                "INSERT INTO copilot_config (owner_email, instructions, "
                "copilot_default_on, copilot_sensitivity, template_instructions, "
                "default_template, bot_name, live_transcription, "
                "auto_dispatch_tasks, auto_dispatch_emails, auto_dispatch_docs) "
                "VALUES (:e, :i, :d, :s, CAST(:t AS JSONB), :tpl, :bn, :lt, "
                ":adt, :ade, :add) "
                "ON CONFLICT (organization_id, owner_email) DO UPDATE SET "
                "instructions = EXCLUDED.instructions, "
                "copilot_default_on = EXCLUDED.copilot_default_on, "
                "copilot_sensitivity = EXCLUDED.copilot_sensitivity, "
                "template_instructions = EXCLUDED.template_instructions, "
                "default_template = EXCLUDED.default_template, "
                "bot_name = EXCLUDED.bot_name, "
                "live_transcription = EXCLUDED.live_transcription, "
                "auto_dispatch_tasks = EXCLUDED.auto_dispatch_tasks, "
                "auto_dispatch_emails = EXCLUDED.auto_dispatch_emails, "
                "auto_dispatch_docs = EXCLUDED.auto_dispatch_docs, "
                "updated_at = now()"
            ),
            {
                "e": email,
                "i": body.copilot_instructions.strip()[:_MAX_INSTRUCTIONS] or None,
                "d": body.copilot_default_on,
                "s": body.copilot_sensitivity,
                "t": json.dumps(overrides),
                "tpl": body.default_template,
                "bn": (body.bot_name or "").strip() or None,
                "lt": body.live_transcription,
                "adt": body.auto_dispatch_tasks,
                "ade": body.auto_dispatch_emails,
                "add": body.auto_dispatch_docs,
            },
        )
        saved = await load(email, db)
    _log.info("notes.settings_saved", overrides=len(overrides))
    return {"settings": saved.model_dump()}
