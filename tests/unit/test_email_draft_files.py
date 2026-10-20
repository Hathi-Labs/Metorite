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
    got = await df.load_draft_files(None, "m1", USER, vision=False)
    [(name, text, cut)] = got.texts
    assert name == "spec.pdf" and text.startswith("Payload 3 kg.")
    assert len(text) == df.MAX_FILE_CHARS and cut is True
    assert got.unread == [] and got.notices() == []


async def test_an_image_is_not_read_with_the_switch_off(monkeypatch) -> None:
    read = _files(monkeypatch, [_row("cell.jpg", "image/jpeg", aid="i1"),
                                _row("logo.png", "image/png", size=3_000, aid="i2")])
    got = await df.load_draft_files(None, "m1", USER, vision=False)
    assert read == []
    assert got.images == [] and got.unread == [("cell.jpg", df.UNREAD_IMAGE)]
    [note] = got.notices()
    assert "cell.jpg (an image)" in note and "logo.png" not in note


async def test_an_image_goes_to_the_model_with_the_switch_on(monkeypatch) -> None:
    _files(monkeypatch, [_row("cell.jpg", "image/jpeg", aid="i1")])
    got = await df.load_draft_files(None, "m1", USER, vision=True)
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
    got = await df.load_draft_files(None, "m1", USER, budget_s=0.2, vision=False)
    assert got.unread == [("big.pdf", df.UNREAD_SLOW)]


async def test_no_member_session_reads_nothing(monkeypatch) -> None:
    read = _files(monkeypatch, [_row("spec.pdf", "application/pdf", aid="a1")])
    got = await df.load_draft_files(None, "m1", None, vision=False)
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
