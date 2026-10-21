"""The reply drafter fits a long mail to the window of its tier (owner, 2026-10-20).

"Be mindful about the tier that we are using for drafting. If the length of
the email chain or the attachments are too large, then that has to be taken
into account."

Each rule names the mutation that turns it red (R7):

1. **A mail that fits changes nothing.** Mutation: always cut ->
   ``test_a_mail_that_fits_is_not_touched``.
2. **A mail that does not fit moves to a tier with a LARGER window**, never a
   smaller one. Mutation: take the first tier in the list ->
   ``test_a_long_mail_moves_to_a_larger_window``.
3. **With no tier that fits, the least needed parts go first**: the examples,
   then the memories, then the OLDEST thread, then the end of each file, and
   the latest message last. Mutation: cut the thread from its end ->
   ``test_the_newest_thread_stays``.
4. **The latest message is the last block the model reads**, after the files,
   and the generic fitter never cuts it. Mutation: put the files after it ->
   ``test_the_drafter_keeps_the_latest_message_whole_and_last``.
5. **The member's card names what the draft did not see.**
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from gateway.routes.email.automation import draft_budget as db
from gateway.routes.email.automation import draft_files as df
from gateway.routes.email.automation import drafting as dr


def _windows(**w: int):
    return lambda m: w[m.replace("-", "_")]


def _parts(**kw: Any) -> db.DraftParts:
    base = dict(latest="Please quote three BQs by Friday.", thread="", files=[],
                memories="", examples="", voice="")
    base.update(kw)
    return db.DraftParts(**base)


def test_a_mail_that_fits_is_not_touched() -> None:
    parts = _parts(thread="t" * 1000, files=[("spec.pdf", "s" * 1000, False)])
    plan = db.plan_draft("tier-fast", parts, fixed_chars=5000,
                         larger=("tier-powerful",),
                         window_of=_windows(tier_fast=128_000, tier_powerful=1_000_000))
    assert plan.model == "tier-fast" and plan.parts is parts and plan.left_out == []


def test_a_long_mail_moves_to_a_larger_window() -> None:
    parts = _parts(thread="t" * 90_000, files=[("spec.pdf", "s" * 20_000, False)])
    plan = db.plan_draft(
        "tier-fast", parts, fixed_chars=5000,
        larger=("tier-balanced", "tier-powerful"),
        window_of=_windows(tier_fast=32_000, tier_balanced=16_000, tier_powerful=128_000))
    assert plan.model == "tier-powerful" and plan.escalated
    assert plan.left_out == [] and plan.parts.thread == parts.thread


def test_the_newest_thread_stays() -> None:
    old, new = "OLDEST MAIL. " * 3000, "NEWEST MAIL. " * 300
    parts = _parts(thread=old + new, examples="e" * 2000, voice="v" * 2000,
                   memories="m" * 2000)
    plan = db.plan_draft("tier-fast", parts, fixed_chars=5000,
                         window_of=_windows(tier_fast=16_000))
    fit = plan.parts
    assert fit.examples == "" and fit.voice == "" and len(fit.memories) == 600
    kept = fit.thread.split("\n", 1)[1]
    assert fit.thread.endswith(new) and (old + new).endswith(kept)
    assert len(kept) < len(old + new)
    assert fit.thread.startswith("[The oldest messages of this thread are not shown.]")
    assert fit.latest == parts.latest
    assert "the oldest messages of the thread" in plan.left_out


def test_files_are_cut_before_the_latest_message() -> None:
    latest = "L" * 20_000
    parts = _parts(latest=latest, files=[("a.pdf", "a" * 30_000, False),
                                         ("b.pdf", "b" * 3000, False)])
    plan = db.plan_draft("tier-fast", parts, fixed_chars=2000,
                         window_of=_windows(tier_fast=16_000))
    files = dict((n, (t, c)) for n, t, c in plan.parts.files)
    assert len(files["a.pdf"][0]) >= db.MIN_FILE_CHARS and files["a.pdf"][1] is True
    assert "the end of a.pdf" in plan.left_out
    # Within the room the latest message keeps its head, and the cut is said.
    assert plan.parts.latest.startswith("L" * db.MIN_LATEST_CHARS)


def test_a_language_of_one_token_a_character_is_measured() -> None:
    """Chinese, Japanese or Hindi text is about a token for each character.
    The plan counts THIS mail, so it cuts where 3 characters a token would
    not (review of #1203, P2). Mutation: drop ``_measured_ratio``."""
    parts = _parts(latest="\u8bf7" * 5000, thread="\u65e7" * 200_000)
    plan = db.plan_draft("tier-fast", parts, fixed_chars=5000,
                         window_of=_windows(tier_fast=131_072),
                         count_tokens=lambda text, model: len(text))
    assert plan.left_out and len(plan.parts.thread) < 130_000
    english = db.plan_draft("tier-fast", parts, fixed_chars=5000,
                            window_of=_windows(tier_fast=131_072),
                            count_tokens=lambda text, model: len(text) // 4)
    assert english.left_out == []


def test_a_window_that_cannot_be_read_counts_as_small() -> None:
    def broken(m: str) -> int:
        raise RuntimeError("no model")

    plan = db.plan_draft("tier-x", _parts(thread="t" * 200_000), fixed_chars=0,
                         window_of=broken)
    assert plan.left_out and len(plan.parts.thread) < 200_000


def test_no_prompt_passes_the_ceiling_on_a_huge_window() -> None:
    room = db.budget_chars(1_000_000, fixed_chars=0)
    assert room == db.MAX_PROMPT_TOKENS * db.CHARS_PER_TOKEN


def test_the_card_names_what_the_draft_did_not_see() -> None:
    files = df.DraftFiles(left_out=["the oldest messages of the thread"])
    [note] = files.notices()
    assert "did not see the oldest messages of the thread" in note


# ── 4. The drafter ───────────────────────────────────────────────────────────


def _resp(content: str) -> Any:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


async def test_the_drafter_keeps_the_latest_message_whole_and_last(monkeypatch) -> None:
    seen: list[dict[str, Any]] = []

    async def fake(*, model, messages, **kw):
        seen.append({"model": model, "messages": messages})
        return _resp("Dear Ravi,\n\nThanks."), model

    monkeypatch.setattr("acb_llm.context.acompletion_with_fallback", fake)
    monkeypatch.setattr(db, "_window_of", lambda m: {"tier-fast": 20_000}.get(m, 8_000))
    latest = "Please send three BQs by Friday. " * 40
    files = df.DraftFiles(texts=[("spec.pdf", "Payload 3 kg. " * 3000, False)])
    email = {"from": "ravi@navy.test", "from_name": "Ravi", "subject": "BQ",
             "body": latest, "thread": "Older mail. " * 4000, "_files": files}
    await dr._llm_draft_reply(email, about="", signature="", model="tier-fast")
    [call] = seen
    user = call["messages"][1]["content"]
    assert latest.strip() in user
    assert user.index("File: spec.pdf") < user.index("Latest message")
    assert "To fit, these parts are not shown:" in user
    assert files.left_out and "did not see" in " ".join(files.notices())
