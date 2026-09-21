"""Tasks · capability — semantic assignee matching (spec §5, Phase 2).

Embeds each person's capability text (role · title · skills · domain · résumé
summary) into ``people.capability_embedding`` and scores a task against the
roster by cosine similarity, so the clarify/assignment engine can rank owners by
SEMANTIC fit — not just keyword overlap.

ADDITIVE + flag-gated (``task_semantic_match_enabled``, default OFF). With the
flag off every function here is a no-op and the deterministic keyword matcher
(``ai._match_capability``) stays the sole path — the golden-eval baseline is
untouched. With it on, semantic scores only *re-rank / augment* the candidate
people fed to the LLM; they never DROP a keyword candidate (same posture as the
email hybrid search — see [[email-search-hybrid]]).

Embedding is synchronous + best-effort on each person write (the roster is tiny,
dozens of rows), plus a ``POST /tasks/people/embed`` backfill for existing rows.
``capability_text_hash`` skips re-embedding a person whose derived text is
unchanged (embeddings cost tokens). Any failure degrades to keyword matching.
"""

from __future__ import annotations

import hashlib
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import Depends
from gateway.routes.tasks.core import _tenant_session, require_people_write, router
from sqlalchemy import text

_log = get_logger("gateway.tasks.capability")

# Cap the capability text — a résumé summary can be long; the gist is in the
# role + skills + the summary head. Keeps the embed request small.
_MAX_CHARS = 4000


def _semantic_enabled() -> bool:
    try:
        from acb_common import get_settings
        return bool(get_settings().task_semantic_match_enabled)
    except Exception:
        return False


def _embedding_model() -> str:
    try:
        from acb_common import get_settings
        return get_settings().email_embedding_model  # one embedder for the app
    except Exception:
        return "text-embedding-3-small"


def capability_text(p: dict[str, Any]) -> str:
    """The text a person's capability_embedding is built from. One definition so
    the stored embedding and a re-embed can never diverge. Kept human-readable so
    the same string is a decent thing to embed for the *task* side too."""
    bits: list[str] = []
    if p.get("role"):
        bits.append(str(p["role"]))
    if p.get("title"):
        bits.append(str(p["title"]))
    dom = (p.get("domain") or "").strip()
    if dom and dom.lower() != "unknown":
        bits.append(f"domain: {dom}")
    skills = [s for s in (p.get("skills") or []) if s]
    if skills:
        bits.append("skills: " + ", ".join(str(s) for s in skills))
    summ = (p.get("resume_summary") or "").strip()
    if summ:
        bits.append(summ)
    return " · ".join(bits)[:_MAX_CHARS]


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()


async def _embed(texts: list[str]) -> list[list[float]] | None:
    """Embed a batch through the LiteLLM gateway (same /v1/embeddings path the
    email embeddings + mem0 use — no OPENAI_API_KEY needed). None on any failure
    so every caller degrades to keyword matching."""
    if not texts:
        return None
    try:
        import litellm  # noqa: PLC0415
        from acb_common.settings import get_settings as _gs  # noqa: PLC0415
        litellm.drop_params = True
        settings = _gs()
        resp = await litellm.aembedding(
            model=_embedding_model(),
            input=texts,
            api_base=settings.litellm_base_url.rstrip("/") + "/v1",
            api_key=settings.litellm_master_key,
            custom_llm_provider="openai",
        )
        data = resp["data"] if isinstance(resp, dict) else resp.data
        return [d["embedding"] if isinstance(d, dict) else d.embedding
                for d in data]
    except Exception as exc:  # noqa: BLE001
        _log.warning("tasks.capability.embed_failed", error=str(exc)[:160])
        return None


def _vec_literal(vec: list[float]) -> str:
    """pgvector accepts the '[..]' text form for a vector literal."""
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


async def embed_person(db: Any, person_id: str) -> bool:
    """Embed ONE person's capability text (best-effort, hash-gated). Called after
    a create/patch/résumé write so the vector tracks the row. No-op (returns
    False) when semantic matching is off, the text is empty, or the hash is
    unchanged. Never raises — the caller's write already committed."""
    if not _semantic_enabled():
        return False
    try:
        row = (await db.execute(text(
            """SELECT role, title, domain, skills, resume_summary,
                      capability_text_hash
                 FROM people WHERE id = :id"""), {"id": person_id})).fetchone()
        if row is None:
            return False
        ctext = capability_text({
            "role": row.role, "title": getattr(row, "title", None),
            "domain": row.domain, "skills": list(row.skills or []),
            "resume_summary": row.resume_summary,
        })
        if not ctext.strip():
            return False
        h = _hash(ctext)
        if getattr(row, "capability_text_hash", None) == h:
            return False  # derived text unchanged → keep the existing vector
        vecs = await _embed([ctext])
        if not vecs:
            return False
        await db.execute(text(
            """UPDATE people
                  SET capability_embedding = CAST(:emb AS vector),
                      capability_text_hash = :hash
                WHERE id = :id"""),
            {"emb": _vec_literal(vecs[0]), "hash": h, "id": person_id})
        await db.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        _log.warning("tasks.capability.embed_person_failed",
                     error=str(exc)[:160])
        return False


async def embed_pending_people(db: Any, *, batch: int = 64) -> int:
    """Backfill: embed active people whose capability text changed / was never
    embedded. Returns how many were embedded. No-op when the flag is off."""
    if not _semantic_enabled():
        return 0
    rows = (await db.execute(text(
        """SELECT id, role, title, domain, skills, resume_summary,
                  capability_text_hash
             FROM people
            WHERE status = 'active'
            ORDER BY updated_at DESC NULLS LAST
            LIMIT :lim"""), {"lim": batch})).fetchall()
    embedded = 0
    pending: list[tuple[str, str, str]] = []  # (id, ctext, hash)
    for r in rows:
        ctext = capability_text({
            "role": r.role, "title": getattr(r, "title", None),
            "domain": r.domain, "skills": list(r.skills or []),
            "resume_summary": r.resume_summary,
        })
        if not ctext.strip():
            continue
        h = _hash(ctext)
        if getattr(r, "capability_text_hash", None) == h:
            continue
        pending.append((str(r.id), ctext, h))
    if not pending:
        return 0
    vecs = await _embed([c for _, c, _ in pending])
    if not vecs or len(vecs) != len(pending):
        return 0
    for (pid, _c, h), vec in zip(pending, vecs, strict=True):
        await db.execute(text(
            """UPDATE people
                  SET capability_embedding = CAST(:emb AS vector),
                      capability_text_hash = :hash
                WHERE id = :id"""),
            {"emb": _vec_literal(vec), "hash": h, "id": pid})
        embedded += 1
    if embedded:
        await db.commit()
    return embedded


async def semantic_scores(db: Any, task_text: str) -> dict[str, float]:
    """Cosine similarity of a task against every active person with an embedding.
    Returns {lowercased_name: score in [0,1]} — empty when the flag is off, the
    task text is empty, nobody is embedded, or the embed fails (→ caller keeps
    the keyword order). Names are the join key the clarify people-briefs use."""
    if not _semantic_enabled() or not (task_text or "").strip():
        return {}
    try:
        vecs = await _embed([task_text.strip()[:_MAX_CHARS]])
        if not vecs:
            return {}
        rows = (await db.execute(text(
            """SELECT name,
                      1 - (capability_embedding <=> CAST(:qvec AS vector)) AS sim
                 FROM people
                WHERE status = 'active'
                  AND capability_embedding IS NOT NULL"""),
            {"qvec": _vec_literal(vecs[0])})).fetchall()
        out: dict[str, float] = {}
        for r in rows:
            name = (r.name or "").strip().lower()
            if name:
                out[name] = float(r.sim)
        return out
    except Exception as exc:  # noqa: BLE001
        _log.warning("tasks.capability.scores_failed", error=str(exc)[:160])
        return {}


@router.post("/people/embed", dependencies=[require_people_write()])
async def backfill_people_embeddings(
    _user: UserContext = Depends(get_current_user),
):
    """Embed the roster's capability vectors (idempotent, hash-gated). No-op with
    a clear message when semantic matching is disabled. Run after connecting HR
    data or turning the flag on.

    Admin-only (`admin:members:manage`) — it is the fourth write route on the
    people directory (N4), and it reads every person's résumé summary and
    skills to build the vectors."""
    if not _semantic_enabled():
        return {"enabled": False, "embedded": 0,
                "detail": "task_semantic_match_enabled is off."}
    # `embed_pending_people` commits as its LAST database action, so it may be
    # the sole occupant of this tenant block (H2): every statement runs under
    # the GUC and the wrapper's exit commit is an empty no-op.
    async with _tenant_session() as db:
        n = await embed_pending_people(db)
        return {"enabled": True, "embedded": n}
