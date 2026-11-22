"""Org glossary — the user's jargon, biased into transcription.

Terms (product names, people, customers, acronyms) are injected into the STT
prompt so the engine spells them right — fixing the most common transcript
errors before they propagate into notes/actions/search (spec §4 Tier-1 item 6).
The pipeline calls ``glossary_prompt(owner_email)`` at transcription time.
"""

from __future__ import annotations

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.notes.core import _get_db, _log, _tenant_session, router
from pydantic import BaseModel
from sqlalchemy import text

# Keep the injected prompt bounded — whisper's prompt window is small and a
# huge term list dilutes the bias. Cap terms and total chars.
_MAX_TERMS = 200
_MAX_PROMPT_CHARS = 900


class GlossaryTerm(BaseModel):
    id: str
    term: str


class AddTermRequest(BaseModel):
    term: str


@router.get("/glossary")
async def list_glossary(
    user: UserContext = Depends(get_current_user),
) -> list[GlossaryTerm]:
    async with _tenant_session() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT id, term FROM notes_glossary WHERE user_id=:u "
                    "ORDER BY lower(term)"
                ),
                {"u": user.email or "anonymous"},
            )
        ).fetchall()
    return [GlossaryTerm(id=str(r.id), term=r.term) for r in rows]


@router.post("/glossary", status_code=201)
async def add_term(
    body: AddTermRequest,
    user: UserContext = Depends(get_current_user),
) -> GlossaryTerm:
    term = body.term.strip()
    if not term:
        raise HTTPException(status_code=400, detail="empty term")
    if len(term) > 120:
        raise HTTPException(status_code=400, detail="term too long")
    async with _tenant_session() as db:
        # Idempotent per (user, case-folded term): return the existing row.
        row = (
            await db.execute(
                text(
                    "INSERT INTO notes_glossary (user_id, term) VALUES (:u, :t) "
                    "ON CONFLICT (user_id, lower(term)) DO UPDATE SET term=EXCLUDED.term "
                    "RETURNING id, term"
                ),
                {"u": user.email or "anonymous", "t": term},
            )
        ).fetchone()
    _log.info("notes.glossary_add", user=user.email, term=term)
    return GlossaryTerm(id=str(row.id), term=row.term)


@router.delete("/glossary/{term_id}", status_code=204)
async def delete_term(
    term_id: str,
    user: UserContext = Depends(get_current_user),
) -> None:
    async with _tenant_session() as db:
        await db.execute(
            text("DELETE FROM notes_glossary WHERE id=:id AND user_id=:u"),
            {"id": term_id, "u": user.email or "anonymous"},
        )


def format_glossary_prompt(terms: list[str]) -> str:
    """Build the STT bias prompt from a term list (bounded). Empty → ''.

    Phrased as a hint sentence so whisper treats the terms as vocabulary context
    rather than transcribable content."""
    clean = [t.strip() for t in terms if t and t.strip()][:_MAX_TERMS]
    if not clean:
        return ""
    joined = ", ".join(clean)
    if len(joined) > _MAX_PROMPT_CHARS:
        joined = joined[:_MAX_PROMPT_CHARS].rsplit(",", 1)[0]
    return f"Glossary of terms that may be used: {joined}."


async def glossary_prompt(user_id: str) -> str:
    """Load a user's glossary and format it for the STT prompt. Best-effort —
    returns '' if unavailable so transcription never blocks on it."""
    if not user_id:
        return ""
    try:
        # H4: called from the background transcription pipeline
        # (`pipeline.run_transcription`, a spawned task). A browser upload's
        # task sees the member's bound tenant (`create_task` copies the
        # context). The meeting-bot ingest's task has none. Derive it from
        # the meeting row's owner.
        async with await _get_db() as db:
            rows = (
                await db.execute(
                    text("SELECT term FROM notes_glossary WHERE user_id=:u"),
                    {"u": user_id},
                )
            ).fetchall()
        return format_glossary_prompt([r.term for r in rows])
    except Exception as exc:
        _log.warning("notes.glossary_prompt_failed", error=str(exc)[:200])
        return ""
