"""The question card never cuts a word, and never drops text silently.

Owner report, 2026-10-13: the ``ask_questions`` card showed "…should be in
the same plac". Both paths that build the card cut the question at 200
characters with ``str[:200]``: the tool itself (``acb_skills.ask_tools``) and
the executor's bridge (``orchestrator.executor``). Now both call
``clip_words``: a long text is cut at a word, with an ellipsis, and the
question may run to 1000 characters.

Mutations each test catches are named in its docstring.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import orchestrator.executor as ex
from acb_skills import ask_tools
from acb_skills.ask_tools import (
    ELLIPSIS,
    QUESTION_TEXT_MAX,
    ask_questions,
    clip_words,
)

ROOT = Path(__file__).resolve().parents[2]

#: The owner's question, longer than the old cap of 200.
OWNER_Q = (
    "Do you want me to archive #22 together with #23? Both ask for the same change: "
    "#22 «Move the assistant button to the top bar» and #23 «The assistant button "
    "should be in the same place on every page, so a member can always find it»."
)


def test_a_text_that_fits_comes_back_whole() -> None:
    """Mutation caught: the cap goes back to 200 (the owner's text is 230)."""
    assert len(OWNER_Q) > 200
    assert clip_words(OWNER_Q, QUESTION_TEXT_MAX) == OWNER_Q


def test_a_long_text_is_cut_at_a_word_with_an_ellipsis() -> None:
    """Mutation caught: ``text[:limit]``, which cuts inside a word."""
    out = clip_words(OWNER_Q, 120)
    assert len(out) <= 120
    assert out.endswith(ELLIPSIS)
    body = out[: -len(ELLIPSIS)]
    # The kept text is a run of whole words of the original.
    assert OWNER_Q.startswith(body)
    assert OWNER_Q[len(body)] in " ,."
    assert not body.endswith(" ")


def test_a_limit_at_the_end_of_a_word_keeps_that_word() -> None:
    """Mutation caught: the cut always goes back to the space before."""
    assert clip_words("alpha beta gamma delta", 12) == "alpha beta" + ELLIPSIS
    assert clip_words("alpha beta gamma delta", 17) == "alpha beta gamma" + ELLIPSIS


def test_one_long_token_is_cut_at_the_limit_with_the_mark() -> None:
    out = clip_words("x" * 300, 50)
    assert out == "x" * 49 + ELLIPSIS


def test_blank_stays_blank() -> None:
    assert clip_words("   ", 10) == ""
    assert clip_words(None, 10) == ""  # type: ignore[arg-type]


def test_the_tool_sends_the_whole_question() -> None:
    """Path A of ``ask_questions``: the card it pushes holds the whole text.

    Mutation caught: ``str(q.get("question")).strip()[:200]`` comes back.
    """

    async def run() -> dict:
        queue: asyncio.Queue = asyncio.Queue()
        token = ex._active_run_queue.set(queue)
        try:
            task = asyncio.create_task(ask_questions(json.dumps({"questions": [{
                "header": "#23 with #22?",
                "question": OWNER_Q,
                "options": [{"label": "Archive both #22 and #23", "recommended": True}, {"label": "Archive only #22"}],
            }]})))
            event = await asyncio.wait_for(queue.get(), 5)
            rid = event["value"]["request_id"]
            # Pop the Future, as `resolve_user_input` does. Left in the
            # shared registry, it broke `test_ask_user_hitl.py`, which reads
            # the first pending id (CI, 2026-10-13).
            ex._pending_user_input.pop(rid).set_result({"answer": "Archive both #22 and #23"})
            assert await task == "User response: Archive both #22 and #23"
            return event
        finally:
            ex._active_run_queue.reset(token)

    event = asyncio.run(run())
    q = event["value"]["questions"][0]
    assert q["question"] == OWNER_Q
    assert q["header"] == "#23 with #22?"
    assert q["options"][0] == {"label": "Archive both #22 and #23", "description": None, "recommended": True}


def test_both_paths_cut_with_the_one_function() -> None:
    """The executor's bridge builds the card too. It must cut the same way.

    Mutation caught: the bridge goes back to ``.strip()[:200]``.
    """
    src = (ROOT / "apps/services/orchestrator/orchestrator/executor.py").read_text(encoding="utf-8")
    bridge = src[src.index('if _tc_name == "ask_questions":'):]
    bridge = bridge[: bridge.index('"elicitation_requested"')]
    assert "_clip_words(" in bridge
    assert "[:200]" not in bridge and "[:50]" not in bridge
    tool = inspect.getsource(ask_tools.ask_questions)
    assert "clip_words(" in tool
    assert "[:200]" not in tool and "[:100]" not in tool
