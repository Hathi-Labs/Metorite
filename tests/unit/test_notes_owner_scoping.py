"""Notes is private to its owner — reads, deletes, and acting-as.

Spec: ``project-docs/specs/note_taker_app.md``. Four live defects, all
reachable by any member holding ``feature:notes``:

1. ``GET /notes/meetings`` had no owner filter, and its ``query`` parameter
   searches ``m.transcript`` — so the library was a full-text search over every
   recorded conversation in the company.
2. ``_load_meeting`` had no owner filter, so ``GET``/``PATCH``/``PUT`` and the
   hard ``DELETE`` all addressed any colleague's meeting by id.
3. Dispatch acts AS the meeting's owner — ``_dispatch_email`` opens a provider
   session on ``meeting.owner_email`` and SENDS. Neither of the two dispatch
   endpoints checked that the acting caller was that person.
4. ...and neither did the OTHER two routes into ``_dispatch``. ``POST
   /meetings/{id}/summarize`` and ``POST /meetings/{id}/retranscribe`` took no
   owner check and ran notes generation, which deletes the meeting's
   un-promoted draft action items and then calls ``auto_dispatch`` — which
   passed a sentinel actor (``"auto"``) that ``cross_owner_refusal`` waved
   through. So the fix for (3) was reachable around: ask the platform to
   re-summarise a colleague's meeting and the colleague's mailbox sends.
   The sentinel is gone; the triggering member's identity is threaded down
   instead, and the two endpoints are owner-scoped as well, because destroying
   a colleague's action items is its own defect.

These tests need no database: the meeting reads run against a fake session that
plays Postgres for exactly the owner predicate, and the dispatch rules are pure.
"""
from __future__ import annotations

import inspect
import io
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

ALICE = "alice@fracktal.in"
BOB = "bob@fracktal.in"


def _meeting(
    mid: str, owner: str | None, title: str = "Q3 pricing", transcript: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=mid, title=title, transcript=transcript,
        platform="upload", status="ready", language="en",
        duration_s=600.0, owner_email=owner, start_at=None, created_at=None,
        template_key=None, scheduled_at=None, copilot_enabled=None,
        segment_count=0, has_notes=False, pending_actions=0, action_count=0,
        agenda_count=0, has_brief=False, attendee_count=0, is_live=False,
        transcript_source="upload", summary_md="the number is 4cr",
        summary_json=None, scratch_notes=None, attendees=[], speaker_names={},
    )


def _action(kind: str = "email", meeting_id: str = "m-alice") -> SimpleNamespace:
    return SimpleNamespace(
        id="a1", meeting_id=meeting_id, description="send the quote",
        confidence=0.95, status="draft", due_hint=None, segment_ids=[],
        resulting_task_id=None, kind=kind,
        payload={"email_to": "customer@acme.com"},
        dispatch_ref=None, dispatch_error=None, created_at=None,
    )


class _FakeResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def fetchall(self) -> list:
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _draft_action(
    aid: str, meeting_id: str, description: str,
) -> SimpleNamespace:
    """A draft action item, with the dispatch columns ``_dispatch`` reads."""
    return SimpleNamespace(
        id=aid, meeting_id=meeting_id, description=description,
        confidence=0.95, status="draft", due_hint=None, segment_ids=[],
        resulting_task_id=None, kind="task", payload={},
        dispatch_ref=None, dispatch_error=None, created_at=None,
    )


def _recording(
    rid: str, meeting_id: str, path: str, channel: str = "mixed",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=rid, meeting_id=meeting_id, artifact_path=path, channel=channel,
        mime="audio/webm", byte_size=0, duration_s=None, created_at=None,
    )


class _FakeDb:
    """Enough async session to answer the meeting reads, without Postgres.

    It plays the database for ONE predicate — the owner scope — and it decides
    whether to apply it by looking at the PARAMETERS the route bound, not at
    the SQL text. That matters: an earlier version keyed on the literal string
    ``"owner_email = :owner"``, so making the comparison case-insensitive (a
    fix) would have made the fake stop filtering (a false pass). A query that
    binds ``:owner`` is a query that means to scope; one that does not, is not.

    The same rule extends to the two tables that reach a meeting by join —
    ``action_item`` (N2) and ``meeting_recording`` (N1): a query that binds
    ``:owner`` gets its rows filtered through the meeting they belong to, one
    that does not gets everything. So a fix that drops the join, drops the
    bind, or compares the wrong identity all read the same way here: the row
    comes back.
    """

    def __init__(
        self,
        meetings: list,
        actions: list | None = None,
        recordings: list | None = None,
        segments: list | None = None,
    ) -> None:
        self.meetings = meetings
        self.actions = list(actions or [])
        self.recordings = list(recordings or [])
        self.segments = list(segments or [])
        self.statements: list[tuple[str, dict]] = []

    # The owner predicate, applied to a row that has a meeting_id rather than
    # an owner of its own — the join, as the fake sees it.
    def _owned(self, meeting_id, owner: str) -> bool:
        for m in self.meetings:
            if str(m.id) == str(meeting_id):
                return (
                    m.owner_email is None
                    or (m.owner_email or "").casefold() == owner
                )
        return False

    def wrote(self, needle: str) -> bool:
        """Did any statement this session issued contain ``needle``?"""
        return any(needle in sql for sql, _ in self.statements)

    def _scope(self, rows: list, sql: str, params: dict) -> list:
        """Apply the join-side scope: id, meeting id, then ``:owner``."""
        if "meeting_id = :id" in sql:                    # get_audio's shape
            return [
                r for r in rows
                if str(r.meeting_id) == str(params.get("id"))
            ]
        if ":id" in sql:
            rows = [r for r in rows if str(r.id) == str(params.get("id"))]
        if "mid" in params:
            rows = [
                r for r in rows if str(r.meeting_id) == str(params["mid"])
            ]
        if "owner" in params:
            owner = str(params.get("owner") or "").casefold()
            rows = [r for r in rows if self._owned(r.meeting_id, owner)]
        return rows

    def _meeting_rows(self, sql: str, params: dict) -> list:
        rows = list(self.meetings)
        if ":id" in sql:
            rows = [m for m in rows if str(m.id) == str(params.get("id"))]
        if params.get("q"):
            needle = str(params["q"]).casefold()
            rows = [
                m for m in rows
                if needle in (m.title or "").casefold()
                or needle in (m.transcript or "").casefold()
            ]
        if "owner" in params:
            owner = str(params.get("owner") or "").casefold()
            rows = [
                m for m in rows
                if (m.owner_email or "").casefold() == owner
                or m.owner_email is None
            ]
        return rows

    #: INSERT … RETURNING id, by table. Most specific first — the bot insert
    #: names both ``meeting_bot`` and (via its FK column) ``meeting``.
    _RETURNS = (
        ("INTO summary_run", "run-1"),
        ("INTO meeting_bot", "bot-1"),
        ("INTO meeting ", "m-new"),
    )

    async def execute(self, stmt, params=None):
        sql, params = str(stmt), dict(params or {})
        self.statements.append((sql, params))
        # Most specific first: the library SELECT counts action items in a
        # subquery, so testing "FROM action_item" first would misroute it.
        # "FROM meeting m" = library + core.load_owned_meeting;
        # "FROM meeting WHERE" = dispatch._load_meeting (unscoped by design —
        # the dispatch seam does the owner check itself, on the loaded row);
        # "UPDATE meeting" = bot_join's attach branch, which binds the scope
        # into the write rather than reading first (N3).
        if (
            "FROM meeting m" in sql
            or "FROM meeting WHERE" in sql
            or sql.lstrip().upper().startswith("UPDATE MEETING")
        ):
            return _FakeResult(self._meeting_rows(sql, params))
        if "FROM action_item" in sql:
            return _FakeResult(self._scope(self.actions, sql, params))
        if "FROM meeting_recording" in sql:
            return _FakeResult(self._scope(self.recordings, sql, params))
        if "FROM transcript_segment" in sql:
            return _FakeResult([
                s for s in self.segments
                if str(s.meeting_id) == str(params.get("id"))
            ])
        for needle, new_id in self._RETURNS:
            if needle in sql:
                return _FakeResult([SimpleNamespace(id=new_id)])
        return _FakeResult([])              # other inserts / updates

    async def commit(self) -> None:
        return None

    async def __aenter__(self) -> _FakeDb:
        return self

    async def __aexit__(self, *exc) -> bool:
        return False


def _install_db(monkeypatch, module, db: _FakeDb) -> _FakeDb:
    """Point a notes module's DB seam(s) at ``db``.

    H2 note: converted request handlers acquire sessions through the module's
    ``_tenant_session`` alias (an ``asynccontextmanager`` over the shared
    tenant-bound seam), while background/service paths still use ``_get_db``.
    Both are patched when present — a module mid-conversion has both — and the
    fake context manager mirrors the real wrapper's commit-on-clean-exit so
    one-transaction contracts stay observable. GUC plumbing is out of scope
    here; ``test_tenant_session.py`` owns that.
    """
    async def _get_db():
        return db

    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None):
        yield db
        await db.commit()

    if hasattr(module, "_tenant_session"):
        monkeypatch.setattr(module, "_tenant_session", _tenant_session)
    if hasattr(module, "_get_db"):
        monkeypatch.setattr(module, "_get_db", _get_db)
    return db


@pytest.fixture
def two_libraries(monkeypatch):
    """Alice owns one meeting, Bob owns another, and one legacy row has no
    owner at all (the column arrived nullable in migration 95)."""
    from gateway.routes.notes import meetings as m

    return _install_db(monkeypatch, m, _FakeDb([
        _meeting("m-alice", ALICE, "Alice 1:1", "performance review"),
        _meeting("m-bob", BOB, "Bob and the customer", "we can go to 40% off"),
        _meeting("m-legacy", None, "Before owners existed", ""),
    ]))


def _user(email: str):
    from acb_auth import UserContext, UserRole

    return UserContext(email=email, role=UserRole.EMPLOYEE)


# ── 1. The library is yours ──────────────────────────────────────────────────

async def test_the_library_lists_only_your_own_meetings(two_libraries) -> None:
    from gateway.routes.notes.meetings import list_meetings

    titles = {m.title for m in await list_meetings(user=_user(ALICE))}
    assert "Alice 1:1" in titles
    assert "Bob and the customer" not in titles


async def test_the_transcript_search_cannot_reach_a_colleagues_transcript(
    two_libraries,
) -> None:
    """`query` runs against `m.transcript`. Unscoped, it read everyone's."""
    from gateway.routes.notes.meetings import list_meetings

    # The words are Bob's, in the body of his customer call.
    found = await list_meetings(query="40% off", user=_user(ALICE))
    assert [m.title for m in found] == []
    # ...and Bob still finds them.
    assert [m.title for m in await list_meetings(query="40% off", user=_user(BOB))] == [
        "Bob and the customer"
    ]


async def test_a_legacy_meeting_with_no_owner_stays_visible(two_libraries) -> None:
    """Fail-safe, deliberately: excluding NULL-owner rows would make a
    member's own pre-ownership meetings vanish rather than protect anyone.
    Both insert paths stamp an owner, so the set cannot grow.

    Asserted against the WHOLE result, not just "the legacy row is in there" —
    an implementation with no owner filter at all also returns the legacy row,
    so the presence of one row proves nothing on its own. What this pins is
    that the same query keeps the legacy row AND drops Alice's.
    """
    from gateway.routes.notes.meetings import list_meetings

    titles = {m.title for m in await list_meetings(user=_user(BOB))}
    assert titles == {"Bob and the customer", "Before owners existed"}


async def test_a_differently_cased_sign_in_still_sees_its_own_library(
    two_libraries,
) -> None:
    """`owner_email` is stamped verbatim from `X-User-Email`, and an IdP can
    return the same UPN cased differently between sessions (Entra ID does).
    Byte equality fails CLOSED — the member's library silently empties — which
    is the "locked out of my own work" shape, not a leak, but still a defect.
    """
    from gateway.routes.notes.meetings import list_meetings

    titles = {m.title for m in await list_meetings(user=_user("Alice@Fracktal.IN"))}
    assert "Alice 1:1" in titles
    assert "Bob and the customer" not in titles


# ── 2. Every by-id endpoint goes through the same door ───────────────────────

async def test_opening_a_colleagues_meeting_is_404(two_libraries) -> None:
    from gateway.routes.notes.meetings import get_meeting

    with pytest.raises(HTTPException) as exc:
        await get_meeting("m-bob", user=_user(ALICE))
    assert exc.value.status_code == 404


async def test_deleting_a_colleagues_meeting_is_404(two_libraries) -> None:
    """The one that is irreversible: recording, transcript, notes and action
    items all cascade."""
    from gateway.routes.notes.meetings import delete_meeting

    with pytest.raises(HTTPException) as exc:
        await delete_meeting("m-bob", user=_user(ALICE))
    assert exc.value.status_code == 404


async def test_patching_a_colleagues_meeting_is_404(two_libraries) -> None:
    from gateway.routes.notes.core import PatchMeetingRequest
    from gateway.routes.notes.meetings import patch_meeting

    with pytest.raises(HTTPException) as exc:
        await patch_meeting(
            "m-bob", PatchMeetingRequest(title="renamed"), user=_user(ALICE)
        )
    assert exc.value.status_code == 404


async def test_you_can_still_open_your_own_meeting(two_libraries) -> None:
    """The fix has to be a scope, not a wall."""
    from gateway.routes.notes.meetings import get_meeting

    detail = await get_meeting("m-alice", user=_user(ALICE))
    assert detail.title == "Alice 1:1"
    assert detail.summary_md == "the number is 4cr"


# ── 3. Dispatch never acts as somebody else ──────────────────────────────────

def test_the_owner_may_dispatch() -> None:
    from gateway.routes.notes.dispatch import cross_owner_refusal

    assert cross_owner_refusal(ALICE, ALICE) is None
    assert cross_owner_refusal(ALICE, "  ALICE@fracktal.in ") is None
    assert cross_owner_refusal(None, BOB) is None  # legacy row, nobody acted as


def test_a_colleague_may_not() -> None:
    from gateway.routes.notes.dispatch import cross_owner_refusal

    refusal = cross_owner_refusal(ALICE, BOB)
    assert refusal and "another member" in refusal


def test_there_is_no_sentinel_actor_that_passes_the_check() -> None:
    """The regression this file exists for.

    ``cross_owner_refusal`` used to return None for ``actor == "auto"``, on the
    reasoning that ``auto_dispatch`` is the owner's own standing instruction.
    It is not: every background job that reaches ``auto_dispatch`` is started
    by somebody's HTTP request, so the sentinel was a bypass reachable by
    asking the platform to re-summarise a colleague's meeting. Nothing but
    being the owner may pass.
    """
    from gateway.routes.notes import dispatch as d

    assert not hasattr(d, "AUTOMATIC_ACTOR")
    for impostor in ("auto", "system", "system:internal", "", "  ", "AUTO"):
        assert d.cross_owner_refusal(ALICE, impostor), impostor


async def test_sending_from_another_members_mailbox_is_refused() -> None:
    """The seam that actually sends, checked before it opens anything."""
    from gateway.routes.notes.dispatch import DispatchError, _dispatch_email

    with pytest.raises(DispatchError) as exc:
        await _dispatch_email(_action(), _meeting("m-alice", ALICE), ALICE, BOB)
    assert "another member" in str(exc.value)


async def test_dispatch_refuses_before_reaching_any_kinds_handler(
    monkeypatch,
) -> None:
    """All four routes reach ``_dispatch``; the check lives at the seam they
    share, so none can inherit the hole. Nothing kind-specific may run."""
    from gateway.routes.notes import dispatch as d

    async def _must_not_run(*args, **kwargs):
        raise AssertionError("dispatched on another member's behalf")

    audited: list[tuple] = []

    async def _audit(actor, action_id, meeting_id, kind, ref, error):
        audited.append((actor, kind, error))

    monkeypatch.setattr(d, "_dispatch_email", _must_not_run)
    monkeypatch.setattr(d, "_dispatch_document", _must_not_run)
    monkeypatch.setattr(d, "_create_task_from_action", _must_not_run)
    monkeypatch.setattr(d, "_mark", _must_not_run)
    monkeypatch.setattr(d, "_audit", _audit)

    ref, error = await d._dispatch(_action(), _meeting("m-alice", ALICE), BOB)

    assert ref is None
    assert error and "another member" in error
    # The attempt is recorded — acting as a colleague is what an audit trail
    # exists to catch.
    assert audited and audited[0][0] == BOB


# ── 4. The automatic path carries the trigger's identity, not a sentinel ─────

@pytest.fixture
def auto_dispatch_rig(monkeypatch):
    """Alice's meeting, one confident draft email item, auto-send switched on.

    ``auto_dispatch_emails`` defaults to True in ``settings.load`` and a member
    with no ``copilot_config`` row gets the defaults, so this is the shipped
    configuration, not a contrived one.
    """
    from gateway.routes.notes import dispatch as d
    from gateway.routes.notes import settings as notes_settings

    _install_db(
        monkeypatch, d,
        _FakeDb([_meeting("m-alice", ALICE)], actions=[_action("email")]),
    )

    async def _load(_owner):
        return SimpleNamespace(
            auto_dispatch_tasks=True, auto_dispatch_emails=True,
            auto_dispatch_docs=True,
        )

    monkeypatch.setattr(notes_settings, "load", _load)

    sent: list[tuple] = []

    async def _send(action, meeting, owner_email, actor):
        sent.append((owner_email, actor))
        return "sent:msg-1"

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(d, "_dispatch_email", _send)
    monkeypatch.setattr(d, "_mark", _noop)
    monkeypatch.setattr(d, "_audit", _noop)
    return d, sent


async def test_auto_dispatch_triggered_by_a_colleague_sends_nothing(
    auto_dispatch_rig,
) -> None:
    """THE P0.

    Bob holds ``feature:notes`` and knows the id of Alice's meeting. He calls
    ``POST /notes/meetings/{alice_id}/summarize`` (or ``/retranscribe``).
    Generation re-extracts the action items and hands them to
    ``auto_dispatch``. Before this fix that call passed ``AUTOMATIC_ACTOR``,
    ``cross_owner_refusal`` returned None for it, the ``_dispatch_email``
    backstop returned None for it too, and a real LLM-drafted email left
    Alice's mailbox for a real external address on Bob's say-so.

    Nothing about the confidence gate helps: ``MIN_AUTO_CONFIDENCE`` is an LLM
    score, not an authorization.
    """
    d, sent = auto_dispatch_rig

    result = await d.auto_dispatch("m-alice", BOB)

    assert sent == [], "a colleague's request made the owner's mailbox send"
    assert result["dispatched"] == 0
    assert result["failed"] == 1


async def test_auto_dispatch_triggered_by_the_owner_still_sends(
    auto_dispatch_rig,
) -> None:
    """...and the fix is a scope, not a wall. Alice asking for her own notes
    still gets her own auto-dispatch — otherwise this "fix" would simply have
    switched the feature off."""
    d, sent = auto_dispatch_rig

    result = await d.auto_dispatch("m-alice", ALICE)

    assert sent == [(ALICE, ALICE)]
    assert result["dispatched"] == 1


def test_the_whole_chain_demands_a_triggering_identity() -> None:
    """The identity is threaded through four function boundaries between the
    endpoint and the send. Every one of them takes it as a REQUIRED parameter,
    so a caller that forgets is a ``TypeError`` at import-or-call time rather
    than a silent return to acting as anybody.
    """
    from gateway.routes.notes.dispatch import auto_dispatch
    from gateway.routes.notes.pipeline import run_transcription
    from gateway.routes.notes.summaries import enqueue_summary, generate_notes

    for fn in (auto_dispatch, enqueue_summary, generate_notes, run_transcription):
        param = inspect.signature(fn).parameters.get("triggered_by")
        assert param is not None, f"{fn.__name__} lost the triggering identity"
        assert param.default is inspect.Parameter.empty, (
            f"{fn.__name__}.triggered_by has a default; a dropped argument "
            "must not silently resolve to somebody"
        )


# ── 5. ...and the two entry endpoints are owner-scoped in their own right ────

async def test_summarizing_a_colleagues_meeting_is_404(monkeypatch) -> None:
    """Independent of the dispatch seam: generation DELETES the meeting's
    un-promoted draft action items before re-extracting them, so an unscoped
    endpoint let any member destroy a colleague's triage queue whether or not
    anything was ever dispatched."""
    from gateway.routes.notes import summaries

    _install_db(monkeypatch, summaries, _FakeDb([
        _meeting("m-alice", ALICE), _meeting("m-bob", BOB),
    ]))

    with pytest.raises(HTTPException) as exc:
        await summaries.summarize("m-bob", user=_user(ALICE))
    assert exc.value.status_code == 404
    assert exc.value.detail == "meeting not found"


async def test_retranscribing_a_colleagues_meeting_is_404(monkeypatch) -> None:
    from gateway.routes.notes import recordings

    _install_db(monkeypatch, recordings, _FakeDb([
        _meeting("m-alice", ALICE), _meeting("m-bob", BOB),
    ]))

    with pytest.raises(HTTPException) as exc:
        await recordings.retranscribe("m-bob", user=_user(ALICE))
    assert exc.value.status_code == 404
    assert exc.value.detail == "meeting not found"


async def test_summarizing_your_own_meeting_still_works(monkeypatch) -> None:
    """The owner half of the same check — 404-ing everybody would 'fix' this
    by breaking the feature."""
    from gateway.routes.notes import summaries

    _install_db(monkeypatch, summaries, _FakeDb([_meeting("m-alice", ALICE)]))

    queued: list[tuple] = []

    async def _enqueue(meeting_id, triggered_by):
        queued.append((meeting_id, triggered_by))
        return "run-1"

    monkeypatch.setattr(summaries, "enqueue_summary", _enqueue)

    out = await summaries.summarize("m-alice", user=_user(ALICE))
    assert out == {"run_id": "run-1", "status": "queued"}
    assert queued == [("m-alice", ALICE)]


# ═════════════════════════════════════════════════════════════════════════════
# N1 / N2 / N3 — the three holes PR #346 named and left open.
#
# Everything above this line is #346. Everything below closes the rest of the
# same defect: a meeting is private to its owner, and until now that was true
# of the library, the by-id CRUD and the dispatch seam only. Sixteen routes in
# six files reached a meeting by a caller-supplied id without asking whose it
# was.
# ═════════════════════════════════════════════════════════════════════════════

# ── N1a. Recordings — the audio itself ───────────────────────────────────────

@pytest.fixture
def recordings_rig(monkeypatch, tmp_path):
    """Alice and Bob each own a meeting with one recording on disk.

    ``media_dir`` is redirected at a tmp path, so "did a colleague's upload
    land in the media store" is a question this rig can actually answer.
    """
    from gateway.routes.notes import live_session
    from gateway.routes.notes import recordings as rec

    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(rec, "media_dir", lambda: media)
    db = _install_db(monkeypatch, rec, _FakeDb(
        [
            _meeting("m-alice", ALICE), _meeting("m-bob", BOB),
            _meeting("m-legacy", None),
        ],
        recordings=[
            _recording("r-alice", "m-alice", "m-alice/r-alice.webm"),
            _recording("r-bob", "m-bob", "m-bob/r-bob.webm"),
        ],
    ))
    for r in db.recordings:
        p = media / r.artifact_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"0123456789")

    spawned: list = []

    def _spawn(coro):
        coro.close()               # never actually transcribe
        spawned.append(coro)

    monkeypatch.setattr(rec, "_spawn_pipeline", _spawn)
    monkeypatch.setattr(rec, "_REC_SEQ", {})

    async def _begin(*a, **k):
        return None

    monkeypatch.setattr(live_session, "begin", _begin)
    return rec, db, media, spawned


def _upload(name: str = "audio.webm", body: bytes = b"0123456789"):
    from fastapi import UploadFile

    return UploadFile(file=io.BytesIO(body), filename=name)


def _fake_request(body: bytes) -> SimpleNamespace:
    async def _body() -> bytes:
        return body

    return SimpleNamespace(body=_body)


async def test_uploading_audio_into_a_colleagues_meeting_is_404(
    recordings_rig,
) -> None:
    """An upload is a write AND an entry into the transcription pipeline,
    which chains notes generation and ``auto_dispatch`` — the same reach
    ``retranscribe`` was guarded for in PR #346."""
    rec, db, media, spawned = recordings_rig

    with pytest.raises(HTTPException) as exc:
        await rec.upload_recording(
            "m-bob", file=_upload(), channel="upload", user=_user(ALICE)
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "meeting not found"
    assert spawned == [], "a colleague's upload started a transcription run"
    assert not db.wrote("INSERT INTO meeting_recording")
    # ...and no bytes were left behind in the colleague's media directory.
    assert list((media / "m-bob").glob("*")) == [
        media / "m-bob" / "r-bob.webm"
    ]


async def test_uploading_audio_into_your_own_meeting_still_works(
    recordings_rig,
) -> None:
    rec, _db, _media, spawned = recordings_rig

    out = await rec.upload_recording(
        "m-alice", file=_upload(), channel="upload", user=_user(ALICE)
    )
    assert out["status"] == "processing"
    assert out["run_id"] == "run-1"
    assert len(spawned) == 1


async def test_starting_a_recording_on_a_colleagues_meeting_is_404(
    recordings_rig,
) -> None:
    """``start_recording`` flips the meeting to ``status='recording'`` and
    opens a writable file on it — the same mutation N3 closes from the
    bot-join side."""
    rec, db, media, _spawned = recordings_rig

    with pytest.raises(HTTPException) as exc:
        await rec.start_recording(
            "m-bob", rec.StartRecordingRequest(), user=_user(ALICE)
        )
    assert exc.value.status_code == 404
    assert not db.wrote("UPDATE meeting SET status='recording'")
    assert list((media / "m-bob").glob("*")) == [
        media / "m-bob" / "r-bob.webm"
    ]


async def test_starting_a_recording_on_your_own_meeting_still_works(
    recordings_rig,
) -> None:
    rec, db, _media, _spawned = recordings_rig

    out = await rec.start_recording(
        "m-alice", rec.StartRecordingRequest(), user=_user(ALICE)
    )
    assert out["recording_id"]
    assert db.wrote("UPDATE meeting SET status='recording'")


async def test_appending_a_chunk_to_a_colleagues_recording_is_404(
    recordings_rig,
) -> None:
    """Arbitrary bytes appended to the file that is about to be transcribed
    as somebody else's meeting."""
    rec, _db, media, _spawned = recordings_rig
    before = (media / "m-bob" / "r-bob.webm").read_bytes()

    with pytest.raises(HTTPException) as exc:
        await rec.append_chunk(
            "m-bob", "r-bob", _fake_request(b"XXX"), 1, user=_user(ALICE)
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "recording not found"
    assert (media / "m-bob" / "r-bob.webm").read_bytes() == before


async def test_appending_a_chunk_to_your_own_recording_still_works(
    recordings_rig,
) -> None:
    rec, _db, media, _spawned = recordings_rig

    out = await rec.append_chunk(
        "m-alice", "r-alice", _fake_request(b"XXX"), 1, user=_user(ALICE)
    )
    assert out == {"ok": True, "seq": 1}
    assert (media / "m-alice" / "r-alice.webm").read_bytes().endswith(b"XXX")


async def test_completing_a_colleagues_recording_is_404(recordings_rig) -> None:
    rec, _db, _media, spawned = recordings_rig

    with pytest.raises(HTTPException) as exc:
        await rec.complete_recording(
            "m-bob", "r-bob", rec.CompleteRecordingRequest(duration_s=1.0),
            user=_user(ALICE),
        )
    assert exc.value.status_code == 404
    assert spawned == []


async def test_completing_your_own_recording_still_works(
    recordings_rig,
) -> None:
    rec, _db, _media, spawned = recordings_rig

    out = await rec.complete_recording(
        "m-alice", "r-alice", rec.CompleteRecordingRequest(duration_s=1.0),
        user=_user(ALICE),
    )
    assert out["status"] == "processing"
    assert len(spawned) == 1


async def test_playing_back_a_colleagues_audio_is_404(recordings_rig) -> None:
    """The raw recording — the verbatim audio the transcript is only a lossy
    rendering of."""
    rec, _db, _media, _spawned = recordings_rig

    with pytest.raises(HTTPException) as exc:
        await rec.get_audio("m-bob", user=_user(ALICE))
    assert exc.value.status_code == 404
    assert exc.value.detail == "meeting not found"


async def test_playing_back_your_own_audio_still_works(recordings_rig) -> None:
    rec, _db, media, _spawned = recordings_rig

    resp = await rec.get_audio("m-alice", user=_user(ALICE))
    assert str(resp.path) == str(media / "m-alice" / "r-alice.webm")


async def test_a_legacy_recording_with_no_owner_stays_reachable(
    recordings_rig,
) -> None:
    """Same fail-safe as the library: NULL-owner rows predate migration 95 and
    excluding them would hide a member's own old meetings."""
    rec, db, media, _spawned = recordings_rig
    db.recordings.append(
        _recording("r-legacy", "m-legacy", "m-legacy/r-legacy.webm")
    )
    (media / "m-legacy").mkdir()
    (media / "m-legacy" / "r-legacy.webm").write_bytes(b"0123456789")

    resp = await rec.get_audio("m-legacy", user=_user(BOB))
    assert str(resp.path) == str(media / "m-legacy" / "r-legacy.webm")
    out = await rec.append_chunk(
        "m-legacy", "r-legacy", _fake_request(b"Z"), 1, user=_user(BOB)
    )
    assert out == {"ok": True, "seq": 1}


async def test_a_differently_cased_sign_in_still_reaches_its_own_audio(
    recordings_rig,
) -> None:
    rec, _db, media, _spawned = recordings_rig

    resp = await rec.get_audio("m-alice", user=_user("Alice@Fracktal.IN"))
    assert str(resp.path) == str(media / "m-alice" / "r-alice.webm")


# ── N1b. Ask-the-meeting — the whole transcript, in prose ────────────────────

@pytest.fixture
def qa_rig(monkeypatch):
    from gateway.routes.notes import qa

    db = _install_db(monkeypatch, qa, _FakeDb(
        [_meeting("m-alice", ALICE), _meeting("m-bob", BOB)],
        segments=[
            SimpleNamespace(
                id="s1", meeting_id="m-bob", idx=1,
                text="we can go to 40% off", speaker_label="S1",
                channel="mixed",
            )
        ],
    ))

    async def _llm_json(system, user, model, **kw):
        return {"answer": "40% off", "refs": [1]}

    monkeypatch.setattr(qa, "_llm_json", _llm_json)
    monkeypatch.setattr(qa, "_model", lambda _k: "tier-fast")
    return qa, db


async def test_asking_about_a_colleagues_meeting_is_404(qa_rig) -> None:
    """The sharpest read in N1: it does not quote the transcript, it answers
    questions about it — a better interface to a colleague's conversations
    than the library search PR #346 closed."""
    qa, db = qa_rig

    with pytest.raises(HTTPException) as exc:
        await qa.ask_meeting(
            "m-bob", qa.AskRequest(question="what discount did we offer?"),
            user=_user(ALICE),
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "meeting not found"
    # The refusal precedes the read: no transcript was ever loaded.
    assert not db.wrote("FROM transcript_segment")


async def test_asking_about_your_own_meeting_still_answers(qa_rig) -> None:
    qa, db = qa_rig
    db.segments[0].meeting_id = "m-alice"

    out = await qa.ask_meeting(
        "m-alice", qa.AskRequest(question="what discount did we offer?"),
        user=_user(ALICE),
    )
    assert out.answer == "40% off"
    assert [c.segment_id for c in out.citations] == ["s1"]


async def test_the_no_transcript_409_is_not_an_oracle(qa_rig) -> None:
    """A colleague's meeting must answer 404 whether or not it has a
    transcript — otherwise 409-vs-404 tells you which meeting ids are real
    and how far along they are."""
    qa, db = qa_rig
    db.segments.clear()

    with pytest.raises(HTTPException) as exc:
        await qa.ask_meeting(
            "m-bob", qa.AskRequest(question="anything?"), user=_user(ALICE)
        )
    assert exc.value.status_code == 404


# ── N1c. Share — drafting a recap of somebody else's meeting ─────────────────

@pytest.fixture
def share_rig(monkeypatch):
    from gateway.routes.notes import share

    db = _install_db(monkeypatch, share, _FakeDb(
        [_meeting("m-alice", ALICE), _meeting("m-bob", BOB)]
    ))

    async def _draft(title, summary_md):
        return f"Follow-up: {title}", summary_md

    monkeypatch.setattr(share, "_draft", _draft)
    return share, db


async def test_drafting_a_recap_of_a_colleagues_meeting_is_404(
    share_rig,
) -> None:
    """There is no sharing mechanism here to preserve — no grant, no token,
    no redemption route anywhere in the module. The draft is returned to the
    caller's own compose window, so the whole route is a read of a
    colleague's notes plus their attendee list."""
    share, _db = share_rig

    with pytest.raises(HTTPException) as exc:
        await share.draft_followup_email("m-bob", user=_user(ALICE))
    assert exc.value.status_code == 404
    assert exc.value.detail == "meeting not found"


async def test_drafting_a_recap_of_your_own_meeting_still_works(
    share_rig,
) -> None:
    share, _db = share_rig

    draft = await share.draft_followup_email("m-alice", user=_user(ALICE))
    assert draft.subject == "Follow-up: Q3 pricing"
    assert draft.body_text == "the number is 4cr"


# ── N1d. Copilot console — the live suggestions and their transcript refs ────

@pytest.fixture
def copilot_rig(monkeypatch):
    from gateway.routes.notes import copilot

    db = _install_db(monkeypatch, copilot, _FakeDb(
        [_meeting("m-alice", ALICE), _meeting("m-bob", BOB)]
    ))
    return copilot, db


async def test_streaming_a_colleagues_copilot_console_is_404(
    copilot_rig,
) -> None:
    """The bus replays its ring to every late subscriber, and each suggestion
    carries up to 400 characters of what was just said in ``refs.window`` — so
    an unscoped stream is a live transcript feed with extra steps."""
    copilot, _db = copilot_rig

    with pytest.raises(HTTPException) as exc:
        await copilot.copilot_stream("m-bob", user=_user(ALICE))
    assert exc.value.status_code == 404


async def test_streaming_your_own_copilot_console_still_works(
    copilot_rig,
) -> None:
    copilot, _db = copilot_rig

    resp = await copilot.copilot_stream("m-alice", user=_user(ALICE))
    assert resp.media_type == "text/event-stream"


async def test_reading_a_colleagues_copilot_history_is_404(
    copilot_rig,
) -> None:
    copilot, db = copilot_rig

    with pytest.raises(HTTPException) as exc:
        await copilot.copilot_events("m-bob", user=_user(ALICE))
    assert exc.value.status_code == 404
    assert not db.wrote("FROM copilot_event")


async def test_reading_your_own_copilot_history_still_works(
    copilot_rig,
) -> None:
    copilot, db = copilot_rig

    assert await copilot.copilot_events("m-alice", user=_user(ALICE)) == {
        "events": []
    }
    assert db.wrote("FROM copilot_event")


# ── N1e. Live tokens — one member route scoped, two machine routes not ───────

async def test_minting_a_live_token_against_a_colleagues_meeting_is_404(
    monkeypatch,
) -> None:
    """``meeting_id`` is caller-supplied and reads per-meeting state; the 409
    reason distinguished "the copilot is off for this meeting" from "live
    transcription is switched off", which is a presence oracle over somebody
    else's calendar."""
    from gateway.routes.notes import live

    _install_db(monkeypatch, live, _FakeDb(
        [_meeting("m-alice", ALICE), _meeting("m-bob", BOB)]
    ))
    minted: list = []

    async def _issue():
        minted.append(True)
        return live.LiveToken(
            provider="assemblyai", token="t", model="m", expires_in=60
        )

    async def _wanted(_mid):
        return True, "wanted"

    monkeypatch.setattr(live, "_issue_live_token", _issue)
    monkeypatch.setattr(live, "live_wanted", _wanted)

    with pytest.raises(HTTPException) as exc:
        await live.live_token(meeting_id="m-bob", user=_user(ALICE))
    assert exc.value.status_code == 404
    assert minted == []

    # ...and the owner still gets one, as does a caller who names no meeting.
    assert (await live.live_token(meeting_id="m-alice", user=_user(ALICE))).token == "t"
    assert (await live.live_token(user=_user(ALICE))).token == "t"


def test_the_two_bot_token_routes_stay_machine_authed() -> None:
    """DECISION, recorded rather than assumed: ``/live/wanted`` and
    ``/stt/bot-live-token`` are NOT given a member owner check.

    Their caller is the meeting-bot worker — its own container, holding
    ``MEETING_BOT_TOKEN`` and no member identity. An owner predicate needs an
    owner, and the two ways to invent one (trust a client-supplied email, or
    fall back to the meeting's own owner) would each turn the bot token into a
    way to assert an identity. The bot token IS the authority here, exactly as
    it is for ``/live/segment``, which posts the transcript ``/live/wanted``
    only decides whether to keep paying for.

    This test pins the shape so the decision cannot be reversed silently: the
    two routes take no ``UserContext``, and they still demand the bot token.
    (``test_org_access_enforcement`` owns the other half — it fails on ANY
    userless route under a gated router that is not in its registry.)

    ⚠️ Found while writing this, NOT fixed here (it is the opposite of this
    PR's direction — it would *open* a route, and no acceptance criterion asks
    for it): ``/notes/meetings/{meeting_id}/live/wanted`` is absent from BOTH
    ``main.PUBLIC_ROUTES`` and ``core.router``'s ``exempt`` list, so the
    app-wide ``require_authenticated`` and then the feature gate 401 the
    worker before ``_check_bot_auth`` ever runs. Its two siblings
    (``/live/segment``, ``/stt/bot-live-token``) are in both lists — the
    assertion below pins that contrast rather than asserting the broken one.
    """
    from gateway.routes.notes import core, live

    for fn in (live.read_live_wanted, live.bot_live_token):
        params = inspect.signature(fn).parameters
        assert not any(
            "UserContext" in str(p.annotation) for p in params.values()
        ), f"{fn.__name__} grew a member identity — see the docstring"
        assert "authorization" in params, (
            f"{fn.__name__} lost its bot-token header — it would then be "
            "reachable by anybody the feature gate lets through"
        )

    # ``require_feature_router`` closes over its exemption set; the worker
    # calls a route under a gated prefix, so the exemption is what makes the
    # machine auth reachable at all.
    dep = core.router.dependencies[0].dependency
    exempt: set[str] = set()
    for cell in dep.__closure__ or ():
        value = cell.cell_contents
        if isinstance(value, frozenset):
            exempt |= set(value)
    assert "/notes/stt/bot-live-token" in exempt
    assert "/notes/meetings/{meeting_id}/live/segment" in exempt


# ── N2. Approve / reject a single action item ────────────────────────────────

class _RecordingSource:
    """The one task seam, as the approve route sees it (WS-39 S8c).

    The route captures through ``item_source()``. What these tests pin is WHO
    the capture is filed under and WHAT text reaches it, not which table the
    seam writes, so the seam is a recorder here. Its SQL is proven in
    ``test_tasks_ai_source.py`` and against Postgres in ``live_ws39_s8c.py``.
    """

    name = "recording"

    def __init__(self) -> None:
        self.captures: list[SimpleNamespace] = []

    async def find_by_origin(self, db, uid, key, value, *, commitment=False):
        return next((
            c for c in self.captures
            if c.uid == uid and str(c.origin.get(key)) == str(value)
        ), None)

    async def insert_capture(self, db, uid, fields, origin):
        cap = SimpleNamespace(
            id=f"task-{len(self.captures) + 1}", uid=uid,
            fields=dict(fields), origin=dict(origin or {}))
        self.captures.append(cap)
        return cap.id


@pytest.fixture
def actions_rig(monkeypatch):
    """One draft action item on each of Alice's and Bob's meetings."""
    from gateway.routes.notes import actions
    from gateway.routes.tasks import item_source as seam

    source = _RecordingSource()
    monkeypatch.setattr(seam, "item_source", lambda: source)

    db = _install_db(monkeypatch, actions, _FakeDb(
        [
            _meeting("m-alice", ALICE), _meeting("m-bob", BOB),
            _meeting("m-legacy", None),
        ],
        # Carry the dispatch columns too, so the pre-fix run of
        # ``approve-all`` reaches its real answer (a 200 with an empty list)
        # rather than dying on a missing attribute — the red has to be the
        # security claim, not a fixture artefact.
        actions=[
            _draft_action("a-alice", "m-alice", "send Alice's quote"),
            _draft_action(
                "a-bob", "m-bob", "give Acme 40% off, per the call"
            ),
            _draft_action("a-legacy", "m-legacy", "legacy item"),
        ],
    ))
    db.source = source
    return actions, db


async def test_approving_a_colleagues_action_item_is_404(actions_rig) -> None:
    actions, _db = actions_rig

    with pytest.raises(HTTPException) as exc:
        await actions.approve_action("a-bob", user=_user(ALICE))
    assert exc.value.status_code == 404
    assert exc.value.detail == "action item not found"


async def test_approving_a_colleagues_action_creates_no_task_in_your_list(
    actions_rig,
) -> None:
    """THE N2 HARM, stated as itself.

    ``_create_task_from_action`` files the capture under **the caller** and
    copies ``action.description`` into the task title. So the route was not
    only a way to flip somebody else's triage state — it was a way to lift
    the text of their action item into a durable row in your own GTD list.
    A 404 is not enough on its own; nothing may be written.
    """
    actions, db = actions_rig

    with pytest.raises(HTTPException):
        await actions.approve_action("a-bob", user=_user(ALICE))

    assert not db.source.captures, "a capture was written for the caller"
    assert not db.wrote("UPDATE action_item")
    assert not any(
        "40% off" in str(params) for _sql, params in db.statements
    ), "the colleague's description reached a write"


async def test_approving_your_own_action_item_still_creates_the_task(
    actions_rig,
) -> None:
    actions, db = actions_rig

    out = await actions.approve_action("a-alice", user=_user(ALICE))
    assert out.status == "created"
    assert out.resulting_task_id == "task-1"
    (cap,) = db.source.captures
    assert cap.uid == ALICE
    assert cap.fields["title"] == "send Alice's quote"


async def test_rejecting_a_colleagues_action_item_is_404(actions_rig) -> None:
    """The other half of the same harm: dismissing an item out of somebody
    else's triage queue, with no way back through this API."""
    actions, db = actions_rig

    with pytest.raises(HTTPException) as exc:
        await actions.reject_action("a-bob", user=_user(ALICE))
    assert exc.value.status_code == 404
    assert not db.wrote("UPDATE action_item")


async def test_rejecting_your_own_action_item_still_works(actions_rig) -> None:
    actions, db = actions_rig

    out = await actions.reject_action("a-alice", user=_user(ALICE))
    assert out.status == "rejected"
    assert db.wrote("UPDATE action_item")


async def test_a_legacy_action_with_no_meeting_owner_stays_reachable(
    actions_rig,
) -> None:
    actions, _db = actions_rig

    out = await actions.approve_action("a-legacy", user=_user(BOB))
    assert out.status == "created"


async def test_a_differently_cased_sign_in_still_approves_its_own_action(
    actions_rig,
) -> None:
    actions, _db = actions_rig

    out = await actions.approve_action(
        "a-alice", user=_user("Alice@Fracktal.IN")
    )
    assert out.status == "created"


async def test_approve_all_on_a_colleagues_meeting_is_404(
    actions_rig,
) -> None:
    """Aligned with the single-item routes. ``approve-all`` was already safe
    at the seam — every item goes through ``_dispatch``, which refuses a
    cross-owner actor — but it answered 200 with an empty list, which says
    "your meeting, nothing qualified" where the truth is "not your meeting",
    and it read the colleague's draft items to get there."""
    actions, db = actions_rig

    with pytest.raises(HTTPException) as exc:
        await actions.approve_all(
            "m-bob", actions.BulkApproveRequest(), user=_user(ALICE)
        )
    assert exc.value.status_code == 404
    assert not db.wrote("FROM action_item")


async def test_approve_all_on_your_own_meeting_still_dispatches(
    actions_rig, monkeypatch,
) -> None:
    actions, _db = actions_rig
    from gateway.routes.notes import dispatch as notes_dispatch

    seen: list = []

    async def _dispatch(action, meeting, actor):
        seen.append((str(action.id), actor))
        return "task:1", None

    monkeypatch.setattr(notes_dispatch, "_dispatch", _dispatch)

    out = await actions.approve_all(
        "m-alice", actions.BulkApproveRequest(min_confidence=0.5),
        user=_user(ALICE),
    )
    assert out.created == ["a-alice"]
    assert seen == [("a-alice", ALICE)]


# ── N3. bot_join's attach branch ─────────────────────────────────────────────

_MEET_URL = "https://meet.google.com/abc-defg-hij"


@pytest.fixture
def bot_rig(monkeypatch):
    from gateway.routes.notes import live_session
    from gateway.routes.notes import meeting_bot as mb

    db = _install_db(monkeypatch, mb, _FakeDb([
        _meeting("m-alice", ALICE), _meeting("m-bob", BOB),
        _meeting("m-legacy", None),
    ]))
    joined: list = []

    class _Provider:
        async def join(self, url, bot_name, live_callback=None):
            joined.append((url, bot_name))
            return "provider-bot-1"

    monkeypatch.setattr(mb, "resolve_bot_provider", lambda: _Provider())
    monkeypatch.setattr(mb, "_spawn", lambda coro: coro.close())

    async def _begin(*a, **k):
        return None

    monkeypatch.setattr(live_session, "begin", _begin)
    return mb, db, joined


async def test_sending_a_bot_to_a_colleagues_prepared_meeting_is_404(
    bot_rig,
) -> None:
    """THE N3 HARM.

    With ``meeting_id`` set, the route UPDATEs an existing row: it flips the
    meeting to ``recording``, overwrites its title and start time, puts a bot
    in the call and registers live presence under the caller's own identity.
    The acting principal is the CALLER — the only identity the request carries
    — and resolving the check against the row's own ``owner_email`` would
    compare the meeting to itself and pass every time. That is the same reason
    the ingest side reads ``meeting_bot.requested_by`` rather than the owner.
    """
    mb, db, joined = bot_rig

    with pytest.raises(HTTPException) as exc:
        await mb.bot_join(
            mb.BotJoinRequest(
                meeting_url=_MEET_URL, meeting_id="m-bob", title="hijacked"
            ),
            user=_user(ALICE),
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "unknown meeting"
    assert joined == [], "a bot was dispatched to a colleague's meeting"
    assert not db.wrote("INSERT INTO meeting_bot")


async def test_sending_a_bot_to_your_own_prepared_meeting_still_works(
    bot_rig,
) -> None:
    """The whole point of the ``meeting_id`` branch: keep the agenda, brief,
    attendees and copilot decision you just set up."""
    mb, _db, joined = bot_rig

    out = await mb.bot_join(
        mb.BotJoinRequest(
            meeting_url=_MEET_URL, meeting_id="m-alice", title="Q3 pricing"
        ),
        user=_user(ALICE),
    )
    assert out.meeting_id == "m-alice"
    assert out.status == "joining"
    assert [url for url, _name in joined] == [_MEET_URL]


async def test_the_bot_join_create_branch_is_unchanged(bot_rig) -> None:
    """Creating a meeting stays open to any ``feature:notes`` holder — it
    stamps the caller as owner, so there is nobody else's row to reach. Only
    the attach branch is scoped."""
    mb, db, _joined = bot_rig

    out = await mb.bot_join(
        mb.BotJoinRequest(meeting_url=_MEET_URL, title="new one"),
        user=_user(BOB),
    )
    assert out.meeting_id == "m-new"
    inserted = [p for sql, p in db.statements if "INSERT INTO meeting " in sql]
    assert inserted and inserted[0]["o"] == BOB


async def test_attaching_to_a_legacy_meeting_with_no_owner_still_works(
    bot_rig,
) -> None:
    mb, _db, joined = bot_rig

    out = await mb.bot_join(
        mb.BotJoinRequest(meeting_url=_MEET_URL, meeting_id="m-legacy"),
        user=_user(BOB),
    )
    assert out.meeting_id == "m-legacy"
    assert joined


async def test_a_differently_cased_sign_in_still_attaches_to_its_own_meeting(
    bot_rig,
) -> None:
    mb, _db, joined = bot_rig

    out = await mb.bot_join(
        mb.BotJoinRequest(meeting_url=_MEET_URL, meeting_id="m-alice"),
        user=_user("Alice@Fracktal.IN"),
    )
    assert out.meeting_id == "m-alice"
    assert joined


async def test_the_requester_not_the_owner_is_what_the_ingest_carries() -> None:
    """The asymmetry PR #346 established, preserved rather than collapsed.

    N3 makes the attach branch require the caller to BE the owner, so for an
    attach the two identities coincide. That must not be taken as licence to
    read the pipeline's actor off the meeting instead: ``bot_join`` is not the
    only way a ``meeting_bot`` row comes to exist over time, and the rule is
    "authority follows the person who asked".
    """
    from gateway.routes.notes import meeting_bot as mb

    src = inspect.getsource(mb._refresh_bot)
    assert 'getattr(row, "requested_by", None)' in src
    param = inspect.signature(mb._ingest_recording).parameters["requested_by"]
    assert param.default is inspect.Parameter.empty
