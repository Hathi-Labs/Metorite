"""The reply drafter's input, fitted to the window of the tier that writes it.

Owner, 2026-10-20: "be mindful about the tier that we are using for drafting.
If the length of the email chain or the attachments are too large, then that
has to be taken into account."

Before this, each part of the prompt had a fixed cap (the mail 100,000
characters, the thread 60,000, the files 30,000), and the generic fitter
(``acb_llm.context.fit_messages_to_context``) cut the MIDDLE of the longest
message when the whole did not fit. The whole prompt is one message, so a cut
could fall on the mail that the reply answers. A tier's model is a setting
(Settings → Models, and the Router), so a window that fits today can shrink.

:func:`plan_draft` decides it before the call:

1. It reads the window of the tier now (``context_window_for``), and keeps
   room for the reply and a margin. A conservative 3 characters for each
   token covers a language that tokenizes worse than English.
2. When everything fits, nothing changes.
3. When it does not fit, it tries the other email tiers with a LARGER window,
   in the order given, and takes the first that fits everything. A long mail
   then costs a larger model, not a worse reply.
4. When no tier fits, it takes the largest window and cuts by priority, the
   least needed first: past replies and sent examples, then the learned
   memories, then the OLDEST messages of the thread, then the end of each
   file. The latest message is cut last, and only at its end.
5. It names each part that it left out. The prompt tells the model the parts
   are not shown (and ``NO_LIMIT_RULE`` stops it from saying so to the
   sender). The member's card says what the draft did not see.

Fence: ``tests/unit/test_email_draft_budget.py``.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

#: Characters for each token, on the safe side (English is about 4).
CHARS_PER_TOKEN = 3
#: The tokens kept for the reply, as the drafter asks for (``max_tokens``).
REPLY_TOKENS = 3000
#: The tokens kept for the envelope and a miscount.
MARGIN_TOKENS = 2048
#: The tokens that one image costs, about.
IMAGE_TOKENS = 1600
#: The most prompt tokens one draft may use, whatever the window. A draft is
#: a reply, and a million-token window does not make a 900,000-token prompt
#: a good idea: it costs credits and time.
MAX_PROMPT_TOKENS = 120_000
#: The least of the latest message that a cut keeps.
MIN_LATEST_CHARS = 4000
#: The least of one file that a cut keeps.
MIN_FILE_CHARS = 1500


# ── The tier by the size of the mail (owner, 2026-10-22) ─────────────────────
#
# "Use tier fast or balanced for regular things, and switch to powerful when
# there's a lot of context or a long thread." The drafter's own tier stays
# for a short mail. A mail with files, or with some length, takes at least
# balanced. A long thread or a lot of context takes powerful. :func:`plan_draft`
# then still checks the window, so a tier that cannot hold the mail is never
# used: fitting the mail wins over the strength of the tier.

#: The ladder of the drafting tiers, weakest first.
DRAFT_LADDER: tuple[str, ...] = ("tier-fast", "tier-balanced", "tier-powerful")
#: Characters of mail (thread, latest message and files) for tier-balanced.
BALANCED_CHARS = 12_000
#: Characters of mail for tier-powerful: about 20,000 tokens.
POWERFUL_CHARS = 60_000


def tier_for_size(model: str, chars: int, *, has_files: bool = False) -> str:
    """*model*, raised by the size of the mail, never lowered. A tier off the
    ladder (an admin's ``provider/model``) stays."""
    if model not in DRAFT_LADDER:
        return model
    want = ("tier-powerful" if chars >= POWERFUL_CHARS
            else "tier-balanced" if (chars >= BALANCED_CHARS or has_files)
            else model)
    return DRAFT_LADDER[max(DRAFT_LADDER.index(model), DRAFT_LADDER.index(want))]


@dataclass
class DraftParts:
    """The parts of the drafter's prompt that can be cut, in characters."""

    latest: str = ""
    thread: str = ""
    files: list[tuple[str, str, bool]] = field(default_factory=list)
    memories: str = ""
    examples: str = ""
    voice: str = ""

    def size(self) -> int:
        return (len(self.latest) + len(self.thread) + len(self.memories)
                + len(self.examples) + len(self.voice)
                + sum(len(n) + len(t) + 20 for n, t, _c in self.files))


@dataclass
class DraftPlan:
    """The tier that writes the draft, the parts it gets, and what went."""

    model: str
    parts: DraftParts
    #: Each part left out, in words: "the oldest messages of the thread".
    left_out: list[str] = field(default_factory=list)
    #: True when the plan moved to a tier with a larger window.
    escalated: bool = False


def budget_chars(window_tokens: int, *, fixed_chars: int, images: int = 0,
                 chars_per_token: float = CHARS_PER_TOKEN) -> int:
    """The characters that the cuttable parts may take on a window."""
    tokens = min(window_tokens, MAX_PROMPT_TOKENS + REPLY_TOKENS + MARGIN_TOKENS)
    tokens -= REPLY_TOKENS + MARGIN_TOKENS + images * IMAGE_TOKENS
    return max(0, int(tokens * chars_per_token) - fixed_chars)


#: The characters of the mail that :func:`_measured_ratio` counts.
_SAMPLE_CHARS = 60_000


def _count_tokens(text: str, model: str) -> int:
    from acb_llm.context import count_message_tokens  # noqa: PLC0415

    return int(count_message_tokens([{"role": "user", "content": text}], model))


def _measured_ratio(parts: DraftParts, model: str,
                    count: Callable[[str, str], int] | None) -> float:
    """Characters for each token of THIS mail, never more than the default.

    Chinese, Japanese or Hindi text is about one token for each character,
    so 3 would let a mail through that the model cannot take, and the
    generic fitter would then cut the middle of the prompt (review of #1203,
    P2). A sample of the mail is counted with the tokenizer of the model."""
    sample = "\n".join([parts.latest, parts.thread,
                        *(t for _n, t, _c in parts.files)])[:_SAMPLE_CHARS]
    if len(sample) < 2000:
        return CHARS_PER_TOKEN
    try:
        tokens = (count or _count_tokens)(sample, model)
    except Exception:  # noqa: BLE001 - the default is the safe side for English
        return CHARS_PER_TOKEN
    if tokens <= 0:
        return CHARS_PER_TOKEN
    return max(0.5, min(CHARS_PER_TOKEN, len(sample) / tokens))


def _window_of(model: str) -> int:
    from acb_llm.context import context_window_for  # noqa: PLC0415

    return int(context_window_for(model))


def plan_draft(
    model: str,
    parts: DraftParts,
    *,
    fixed_chars: int,
    images: int = 0,
    larger: Sequence[str] = (),
    window_of: Callable[[str], int] | None = None,
    count_tokens: Callable[[str, str], int] | None = None,
) -> DraftPlan:
    """Fit *parts* to the window of *model*, or of a tier in *larger*.

    *fixed_chars* is the prompt that is never cut: the system prompt, the
    member's context, the head of the mail and the instructions. Never
    raises: a window that cannot be read counts as 32,000 tokens."""

    read = window_of or _window_of

    def window(m: str) -> int:
        try:
            return max(1, read(m))
        except Exception:  # noqa: BLE001 - a draft still writes
            return 32_000

    need = parts.size()
    ratio = _measured_ratio(parts, model, count_tokens)

    def room_of(w: int) -> int:
        return budget_chars(w, fixed_chars=fixed_chars, images=images,
                            chars_per_token=ratio)

    own = window(model)
    if need <= room_of(own):
        return DraftPlan(model, parts)
    best_model, best_window = model, own
    for other in larger:
        if not other or other == model:
            continue
        w = window(other)
        if w <= best_window:
            continue
        if need <= room_of(w):
            return DraftPlan(other, parts, escalated=True)
        best_model, best_window = other, w
    room = room_of(best_window)
    cut, left_out = _cut(parts, room)
    return DraftPlan(best_model, cut, left_out, escalated=best_model != model)


def _cut(parts: DraftParts, room: int) -> tuple[DraftParts, list[str]]:
    """*parts* cut to *room* characters, the least needed first."""
    out = replace(parts, files=list(parts.files))
    left: list[str] = []

    def over() -> int:
        return out.size() - room

    # 1. What only shapes the voice.
    for name, words in (("examples", "the past replies to this sender"),
                        ("voice", "the examples of how you write")):
        if over() > 0 and getattr(out, name):
            setattr(out, name, "")
            left.append(words)
    # 2. The learned memories, to a short head.
    if over() > 0 and len(out.memories) > 600:
        out.memories = out.memories[:600]
        left.append("part of the learned memories")
    # 3. The oldest messages of the thread: the newest stay.
    if over() > 0 and out.thread:
        keep = max(0, len(out.thread) - over())
        out.thread = ("[The oldest messages of this thread are not shown.]\n"
                      + out.thread[len(out.thread) - keep:]) if keep else ""
        left.append("the oldest messages of the thread" if keep else "the earlier thread")
    # 4. The end of each file, the largest first, down to a floor each.
    if over() > 0 and out.files:
        files = list(out.files)
        for i in sorted(range(len(files)), key=lambda j: -len(files[j][1])):
            if over() <= 0:
                break
            name, text, was_cut = files[i]
            keep = max(MIN_FILE_CHARS, len(text) - over())
            if keep < len(text):
                files[i] = (name, text[:keep], True)
                left.append(f"the end of {name}")
            out.files = files
        out.files = files
    # 5. Last, the end of the latest message.
    if over() > 0 and len(out.latest) > MIN_LATEST_CHARS:
        keep = max(MIN_LATEST_CHARS, len(out.latest) - over())
        out.latest = out.latest[:keep] + "\n[The rest of this message is not shown.]"
        left.append("the end of the latest message")
    return out, left
