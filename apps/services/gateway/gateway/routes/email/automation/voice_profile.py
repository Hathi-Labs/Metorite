"""Automation · voice profile — a writing-style profile + knowledge suggestions
learned from the account's past sent/drafted mail over a user-chosen date range.

The user picks the source folders ('sent' and/or 'drafts') and a date range in
AI Settings → Advanced Settings → "Voice profile"; a background job then reads
the matching messages (quoted chains stripped, so only the owner's own prose is
studied), extracts style observations batch-by-batch, and synthesizes them into
one profile: structured traits (JSONB, shown as chips in the UI) plus a
narrative style guide (the prompt-facing text). The same pass can propose
knowledge-base entries — recurring facts found in the mail — which land as
status='suggested' rows in email_knowledge and only feed drafting once the user
approves them.

Prompt integration: `voice_profile_block` renders the profile as a
<voice_profile> tagged block; `_load_assistant_about` (assistant.py) places it
between the explicit <writing_style> (user-authored, outranks it) and the
auto-derived <learned_writing_style> (advisory).
"""

from __future__ import annotations

import json
from typing import Any

from acb_auth import UserContext, get_current_user
from email_ingestion.llm_cap import automation_job, llm_slot
from fastapi import BackgroundTasks, Depends, HTTPException, Query, status
from gateway.routes.email.automation.jobs import JobTracker
from gateway.routes.email.core import (
    _assert_account_owner,
    _tenant_session,
    _llm_json,
    _log,
    _parse_iso_date,
    router,
)
from gateway.routes.email.quoting import split_quoted_text
from pydantic import BaseModel, Field
from sqlalchemy import text

# One build at a time per account, with live progress the dialog polls
# (GET /voice-profile/status) — same in-memory tracker the rule backfill uses.
_VOICE_JOBS = JobTracker()

_SAMPLE_CAP = 150        # most-recent messages read from the range
_SAMPLE_MIN_CHARS = 40   # below this a message teaches nothing about style
_SAMPLE_MAX_CHARS = 1200
_BATCH_SIZE = 12         # samples per extraction call
_MAX_KB_SUGGESTIONS = 15
_VALID_SOURCES = ("sent", "drafts")

# Trait keys the profile stores — anything else the model returns is dropped.
# Scalars are short strings; lists are short string lists.
_SCALAR_TRAITS = (
    "tone", "formality", "typical_length", "sentence_style", "emoji_usage",
    "formatting_habits",
)
_LIST_TRAITS = (
    "greetings", "signoffs", "common_phrases", "languages", "dos", "donts",
)


def _clean_sources(raw: list[str] | None) -> list[str]:
    """Validate the requested source folders; default to sent-only (drafts are
    opt-in because synced provider drafts can include assistant-written ones)."""
    out = [s for s in (raw or []) if s in _VALID_SOURCES]
    return out or ["sent"]


def _prep_samples(bodies: list[str]) -> list[str]:
    """Strip each message down to the owner's own prose: drop the quoted chain
    (a reply's tail is the correspondent's writing, not the owner's), trim, and
    keep only samples long enough to carry style signal."""
    out: list[str] = []
    for body in bodies:
        own = split_quoted_text((body or "").strip())[0].strip()
        if len(own) >= _SAMPLE_MIN_CHARS:
            out.append(own[:_SAMPLE_MAX_CHARS])
    return out


def _dedupe_keep_order(items: list[str], cap: int) -> list[str]:
    """Case-insensitively dedupe short strings, preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        s = (it or "").strip()
        key = s.lower()
        if not s or key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= cap:
            break
    return out


def _merge_observations(batches: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Merge the per-batch observation lists into one deduped set for the final
    synthesis call."""
    merged: dict[str, list[str]] = {
        "style_notes": [], "greetings": [], "signoffs": [], "phrases": [],
    }
    facts: list[dict[str, str]] = []
    for b in batches:
        if not isinstance(b, dict):
            continue
        for key in merged:
            vals = b.get(key)
            if isinstance(vals, list):
                merged[key].extend(str(v) for v in vals if v)
        for f in (b.get("facts") or []):
            if isinstance(f, dict) and (f.get("title") or "").strip() \
                    and (f.get("content") or "").strip():
                facts.append({"title": str(f["title"]).strip()[:120],
                              "content": str(f["content"]).strip()[:1000]})
    return {
        "style_notes": _dedupe_keep_order(merged["style_notes"], 60),
        "greetings": _dedupe_keep_order(merged["greetings"], 12),
        "signoffs": _dedupe_keep_order(merged["signoffs"], 12),
        "phrases": _dedupe_keep_order(merged["phrases"], 25),
        "facts": _dedupe_facts(facts),
    }


def _dedupe_facts(facts: list[dict[str, str]]) -> list[dict[str, str]]:
    """Dedupe candidate knowledge entries by title (case-insensitive)."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for f in facts:
        key = f["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def _normalize_traits(raw: Any) -> dict[str, Any]:
    """Whitelist + type-coerce the model's trait object so only known keys with
    sane shapes reach the JSONB column (and later the prompt)."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key in _SCALAR_TRAITS:
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            out[key] = v.strip()[:200]
    for key in _LIST_TRAITS:
        v = raw.get(key)
        if isinstance(v, list):
            vals = _dedupe_keep_order(
                [x for x in v if isinstance(x, str)], 12)
            if vals:
                out[key] = [x[:160] for x in vals]
    return out


def _parse_traits(val: Any) -> dict[str, Any]:
    """A JSONB traits value → dict. asyncpg may hand JSONB back as a str (the
    codebase's address columns get the same both-ways treatment)."""
    if isinstance(val, dict):
        return val
    if isinstance(val, str) and val.strip():
        try:
            data = json.loads(val)
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def voice_profile_block(style_guide: str, traits: Any) -> str:
    """Render a READY profile as the <voice_profile> prompt block. The guide is
    the narrative core; the list traits that most shape a draft's shell
    (greetings / sign-offs / stock phrases) are appended as concrete examples."""
    guide = (style_guide or "").strip() if isinstance(style_guide, str) else ""
    t = _parse_traits(traits)
    lines: list[str] = []
    if guide:
        lines.append(guide)
    for key, label in (("greetings", "Typical greetings"),
                       ("signoffs", "Typical sign-offs"),
                       ("common_phrases", "Phrases the user actually uses")):
        vals = t.get(key)
        if isinstance(vals, list):
            vals = [v for v in vals if isinstance(v, str) and v.strip()]
            if vals:
                lines.append(f"{label}: " + "; ".join(vals[:8]))
    if not lines:
        return ""
    return "<voice_profile>\n" + "\n".join(lines) + "\n</voice_profile>"


async def load_voice_profile_block(db: Any, account_id: str) -> str:
    """The <voice_profile> block for an account, or "" when there is none,
    it's disabled, or the table doesn't exist yet (best-effort by design —
    drafting must never fail because the profile can't be read)."""
    try:
        row = (await db.execute(text(
            """SELECT style_guide, traits FROM email_voice_profiles
               WHERE account_id = :aid AND enabled AND status = 'READY'"""
        ), {"aid": account_id})).fetchone()
        if not row:
            return ""
        sg = row.style_guide if isinstance(row.style_guide, str) else ""
        return voice_profile_block(sg, row.traits)
    except Exception as exc:
        _log.warning("email.voice_profile_load_failed", error=str(exc)[:160])
        return ""


# ── LLM passes ──────────────────────────────────────────────────────────────


async def _llm_observe_batch(
    samples: list[str], extract_knowledge: bool,
) -> dict[str, Any]:
    """One extraction call over a batch of samples → raw observations (+
    candidate knowledge facts). Runs on the balanced tier: it's a per-batch
    extraction task, and a 150-email build makes ~13 of these calls."""
    kb_rule = (
        '- facts: recurring, durable facts about the user/their work that would '
        'help draft future emails (role, company, products, pricing, policies, '
        'links they share, how they handle common requests). Each as '
        '{"title", "content"}. NEVER include one-off details, secrets, '
        'passwords, or anything about a single specific transaction.\n'
        if extract_knowledge else ""
    )
    sys_prompt = (
        "You study emails WRITTEN BY one user to learn their personal writing "
        "voice. From the samples, extract:\n"
        "- style_notes: short concrete observations about tone, formality, "
        "length, structure, punctuation, emoji use, and habits (e.g. 'Opens "
        "with a one-line thank-you', 'Uses dashes instead of commas').\n"
        "- greetings: greeting lines they actually use, with placeholders for "
        "names (e.g. 'Hi <first name>,').\n"
        "- signoffs: closing lines they actually use.\n"
        "- phrases: distinctive recurring phrases or expressions.\n"
        f"{kb_rule}"
        'Respond with ONLY JSON: {"style_notes": [], "greetings": [], '
        '"signoffs": [], "phrases": [], "facts": []}. Empty lists are fine.'
    )
    joined = "\n\n--- EMAIL ---\n\n".join(samples)
    data, _content, _used = await _llm_json(
        "tier-balanced",
        [{"role": "system", "content": sys_prompt},
         {"role": "user", "content": joined[:16000]}],
        max_tokens=1500,
    )
    return data if isinstance(data, dict) else {}


async def _llm_synthesize_profile(
    merged: dict[str, Any], sample_count: int,
) -> tuple[dict[str, Any], str]:
    """Final synthesis call: merged observations → (structured traits,
    narrative style guide). Runs on the powerful tier — this one output IS the
    profile."""
    sys_prompt = (
        "You distil observations about one user's email writing into their "
        "voice profile. Respond with ONLY JSON:\n"
        '{"traits": {"tone": str, "formality": str, "typical_length": str, '
        '"sentence_style": str, "emoji_usage": str, "formatting_habits": str, '
        '"greetings": [str], "signoffs": [str], "common_phrases": [str], '
        '"languages": [str], "dos": [str], "donts": [str]}, '
        '"style_guide": str}\n'
        "traits: short, concrete values (the UI shows them as chips); keep "
        "only what the observations support — omit keys you have no evidence "
        "for. dos/donts: 2-5 imperative drafting rules each.\n"
        "style_guide: 5-9 bullet lines a ghostwriter could follow to write "
        "exactly like this user (tone, length, structure, greeting/sign-off "
        "habits, distinctive quirks). Phrase each as an instruction, e.g. "
        "'Keep replies to 2-3 short sentences.' No preamble."
    )
    user = (
        f"Observations from {sample_count} emails the user wrote:\n"
        + json.dumps({k: merged[k] for k in
                      ("style_notes", "greetings", "signoffs", "phrases")},
                     ensure_ascii=False)[:14000]
    )
    data, _content, _used = await _llm_json(
        "tier-powerful",
        [{"role": "system", "content": sys_prompt},
         {"role": "user", "content": user}],
        max_tokens=2000, temperature=0.2,
    )
    if not isinstance(data, dict):
        return {}, ""
    traits = _normalize_traits(data.get("traits"))
    guide = data.get("style_guide")
    guide = guide.strip()[:4000] if isinstance(guide, str) else ""
    return traits, guide


# ── The build job ───────────────────────────────────────────────────────────


async def _fetch_sample_bodies(
    db: Any, account_id: str, sources: list[str],
    start: Any, end: Any, cap: int = _SAMPLE_CAP,
) -> list[str]:
    """Most-recent message bodies from the chosen folders within the range."""
    clauses = ["account_id = :aid",
               "LOWER(COALESCE(folder, '')) = ANY(:folders)"]
    params: dict[str, Any] = {"aid": account_id, "folders": list(sources),
                              "cap": cap}
    if start is not None:
        clauses.append("received_at >= :start")
        params["start"] = start
    if end is not None:
        clauses.append("received_at <= :end")
        params["end"] = end
    rows = (await db.execute(text(
        f"""SELECT body_text, snippet FROM email_messages
            WHERE {' AND '.join(clauses)}
            ORDER BY received_at DESC NULLS LAST LIMIT :cap"""
    ), params)).fetchall()
    return [(r.body_text or r.snippet or "") for r in rows]


async def _store_kb_suggestions(
    db: Any, account_id: str, facts: list[dict[str, str]],
) -> int:
    """Insert candidate knowledge entries as status='suggested'. Never clobbers
    an existing entry (manual or approved) with the same title."""
    stored = 0
    for f in facts[:_MAX_KB_SUGGESTIONS]:
        res = await db.execute(text(
            """INSERT INTO email_knowledge
                 (account_id, title, content, source, status)
               VALUES (:aid, :title, :content, 'voice_profile', 'suggested')
               ON CONFLICT (account_id, title) DO NOTHING"""
        ), {"aid": account_id, "title": f["title"], "content": f["content"]})
        stored += int(res.rowcount or 0)
    return stored


@automation_job  # EM-T4b: the cap and the daily budget bind its model calls
async def _build_voice_profile_job(
    account_id: str, sources: list[str], start: Any, end: Any,
    extract_knowledge: bool, token: int,
) -> None:
    """Background: read the range, observe per batch, synthesize, save.

    Progress lands in _VOICE_JOBS (phase + processed/total batches); the
    profile row's status moves BUILDING → READY / FAILED so the state survives
    the tracker (which is in-memory and dies with the process).

    EM-T4a-0 (``email_app_master_plan.md`` §10.4.6): the build route starts
    this as a BackgroundTask, which runs inside the tenant scope of its
    request. The build is one ``_tenant_session()`` block with that ambient
    tenant, and the seam commits it on a clean exit. A failed build rolls
    that block back, and the FAILED row is a block of its own. Neither block
    calls ``commit()``. With no tenant bound, the first block raises
    ``TenantUnbound``. The second block raises it too and writes nothing, and
    the handler records the refusal on the progress row. The model calls stay
    inside the build block, as they did before, and EM-T4a-4 owns that I/O.
    """
    try:
        async with _tenant_session() as db:
            _VOICE_JOBS.update(account_id, token, phase="collecting")
            bodies = await _fetch_sample_bodies(
                db, account_id, sources, start, end)
            samples = _prep_samples(bodies)
            if not samples:
                raise ValueError(
                    "No usable emails in that range — nothing written by you "
                    "was found in the selected folders.")

            batches = [samples[i:i + _BATCH_SIZE]
                       for i in range(0, len(samples), _BATCH_SIZE)]
            _VOICE_JOBS.update(
                account_id, token, phase="analyzing",
                processed=0, total=len(batches), sample_count=len(samples))
            observations: list[dict[str, Any]] = []
            for i, batch in enumerate(batches):
                try:
                    observations.append(
                        await _llm_observe_batch(batch, extract_knowledge))
                except Exception as exc:
                    _log.warning("email.voice_profile_batch_failed",
                                 account_id=account_id, batch=i,
                                 error=str(exc)[:160])
                _VOICE_JOBS.update(account_id, token, processed=i + 1)

            merged = _merge_observations(observations)
            if not any(merged[k] for k in
                       ("style_notes", "greetings", "signoffs", "phrases")):
                raise ValueError("Could not extract any style signal from the "
                                 "selected emails.")

            _VOICE_JOBS.update(account_id, token, phase="synthesizing")
            traits, guide = await _llm_synthesize_profile(merged, len(samples))
            if not guide and not traits:
                raise ValueError("Could not synthesize a style profile.")

            suggested = 0
            if extract_knowledge and merged["facts"]:
                _VOICE_JOBS.update(account_id, token, phase="knowledge")
                suggested = await _store_kb_suggestions(
                    db, account_id, merged["facts"])

            await db.execute(text(
                """UPDATE email_voice_profiles SET
                     status = 'READY', style_guide = :sg, traits = :tr,
                     analyzed_count = :n, last_error = NULL,
                     built_at = now(), updated_at = now()
                   WHERE account_id = :aid"""
            ), {"aid": account_id, "sg": guide, "tr": json.dumps(traits),
                "n": len(samples)})
        _VOICE_JOBS.finish(
            account_id, token, status="done", phase="done",
            sample_count=len(samples), suggested_knowledge=suggested)
        _log.info("email.voice_profile_built", account_id=account_id,
                  samples=len(samples), suggested=suggested)
    except Exception as exc:
        msg = str(exc)[:300] or "Build failed."
        try:
            async with _tenant_session() as db:
                await db.execute(text(
                    """UPDATE email_voice_profiles SET
                         status = 'FAILED', last_error = :err, updated_at = now()
                       WHERE account_id = :aid"""
                ), {"aid": account_id, "err": msg})
        except Exception:
            pass
        _VOICE_JOBS.finish(account_id, token, status="error", error=msg)
        _log.warning("email.voice_profile_build_failed",
                     account_id=account_id, error=msg)


# ── Endpoints ───────────────────────────────────────────────────────────────


def _profile_dict(account_id: str, row: Any, suggested: int) -> dict[str, Any]:
    if not row:
        return {"account_id": account_id, "status": "EMPTY", "enabled": True,
                "style_guide": "", "traits": {}, "sources": ["sent"],
                "range_start": None, "range_end": None, "analyzed_count": 0,
                "built_at": None, "last_error": None,
                "suggested_knowledge": suggested}
    eff_status = row.status
    last_error = row.last_error
    # The tracker dies with the process; a BUILDING row with no live job is a
    # build that will never finish — surface it as failed, not stuck.
    if eff_status == "BUILDING" and not _VOICE_JOBS.is_running(account_id):
        eff_status = "FAILED"
        last_error = "The build was interrupted. Run it again."
    return {
        "account_id": account_id,
        "status": eff_status,
        "enabled": bool(row.enabled),
        "style_guide": row.style_guide or "",
        "traits": _parse_traits(row.traits),
        "sources": list(row.sources or ["sent"]),
        "range_start": row.range_start.isoformat() if row.range_start else None,
        "range_end": row.range_end.isoformat() if row.range_end else None,
        "analyzed_count": int(row.analyzed_count or 0),
        "built_at": row.built_at.isoformat() if row.built_at else None,
        "last_error": last_error,
        "suggested_knowledge": suggested,
    }


async def _count_suggested(db: Any, account_id: str) -> int:
    row = (await db.execute(text(
        """SELECT COUNT(*) AS c FROM email_knowledge
           WHERE account_id = :aid AND status = 'suggested'"""
    ), {"aid": account_id})).fetchone()
    return int(row.c) if row else 0


@router.get("/voice-profile")
async def get_voice_profile(
    account_id: str = Query(...),
    user: UserContext = Depends(get_current_user),
):
    """The account's voice profile (or an EMPTY placeholder), plus how many
    suggested knowledge entries are waiting for review."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        row = (await db.execute(text(
            """SELECT enabled, status, style_guide, traits, sources,
                      range_start, range_end, analyzed_count, last_error,
                      built_at
               FROM email_voice_profiles WHERE account_id = :aid"""
        ), {"aid": account_id})).fetchone()
        suggested = await _count_suggested(db, account_id)
        return _profile_dict(account_id, row, suggested)


@router.get("/voice-profile/preview")
async def preview_voice_profile(
    account_id: str = Query(...),
    start_date: str | None = None,
    end_date: str | None = None,
    sources: str = "sent",
    user: UserContext = Depends(get_current_user),
):
    """How many emails a build over this range would study, per source — shown
    live in the dialog so the range picker isn't a shot in the dark."""
    src = _clean_sources([s.strip() for s in sources.split(",")])
    start = _parse_iso_date(start_date, end_of_day=False)
    end = _parse_iso_date(end_date, end_of_day=True)
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        counts: dict[str, int] = {}
        for folder in _VALID_SOURCES:
            if folder not in src:
                counts[folder] = 0
                continue
            clauses = ["account_id = :aid",
                       "LOWER(COALESCE(folder, '')) = :folder"]
            params: dict[str, Any] = {"aid": account_id, "folder": folder}
            if start is not None:
                clauses.append("received_at >= :start")
                params["start"] = start
            if end is not None:
                clauses.append("received_at <= :end")
                params["end"] = end
            row = (await db.execute(text(
                f"SELECT COUNT(*) AS c FROM email_messages "
                f"WHERE {' AND '.join(clauses)}"
            ), params)).fetchone()
            counts[folder] = int(row.c) if row else 0
    total = sum(counts.values())
    return {
        "sent": counts.get("sent", 0),
        "drafts": counts.get("drafts", 0),
        "total": total,
        "will_analyze": min(total, _SAMPLE_CAP),
        "cap": _SAMPLE_CAP,
    }


class VoiceProfileBuildRequest(BaseModel):
    account_id: str
    start_date: str | None = None  # YYYY-MM-DD, inclusive
    end_date: str | None = None    # YYYY-MM-DD, inclusive
    sources: list[str] = Field(default_factory=lambda: ["sent"])
    # Also propose knowledge-base entries from the same pass (they arrive as
    # 'suggested' and only feed drafting once approved).
    extract_knowledge: bool = True


@router.post("/voice-profile/build")
async def build_voice_profile(
    req: VoiceProfileBuildRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
):
    """Start (or restart) a profile build over the chosen range + sources.

    Returns immediately; the dialog polls GET /voice-profile/status. One build
    per account at a time.
    """
    if _VOICE_JOBS.is_running(req.account_id):
        raise HTTPException(status_code=409,
                            detail="A build is already running.")
    sources = _clean_sources(req.sources)
    start = _parse_iso_date(req.start_date, end_of_day=False)
    end = _parse_iso_date(req.end_date, end_of_day=True)
    if start and end and start > end:
        raise HTTPException(status_code=400,
                            detail="Start date is after end date.")
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id,
                                    user.email or "anonymous")
        # Seed/refresh the row first so status survives a process restart.
        await db.execute(text(
            """INSERT INTO email_voice_profiles
                 (account_id, status, sources, range_start, range_end,
                  last_error, updated_at)
               VALUES (:aid, 'BUILDING', :src, :rs, :re, NULL, now())
               ON CONFLICT (account_id) DO UPDATE SET
                 status = 'BUILDING', sources = EXCLUDED.sources,
                 range_start = EXCLUDED.range_start,
                 range_end = EXCLUDED.range_end,
                 last_error = NULL, updated_at = now()"""
        ), {"aid": req.account_id, "src": sources,
            "rs": start.date() if start else None,
            "re": end.date() if end else None})
    token = _VOICE_JOBS.start(
        req.account_id, owner=user.email or "anonymous", status="running",
        phase="collecting", processed=0, total=0)
    background.add_task(
        _build_voice_profile_job, req.account_id, sources, start, end,
        req.extract_knowledge, token)
    return {"scheduled": True, "sources": sources}


@router.get("/voice-profile/status")
async def voice_profile_status(
    account_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Live progress for the most recent build on this account. {"status":
    "idle"} when none has run (or it belongs to a different user)."""
    job = _VOICE_JOBS.get(account_id)
    if not job or job.get("owner") != (user.email or "anonymous"):
        return {"status": "idle"}
    return {k: v for k, v in job.items() if k not in ("owner", "token")}


class VoiceProfileUpdateRequest(BaseModel):
    account_id: str
    enabled: bool | None = None
    style_guide: str | None = None


@router.put("/voice-profile")
async def put_voice_profile(
    req: VoiceProfileUpdateRequest,
    user: UserContext = Depends(get_current_user),
):
    """Edit the profile in place: toggle it, or hand-tune the style guide the
    drafter reads (the built traits stay as the record of what was learned)."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id,
                                    user.email or "anonymous")
        sets, params = [], {"aid": req.account_id}
        if req.enabled is not None:
            sets.append("enabled = :en")
            params["en"] = req.enabled
        if req.style_guide is not None:
            sets.append("style_guide = :sg")
            params["sg"] = req.style_guide.strip()[:4000]
        if not sets:
            raise HTTPException(status_code=400, detail="Nothing to update.")
        res = await db.execute(text(
            f"""UPDATE email_voice_profiles
                SET {', '.join(sets)}, updated_at = now()
                WHERE account_id = :aid"""
        ), params)
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="No profile yet.")
        row = (await db.execute(text(
            """SELECT enabled, status, style_guide, traits, sources,
                      range_start, range_end, analyzed_count, last_error,
                      built_at
               FROM email_voice_profiles WHERE account_id = :aid"""
        ), {"aid": req.account_id})).fetchone()
        suggested = await _count_suggested(db, req.account_id)
        return _profile_dict(req.account_id, row, suggested)


@router.delete("/voice-profile", status_code=status.HTTP_204_NO_CONTENT)
async def delete_voice_profile(
    account_id: str = Query(...),
    user: UserContext = Depends(get_current_user),
):
    """Remove the profile — and the knowledge suggestions it proposed that were
    never approved (approved entries are the user's now and stay)."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        await db.execute(text(
            """DELETE FROM email_knowledge
               WHERE account_id = :aid AND source = 'voice_profile'
                 AND status = 'suggested'"""
        ), {"aid": account_id})
        await db.execute(text(
            "DELETE FROM email_voice_profiles WHERE account_id = :aid"
        ), {"aid": account_id})


class VoiceProfileSampleRequest(BaseModel):
    account_id: str
    # A short scenario to write for, e.g. "Reply to a customer asking for
    # pricing". The dialog offers presets + free text.
    scenario: str = ""


@router.post("/voice-profile/sample")
async def sample_voice_profile(
    req: VoiceProfileSampleRequest,
    user: UserContext = Depends(get_current_user),
):
    """"Try it": write a short sample email in the profile's voice so the user
    can judge the profile before trusting it with real drafts."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id,
                                    user.email or "anonymous")
        row = (await db.execute(text(
            """SELECT style_guide, traits FROM email_voice_profiles
               WHERE account_id = :aid AND status = 'READY'"""
        ), {"aid": req.account_id})).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Build a profile first.")
    block = voice_profile_block(row.style_guide or "", row.traits)
    scenario = (req.scenario or "").strip()[:500] or (
        "Reply to a colleague who asked for a quick status update on a project.")
    try:
        from acb_llm.context import acompletion_with_fallback
        sys_prompt = (
            "Write ONE short sample email body exactly in the user's voice, "
            "strictly following their voice profile below. Output only the "
            "email body (greeting through sign-off) — no subject, no "
            "commentary. Invent plausible but generic specifics.\n\n" + block
        )
        # A member drives the sample, so the slot binds nothing (EM-T4b).
        async with llm_slot():
            resp, _ = await acompletion_with_fallback(
                model="tier-powerful",
                messages=[{"role": "system", "content": sys_prompt},
                          {"role": "user", "content": f"Scenario: {scenario}"}],
                temperature=0.4, max_tokens=700,
            )
        sample = (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        _log.warning("email.voice_profile_sample_failed", error=str(exc)[:160])
        raise HTTPException(status_code=502,
                            detail="Could not generate a sample.") from exc
    if not sample:
        raise HTTPException(status_code=502,
                            detail="Could not generate a sample.")
    return {"sample": sample, "scenario": scenario}


@router.post("/knowledge/{kid}/approve")
async def approve_knowledge(
    kid: str,
    user: UserContext = Depends(get_current_user),
):
    """Approve a suggested knowledge entry so it starts feeding drafts."""
    async with _tenant_session() as db:
        res = await db.execute(text(
            """UPDATE email_knowledge ek SET status = 'active',
                      updated_at = now()
               FROM email_accounts ea
               WHERE ek.id = :id AND ek.account_id = ea.id
                 AND ea.user_id = :uid"""
        ), {"id": kid, "uid": user.email or "anonymous"})
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not found")
        return {"id": kid, "status": "active"}
