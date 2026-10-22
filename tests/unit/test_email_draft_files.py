"""The reply drafter reads the files of a mail, and never claims it cannot.

Owner, 2026-10-20: a draft told a customer "I could not open your image". The
member can open every file, so the sentence was false, and it went out in the
member's name. The drafter also saw only the NAMES of the files of a mail.

Each rule names the mutation that turns it red (R7):

1. **A readable file reaches the drafter as text**, capped per file and in
   total. Mutation: drop the text read -> ``test_a_pdf_reaches_the_drafter``.
2. **An image is named as not read while the switch is off**, and a logo is
   never named. Mutation: send images always ->
   ``test_an_image_is_not_read_with_the_switch_off``.
3. **The switch needs the Router too.** Mutation: drop the Router half ->
   ``test_images_need_the_switch_and_the_router``.
4. **A slow read never holds the draft.** Mutation: drop the deadline ->
   ``test_a_slow_file_is_named_and_the_draft_goes_on``.
5. **The prompt forbids the claim, and the drafter's text loses it.**
   Mutation: drop ``strip_limit_claims`` from ``_llm_draft_reply`` ->
   ``test_the_drafter_removes_a_claim_it_cannot_open_a_file``.
6. **A true sentence stays**: a file the sender forgot, a person who could
   not attend. Mutation: widen the pattern -> ``test_a_true_sentence_stays``.
7. **An image goes as an image part with a declared vision task.**
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from gateway.routes.email.automation import draft_files as df
from gateway.routes.email.automation import drafting as dr

USER = SimpleNamespace(email="dana@fracktal.test", organization_id="org-1")


def _row(name: str, mime: str, size: int = 100_000, aid: str = "") -> Any:
    return SimpleNamespace(id=aid or f"id-{name}", filename=name, mime_type=mime,
                           size_bytes=size)


def _files(monkeypatch, rows: list[Any], texts: dict[str, Any] | None = None,
           *, slow: set[str] = frozenset()) -> list[str]:
    read: list[str] = []

    async def list_files(db, mid):
        return rows

    async def read_text(aid, user):
        read.append(aid)
        if aid in slow:
            await asyncio.sleep(5)
        return (texts or {}).get(aid) or SimpleNamespace(text="", kind="no_text")

    async def read_image(aid, user, mime):
        read.append(aid)
        return f"data:{mime};base64,AAAA"

    monkeypatch.setattr(df, "_list_files", list_files)
    monkeypatch.setattr(df, "_read_text", read_text)
    monkeypatch.setattr(df, "_read_image", read_image)
    return read


# ── 1 to 4. What the drafter reads ──────────────────────────────────────────────


async def test_a_pdf_reaches_the_drafter(monkeypatch) -> None:
    _files(monkeypatch, [_row("spec.pdf", "application/pdf", aid="a1")],
           {"a1": SimpleNamespace(text="Payload 3 kg. Reach 600 mm." * 1000,
                                  kind="pdf", truncated=False)})
    got = await df.load_draft_files(object(), "m1", USER, vision=False)
    [(name, text, cut)] = got.texts
    assert name == "spec.pdf" and text.startswith("Payload 3 kg.")
    assert len(text) == df.MAX_FILE_CHARS and cut is True
    assert got.unread == [] and got.notices() == []


async def test_an_image_is_not_read_with_the_switch_off(monkeypatch) -> None:
    read = _files(monkeypatch, [_row("cell.jpg", "image/jpeg", aid="i1"),
                                _row("logo.png", "image/png", size=3_000, aid="i2")])
    got = await df.load_draft_files(object(), "m1", USER, vision=False)
    assert read == []
    assert got.images == [] and got.unread == [("cell.jpg", df.UNREAD_IMAGE)]
    [note] = got.notices()
    assert "cell.jpg (an image)" in note and "logo.png" not in note


async def test_an_image_goes_to_the_model_with_the_switch_on(monkeypatch) -> None:
    _files(monkeypatch, [_row("cell.jpg", "image/jpeg", aid="i1")])
    got = await df.load_draft_files(object(), "m1", USER, vision=True)
    assert got.images == [("cell.jpg", "data:image/jpeg;base64,AAAA")]
    assert got.unread == []


def test_images_need_the_switch_and_the_router(monkeypatch) -> None:
    settings = SimpleNamespace(email_draft_reads_images=False)
    monkeypatch.setattr(df, "get_settings", lambda: settings)
    monkeypatch.setattr("acb_llm.routed.routing_is_on", lambda: True)
    assert df.images_on() is False
    settings.email_draft_reads_images = True
    assert df.images_on() is True
    monkeypatch.setattr("acb_llm.routed.routing_is_on", lambda: False)
    assert df.images_on() is False


async def test_a_slow_file_is_named_and_the_draft_goes_on(monkeypatch) -> None:
    _files(monkeypatch, [_row("big.pdf", "application/pdf", aid="s1")], slow={"s1"})
    got = await df.load_draft_files(object(), "m1", USER, budget_s=0.2, vision=False)
    assert got.unread == [("big.pdf", df.UNREAD_SLOW)]


async def test_no_member_session_reads_nothing(monkeypatch) -> None:
    read = _files(monkeypatch, [_row("spec.pdf", "application/pdf", aid="a1")])
    got = await df.load_draft_files(object(), "m1", None, vision=False)
    assert read == [] and got.unread == [("spec.pdf", df.UNREAD_SLOW)]


# ── 5 and 6. The claim never leaves ──────────────────────────────────────────────


@pytest.mark.parametrize(("draft", "left"), [
    ("Dear Ravi,\n\nThanks for the spec. Unfortunately, I was unable to open the "
     "attached image. Could you resend it?\n\nWe will quote by Friday.",
     "Dear Ravi,\n\nThanks for the spec.\n\nWe will quote by Friday."),
    ("Hi Ana,\n\nYour message seems to have been cut off. Could you share the "
     "rest?\n\nThe call works for me.", "Hi Ana,\n\nThe call works for me."),
    ("Hi Ana,\n\nThe PDF did not come through for me, so please send it again."
     "\n\nThanks.", "Hi Ana,\n\nThanks."),
    ("Hi,\n\nAs an AI, I cannot view images.\n\nThanks", "Hi,\n\nThanks"),
])
def test_a_claim_and_its_resend_ask_are_removed(draft: str, left: str) -> None:
    body, removed = df.strip_limit_claims(draft)
    assert body == left and removed


@pytest.mark.parametrize("draft", [
    # Review round 1, P1-b: business facts that name a file stay whole.
    "Hi,\n\nWe were unable to process the invoice because the PDF has no PO "
    "number. Could you resend it with the PO?\n\nBest",
    "Hi,\n\nAs I said in my email, the shipment was incomplete. We will send "
    "the rest on Friday.\n\nBest",
    "Hi,\n\nThe attached drawing won't open in SolidWorks 2019, so please "
    "export a STEP.\n\nBest",
    "Hi,\n\nWe use a large language model for search.\n\nBest",
    "Hi,\n\nWe cannot see the PO in the PDF.\n\nBest",
    "Hi Ana,\n\nI could not attend the review. Please open the attached file.\n\nBest,",
    "Hi Ana,\n\nCould you send the reference image you mentioned? It was not "
    "attached to either mail.\n\nBest,",
    "Hi Ana,\n\nI cannot see a reason to delay. We can open the account today.",
])
def test_a_true_sentence_stays(draft: str) -> None:
    assert df.strip_limit_claims(draft) == (draft, [])


def _resp(content: str) -> Any:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


async def test_the_drafter_removes_a_claim_it_cannot_open_a_file(monkeypatch) -> None:
    seen: list[dict[str, Any]] = []

    async def fake(*, model, messages, **kw):
        seen.append({"messages": messages, **kw})
        return _resp("Dear Ravi,\n\nI was unable to open the attached photo. "
                     "We can quote by Friday."), model

    monkeypatch.setattr("acb_llm.context.acompletion_with_fallback", fake)
    files = df.DraftFiles(texts=[("spec.pdf", "Payload 3 kg.", False)],
                          unread=[("cell.jpg", df.UNREAD_IMAGE)])
    email = {"from": "ravi@navy.test", "from_name": "Ravi", "subject": "BQ",
             "body": "Please quote. See attached.", "_files": files}
    out = await dr._llm_draft_reply(email, about="", signature="", model="tier-fast")
    assert out == "Dear Ravi,\n\nWe can quote by Friday."
    # The card names the line that went (review round 1, P1-b).
    assert any("I was unable to open the attached photo." in n for n in files.notices())
    system, user = seen[0]["messages"][0]["content"], seen[0]["messages"][1]["content"]
    assert df.NO_LIMIT_RULE.strip() in system
    assert "Payload 3 kg." in user and "File: spec.pdf" in user
    assert "content you were not given: cell.jpg" in user
    assert "task" not in seen[0]


async def test_an_image_goes_as_an_image_part_with_a_vision_task(monkeypatch) -> None:
    seen: list[dict[str, Any]] = []

    async def fake(*, model, messages, **kw):
        seen.append({"messages": messages, **kw})
        return _resp("Dear Ravi,\n\nThe cell in your photo fits."), model

    monkeypatch.setattr("acb_llm.context.acompletion_with_fallback", fake)
    files = df.DraftFiles(images=[("cell.jpg", "data:image/jpeg;base64,AAAA")])
    email = {"from": "ravi@navy.test", "from_name": "Ravi", "subject": "BQ",
             "body": "See the photo.", "_files": files}
    await dr._llm_draft_reply(email, about="", signature="", model="tier-fast")
    [call] = seen
    assert call["task"] == "vision"
    text_part, image_part = call["messages"][1]["content"]
    assert text_part["type"] == "text" and "cell.jpg" in text_part["text"]
    assert image_part == {"type": "image_url",
                          "image_url": {"url": "data:image/jpeg;base64,AAAA"}}


# ── "Draft with AI" in the Email app reads the files too ─────────────────────
#
# A NEW reply in the composer goes through the same drafter, so it gets the
# files of the mail. A text that the owner typed is improved in place, and it
# reads no file. Mutation: drop the load in ``_compose_assist_run``.


class _Step:
    def __init__(self, req: Any) -> None:
        self.phased = None
        self.about, self.signature = "", ""
        self.ctx = {"subject": "BQ", "body": "See attached.", "thread_id": "t1"}

    async def block_1(self, *a: Any) -> None:
        return None

    def body_fetch(self) -> None:
        return None

    def needs_side(self) -> bool:
        return False

    def context(self) -> dict[str, Any]:
        return self.ctx

    def sender(self, ctx: Any) -> str:
        return ""


@pytest.mark.parametrize(("body", "reads"), [("", True), ("My own words.", False)])
async def test_draft_with_ai_reads_the_files_of_a_new_reply(monkeypatch, body, reads) -> None:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def session():
        yield object()

    files = df.DraftFiles(texts=[("spec.pdf", "Payload 3 kg.", False)])
    loaded: list[str] = []

    async def load(db, mid, user, **kw):
        loaded.append(mid)
        return files

    seen: list[Any] = []

    async def compose(req, user_email, ctx, *a, **kw):
        seen.append(ctx)
        return {"draft": "x"}, ""

    monkeypatch.setattr(dr, "_ComposeStep", _Step)
    monkeypatch.setattr(dr, "_tenant_session", session)
    monkeypatch.setattr(dr, "load_draft_files", load)
    monkeypatch.setattr(dr, "_compose_draft", compose)
    req = dr.ComposeAssistRequest(account_id="a1", mode="reply", message_id="m1", body=body)
    await dr._compose_assist_run(req, USER)
    assert (loaded == ["m1"]) is reads
    assert (seen[0].get("_files") is files) is reads


async def test_a_streamed_draft_sends_no_image(monkeypatch) -> None:
    """The composer streams with no Router hop, so no model is chosen for an
    image (review round 1, P1-c). The image is named as not read."""
    seen: list[dict[str, Any]] = []

    async def fake(*, model, messages, on_delta=None, **kw):
        seen.append({"messages": messages, **kw})
        return "Dear Ravi,\n\nThanks.", model

    monkeypatch.setattr("acb_llm.context.acompletion_stream_text", fake)
    files = df.DraftFiles(images=[("cell.jpg", "data:image/jpeg;base64,AAAA")])
    email = {"from": "ravi@navy.test", "from_name": "Ravi", "subject": "BQ",
             "body": "See the photo.", "_files": files}

    async def delta(kind: str, text: str) -> None:
        return None

    await dr._llm_draft_reply(email, about="", signature="", model="tier-fast",
                              on_delta=delta)
    [call] = seen
    assert "task" not in call
    assert isinstance(call["messages"][1]["content"], str)
    assert files.unread == [("cell.jpg", df.UNREAD_IMAGE)]


async def test_a_saved_reply_draft_keeps_every_to(monkeypatch) -> None:
    """``/email/drafts/save`` writes each To on the local row too, or the
    signed send rebuilds the draft with the first one only (P1-a)."""
    from contextlib import asynccontextmanager

    row = SimpleNamespace(subject="Quote", thread_id="t1",
                          from_address={"email": "dana@fracktal.test"})

    class _Db:
        async def execute(self, *a, **kw):
            return SimpleNamespace(fetchone=lambda: row, fetchall=lambda: [])

    @asynccontextmanager
    async def session():
        yield _Db()

    made: list[dict[str, Any]] = []

    class _Provider:
        async def create_draft(self, **kw):
            made.append(kw)
            return "prov-1"

    @asynccontextmanager
    async def provider_session(*a, **kw):
        yield SimpleNamespace(authed=True, provider=_Provider(), provider_message_id="pm")

    upserts: list[dict[str, Any]] = []

    async def upsert(*a, **kw):
        upserts.append(kw)
        return "local-1"

    async def nothing(*a, **kw):
        return ""

    async def two(*a, **kw):
        return ["ravi@contoso.test", "priya@contoso.test"]

    monkeypatch.setattr(dr, "_tenant_session", session)
    monkeypatch.setattr(dr, "_assert_account_owner", nothing)
    monkeypatch.setattr(dr, "_account_signature", nothing)
    monkeypatch.setattr(dr, "provider_session", provider_session)
    monkeypatch.setattr(dr, "_reply_to_list", two)
    monkeypatch.setattr(dr, "_upsert_local_draft", upsert)

    async def me(*a, **kw):
        return SimpleNamespace(address="dana@fracktal.test")

    monkeypatch.setattr(dr, "resolve_self", me)
    out = await dr.save_draft(
        dr.SaveDraftRequest(account_id="a1", message_id="m1", body="Hi"), USER)
    assert out == {"created": True, "id": "local-1", "kept_draft": False}
    assert made[0]["to"] == ["ravi@contoso.test", "priya@contoso.test"]
    assert upserts[0]["to_addresses"] == ["ravi@contoso.test", "priya@contoso.test"]


# ── H-377: a rule draft reads the files of the mail ──────────────────────────
#
# A rule runs with no member session. ``_rule_draft_files`` builds the member
# from the OWNER of the mailbox and the tenant that the job bound, never from
# request input (R5). Mutation: drop the call in ``_apply_rule_actions``, or
# read with no bound tenant.


async def test_a_rule_draft_reads_the_files_as_the_mailbox_owner(monkeypatch) -> None:
    from acb_common.db import bind_tenant, release_tenant
    from gateway.routes.email.automation import actions

    seen: list[Any] = []

    async def load(db, mid, user, **kw):
        seen.append((mid, user, kw))
        return df.DraftFiles(texts=[("spec.pdf", "Payload 3 kg.", False)])

    monkeypatch.setattr(df, "load_draft_files", load)
    token = bind_tenant("00000000-0000-4000-8000-0000000000b2")
    try:
        files = await actions._rule_draft_files(object(), "m1", "dana@fracktal.test")
    finally:
        release_tenant(token)
    [(mid, user, kw)] = seen
    assert mid == "m1" and user.email == "dana@fracktal.test"
    assert user.organization_id == "00000000-0000-4000-8000-0000000000b2"
    assert kw["budget_s"] == actions.RULE_FILES_BUDGET_S
    assert files.texts[0][0] == "spec.pdf"


async def test_a_rule_draft_with_no_tenant_reads_nothing(monkeypatch) -> None:
    from gateway.routes.email.automation import actions

    async def load(*a, **kw):
        raise AssertionError("no read with no bound tenant")

    monkeypatch.setattr(df, "load_draft_files", load)
    assert await actions._rule_draft_files(object(), "m1", "dana@fracktal.test") is None
    assert await actions._rule_draft_files(object(), "m1", "") is None


async def test_the_rule_path_hands_the_files_to_the_drafter(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from gateway.routes.email.automation import actions

    files = df.DraftFiles(texts=[("spec.pdf", "Payload 3 kg.", False)])
    got: list[Any] = []

    async def build(*a, **kw):
        return {"from": "ravi@navy.test", "subject": "BQ", "body": "See attached.",
                "thread_id": ""}

    async def rule_files(db, mid, owner):
        return files

    async def drafter(email, *a, **kw):
        got.append(email)
        return "Dear Ravi,\n\nThanks."

    async def no(*a, **kw):
        return False

    monkeypatch.setattr(actions, "_build_reply_context", build)
    monkeypatch.setattr(actions, "_rule_draft_files", rule_files)
    monkeypatch.setattr(actions, "_agent_draft_reply", drafter)
    monkeypatch.setattr(actions, "_skip_for_paired_mailbox", no)
    db, provider = AsyncMock(), AsyncMock()
    db.execute.return_value.fetchone = lambda: None
    await actions._apply_rule_actions(
        db, provider, "m1", "pm1", [{"type": "DRAFT_EMAIL", "to_address": "ravi@navy.test"}],
        {"from": "ravi@navy.test", "subject": "BQ", "body": "See attached.", "thread_id": ""},
        user_email="dana@fracktal.test", account_id="",
    )
    assert got and got[0]["_files"] is files
