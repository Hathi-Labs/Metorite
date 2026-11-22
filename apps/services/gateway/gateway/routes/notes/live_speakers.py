"""Live speaker identity — consistent diarization across streaming chunks.

Streaming ASR emits utterance chunks with only *local* speaker hints; on its own
that gives labels that don't stay consistent across chunks (chunk A's "S1" is not
necessarily chunk C's "S1"). This module keeps a per-meeting **running voiceprint
gallery**: each chunk carries a speaker *embedding*, which is matched (cosine)
against the speakers seen so far and assigned a **stable global id** — or enrolls
a new speaker when nothing matches. That's what lets the copilot know *who* is
talking, consistently, while the meeting is still happening.

It also **binds names live** from self-introductions ("I'm Priya", "this is
Alex") with a cheap, precision-first heuristic — no LLM on the live path — so the
agent can reason about people (and, later, their roles) in real time.

This is the live *draft* identity. The batch re-pass on stop stays authoritative:
offline clustering sees the whole recording and can fix any online drift, and the
LLM speaker-id pass (``speaker_id.py``) refines the names. So live is a good draft
that the batch pass *upgrades* — not a throwaway (spec: live_meeting_copilot.md
§3.5, note_taker_app.md §3.13).

Fail-safe by construction: no embedding → fall back to the provided label; any
error resolves to a plain label rather than raising — the caller can wrap the bus
around it safely.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass

from gateway.routes.notes.core import _log
from sqlalchemy import text


def _threshold() -> float:
    """Cosine similarity at/above which a chunk is judged the SAME speaker as an
    enrolled one. CAM++/pyannote embeddings cluster tightly; ~0.6-0.7 is a sane
    same-speaker floor. Env-overridable per deployment/embedding model."""
    try:
        return float(os.environ.get("NOTES_LIVE_SPEAKER_THRESHOLD", "0.65"))
    except ValueError:
        return 0.65


_LABEL_RE = re.compile(r"^S\d+$")  # the diarized label space shared with batch


# ── pure helpers (no state, trivially testable) ──────────────────────────────

def cosine(a: list[float] | None, b: list[float] | None) -> float:
    """Cosine similarity of two embedding vectors. 0.0 for empty/mismatched/
    zero-norm inputs — never raises."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# Tokens that follow an intro trigger but are NOT names — keeps precision high on
# the live path (the authoritative naming is the batch LLM pass).
_NOT_A_NAME = {
    "a", "an", "the", "not", "so", "just", "really", "gonna", "going", "good",
    "fine", "here", "sure", "sorry", "glad", "happy", "trying", "looking",
    "calling", "thinking", "afraid", "excited", "done", "ready", "okay", "ok",
    "still", "also", "only", "kind", "sort", "like", "about", "from", "with",
    "wondering", "hoping", "worried", "confident", "certain", "aware",
    "guessing", "assuming", "telling", "asking", "saying", "seeing", "hearing",
    "pretty", "quite", "very", "super", "totally", "curious", "new",
}

# Trigger phrases are matched case-insensitively (scoped ``(?i:...)``), but the
# captured NAME stays anchored to a capital letter — so a lowercasing ASR yields
# no false names, while "This is Dana" (sentence-initial capital) still matches.
# The typographic apostrophe U+2019 (ASR often emits it) is matched alongside the
# ASCII one so "I<curly>m Priya" is caught too. It's defined via an escape so the
# source stays ASCII-clean (no ambiguous glyph in the file).
_CURLY = chr(0x2019)  # U+2019 typographic apostrophe (kept out of source glyphs)
_APOS = rf"['{_CURLY}]"          # ASCII or curly apostrophe
_NAME = rf"[A-Za-z'{_CURLY}\-]"  # a name character (either apostrophe)
_NAME_CAP = rf"[A-Z]{_NAME}+(?:\s+[A-Z]{_NAME}+)?"  # 1-2 capitalized tokens
# "my name is Priya" / "My name's Priya Sharma" — strongest signal.
_STRONG = re.compile(rf"\b(?i:my name(?:{_APOS}s| is))\s+({_NAME_CAP})")
# "I'm Priya" / "I am Priya" / "this is Alex" / "you're speaking with Dana".
_MED = re.compile(
    rf"\b(?i:I{_APOS}?m|I am|this is|you{_APOS}?re speaking (?:with|to))\s+"
    rf"({_NAME_CAP})"
)
# "Priya here" / "Alex speaking".
_TRAIL = re.compile(rf"\b({_NAME_CAP})\s+(?i:here|speaking)\b")


def detect_self_intro(text: str) -> str | None:
    """Extract a self-stated name from a segment, or None. Precision over recall:
    requires an explicit first-person intro pattern and a capitalized, non-stop
    word — so "I'm going" / "this is great" never name anyone. A lowercasing ASR
    simply yields no live names (the batch pass still names them)."""
    if not text:
        return None
    t = text.strip()
    for rx in (_STRONG, _MED, _TRAIL):
        m = rx.search(t)
        if not m:
            continue
        name = m.group(1).strip()
        if not name or len(name) > 60:
            continue
        first = name.split()[0].lower().strip("'-" + _CURLY)
        if len(first) < 2 or first in _NOT_A_NAME:
            continue
        return name
    return None


# ── registry (per-meeting state) ─────────────────────────────────────────────

@dataclass
class _Speaker:
    speaker_id: str
    centroid: list[float] | None = None
    count: int = 0
    name: str | None = None
    role: str | None = None


@dataclass
class Resolved:
    speaker_id: str
    name: str | None
    role: str | None
    is_new: bool
    score: float


# How many (speaker, start, end) spans to keep per meeting. Three floats per
# utterance is tiny — a 3-hour meeting is well under this — and it's what the
# batch pass reconciles against on stop.
_TIMELINE_MAX = 5000


class LiveSpeakerRegistry:
    """Per-meeting running voiceprint gallery + live name/role binding."""

    def __init__(self) -> None:
        self._speakers: dict[str, _Speaker] = {}
        self._n = 0  # high-water mark for auto-assigned Sn ids
        # (speaker_id, start_s, end_s) — who spoke when, per the LIVE pass. The
        # batch re-pass reconciles its own labels against this on stop.
        self._timeline: list[tuple[str, float, float]] = []

    # -- enrollment / matching --

    def _enroll(self, embedding: list[float] | None) -> _Speaker:
        self._n += 1
        sp = _Speaker(speaker_id=f"S{self._n}")
        if embedding:
            sp.centroid = list(embedding)
            sp.count = 1
        self._speakers[sp.speaker_id] = sp
        return sp

    def _update_centroid(self, sp: _Speaker, emb: list[float]) -> None:
        if sp.centroid is None or len(sp.centroid) != len(emb):
            sp.centroid = list(emb)
            sp.count = 1
            return
        n = sp.count
        sp.centroid = [
            (c * n + e) / (n + 1)
            for c, e in zip(sp.centroid, emb, strict=True)
        ]
        sp.count = n + 1

    def resolve(
        self,
        embedding: list[float] | None = None,
        fallback_label: str | None = None,
    ) -> Resolved:
        """Assign a stable speaker id to a chunk.

        With an embedding: match against the gallery (cosine ≥ threshold) and
        update that speaker's centroid, else enroll a new one. Without one: pass
        the provided label through unchanged (browser/channel path) so this is a
        no-op for sources that don't yet produce voiceprints."""
        if embedding:
            best_sid: str | None = None
            best_score = -1.0
            for sp in self._speakers.values():
                if sp.centroid is None:
                    continue
                score = cosine(embedding, sp.centroid)
                if score > best_score:
                    best_sid, best_score = sp.speaker_id, score
            if best_sid is not None and best_score >= _threshold():
                sp = self._speakers[best_sid]
                self._update_centroid(sp, embedding)
                return Resolved(sp.speaker_id, sp.name, sp.role, False, best_score)
            sp = self._enroll(embedding)
            return Resolved(sp.speaker_id, sp.name, sp.role, True,
                            max(best_score, 0.0))

        sid = (str(fallback_label).strip() if fallback_label else "") or "?"
        sp = self._speakers.get(sid)
        is_new = sp is None
        if sp is None:
            sp = _Speaker(speaker_id=sid)
            self._speakers[sid] = sp
            # keep the Sn counter ahead of any explicit Sn label we adopt
            if _LABEL_RE.match(sid):
                self._n = max(self._n, int(sid[1:]))
        return Resolved(sp.speaker_id, sp.name, sp.role, is_new, 0.0)

    # -- names / roles --

    def note_text(self, speaker_id: str, text: str) -> str | None:
        """Learn a name from this chunk's text if it's a self-introduction.
        First confident name wins (precision) — never rebinds a named speaker."""
        sp = self._speakers.get(speaker_id)
        if sp is None or sp.name:
            return None
        name = detect_self_intro(text)
        if name:
            sp.name = name
            return name
        return None

    def bind(
        self, speaker_id: str, *, name: str | None = None, role: str | None = None
    ) -> None:
        """Set a name/role explicitly (e.g. role from a business-context lookup,
        or a user correction). Creates the speaker if unknown."""
        sp = self._speakers.get(speaker_id)
        if sp is None:
            sp = _Speaker(speaker_id=speaker_id)
            self._speakers[speaker_id] = sp
        if name and name.strip():
            sp.name = name.strip()
        if role and role.strip():
            sp.role = role.strip()

    def name_of(self, speaker_id: str) -> str | None:
        sp = self._speakers.get(speaker_id)
        return sp.name if sp else None

    # -- identity timeline (what the batch pass reconciles against) --

    def note_span(self, speaker_id: str, start_s: float, end_s: float) -> None:
        """Record that ``speaker_id`` held the floor over [start_s, end_s]."""
        if end_s <= start_s:
            return
        self._timeline.append((speaker_id, float(start_s), float(end_s)))
        if len(self._timeline) > _TIMELINE_MAX:
            del self._timeline[:-_TIMELINE_MAX]

    def timeline(self) -> list[tuple[str, float, float]]:
        return list(self._timeline)

    def names(self) -> dict[str, str]:
        """speaker_id → live-bound name, for speakers that have one."""
        return {
            sp.speaker_id: sp.name for sp in self._speakers.values() if sp.name
        }

    def roster(self) -> list[dict]:
        """Who's on the call so far — the compact identity context the copilot
        (and the console) consume. Ordered by first appearance."""
        return [
            {
                "speaker_id": sp.speaker_id,
                "name": sp.name,
                "role": sp.role,
                "utterances": sp.count,
            }
            for sp in self._speakers.values()
        ]


# ── per-meeting registry map (mirrors the live bus) ──────────────────────────

_REGISTRIES: dict[str, LiveSpeakerRegistry] = {}


def registry(meeting_id: str) -> LiveSpeakerRegistry:
    reg = _REGISTRIES.get(meeting_id)
    if reg is None:
        reg = LiveSpeakerRegistry()
        _REGISTRIES[meeting_id] = reg
    return reg


def reset(meeting_id: str) -> None:
    """Drop a meeting's gallery (e.g. on a fresh join). Safe if absent."""
    _REGISTRIES.pop(meeting_id, None)


# ── Reconciliation: carry live identity onto the authoritative transcript ────
#
# On stop the batch pass re-diarizes the WHOLE recording — better clustering,
# but its labels are its own (batch "S1" need not be live "S1"). Rather than
# discard the identity work done live, we map batch labels → live speakers by
# **maximum time overlap** and carry the live-bound names across. Both clocks
# start when the bot enters the call (ffmpeg and the live stream start together),
# so they share an origin; the matching tolerates the small skew between them.

def overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    """Seconds of overlap between [a0,a1] and [b0,b1] (0 if disjoint)."""
    return max(0.0, min(a1, b1) - max(a0, b0))


def reconcile_labels(
    batch_spans: list[tuple[str, float, float]],
    live_timeline: list[tuple[str, float, float]],
) -> dict[str, str]:
    """Map batch diarization labels → live speaker ids by max total overlap.

    Both inputs are ``(id, start_s, end_s)`` spans. Greedy **one-to-one**
    matching: the strongest pair wins first, and neither side is reused — so if
    the live gallery merged two people, only the better-matching batch label
    inherits that identity rather than both being mislabelled. Pairs with no
    overlap are never matched."""
    totals: dict[tuple[str, str], float] = {}
    for label, b0, b1 in batch_spans:
        for sid, l0, l1 in live_timeline:
            ov = overlap(b0, b1, l0, l1)
            if ov > 0:
                totals[(label, sid)] = totals.get((label, sid), 0.0) + ov
    mapping: dict[str, str] = {}
    used_live: set[str] = set()
    # Deterministic: strongest overlap first, ties broken by name.
    for (label, sid), _ov in sorted(
        totals.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1])
    ):
        if label in mapping or sid in used_live:
            continue
        mapping[label] = sid
        used_live.add(sid)
    return mapping


def live_names_for_batch(
    meeting_id: str, batch_spans: list[tuple[str, float, float]]
) -> dict[str, str]:
    """batch label → live-bound name, for this meeting's live gallery."""
    reg = _REGISTRIES.get(meeting_id)
    if reg is None:
        return {}
    live_names = reg.names()
    if not live_names:
        return {}
    mapping = reconcile_labels(batch_spans, reg.timeline())
    return {
        label: live_names[sid]
        for label, sid in mapping.items()
        if sid in live_names
    }


async def apply_live_names(meeting_id: str, segments: list) -> dict[str, str]:
    """Merge names learned during the LIVE pass into ``meeting.speaker_names``,
    keyed by the authoritative batch labels.

    Runs before the LLM speaker-id pass so live-detected names are already in
    place (that pass then only fills whatever is still anonymous). Non-
    destructive — never overwrites a name the user set. Returns the names newly
    applied; never raises."""
    try:
        spans = [
            (str(s.speaker_label), float(s.start_s or 0.0), float(s.end_s or 0.0))
            for s in segments
            if getattr(s, "speaker_label", None)
        ]
        inferred = live_names_for_batch(meeting_id, spans)
        if not inferred:
            return {}

        from gateway.routes.notes.core import _get_db
        from gateway.routes.notes.speaker_id import merge_inferred

        # H4: called from the background transcription pipeline
        # (`pipeline.run_transcription`). A browser upload's task sees the
        # member's bound tenant (`create_task` copies the context). The
        # meeting-bot ingest's task has none. Derive it from the meeting row.
        async with await _get_db() as db:
            row = (
                await db.execute(
                    text("SELECT speaker_names FROM meeting WHERE id = :id"),
                    {"id": meeting_id},
                )
            ).fetchone()
            if row is None:
                return {}
            existing = row.speaker_names if isinstance(row.speaker_names, dict) else {}
            merged, applied = merge_inferred(existing, inferred, overwrite=False)
            if not applied:
                return {}
            await db.execute(
                text(
                    "UPDATE meeting SET speaker_names = CAST(:n AS JSONB) "
                    "WHERE id = :id"
                ),
                {"n": json.dumps(merged), "id": meeting_id},
            )
            await db.commit()
        _log.info(
            "notes.live_names_reconciled",
            meeting_id=meeting_id, applied=len(applied),
        )
        return applied
    except Exception as exc:
        _log.warning(
            "notes.live_names_reconcile_failed",
            meeting_id=meeting_id, error=str(exc)[:200],
        )
        return {}
