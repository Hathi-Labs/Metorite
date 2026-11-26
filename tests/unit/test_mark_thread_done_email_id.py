"""Task 2873: the chat marks a thread done from the email id it has.

On 2026-11-11 the member said "mark the email as done" twice, and
``mark_thread_done`` refused both times. Every read tool (find_priority,
query_inbox, read_email, read_thread) shows EMAIL ids, and the tool looked up
the value as a thread id only. Now the email decides its thread and its
mailbox, as in ``read_thread`` (EM-T8e-2), and a thread id that names no
thread is tried once as an email id.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_AGENT = (
    Path(__file__).resolve().parents[2]
    / "apps" / "agents" / "agent-email-assistant" / "agents.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("ea_mark_done", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load()

BOX = "acc-1"
OTHER_BOX = "acc-2"
EMAIL = "e-7"          # an email id, as find_priority shows it
THREAD = "t-7"         # its thread


@pytest.fixture(autouse=True)
def _open_run() -> None:
    from acb_skills.write_artifact import bind_artifact_context

    bind_artifact_context(no_egress=False)


class Fake:
    def __init__(self, email_box: str = BOX) -> None:
        self.email_box = email_box
        self.gets: list[tuple[str, dict[str, Any] | None]] = []
        self.posts: list[tuple[str, Any]] = []
        self.cards: list[str] = []

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.gets.append((path, params))
        if path == f"/email/messages/{EMAIL}":
            return {"id": EMAIL, "thread_id": THREAD, "account_id": self.email_box}
        if path.startswith("/email/messages/"):
            raise RuntimeError("404")
        if path == "/email/messages":
            params = params or {}
            if (params.get("thread_id") == THREAD
                    and params.get("account_id") == self.email_box):
                return {"emails": [{"id": EMAIL, "subject": "Salary approval"}]}
            return {"emails": []}
        return {}

    async def post(self, path: str, body: Any = None, *a: Any, **k: Any) -> Any:
        self.posts.append((path, body))
        return {}

    async def refusal(self, title: str, *a: Any, **k: Any) -> str | None:
        self.cards.append(title)
        return None  # the member approved the card


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    f = Fake()
    monkeypatch.setattr(agents, "_get", f.get)
    monkeypatch.setattr(agents, "_post", f.post)
    monkeypatch.setattr(agents, "_card_refusal", f.refusal)

    async def named(account_id: str) -> str:
        return f"box {account_id}"

    monkeypatch.setattr(agents, "_named", named)
    return f


async def test_an_email_id_marks_its_thread_done(fake: Fake) -> None:
    out = await agents.mark_thread_done(BOX, email_id=EMAIL)
    assert out == "Thread marked done."
    assert fake.cards == ['Mark the thread "Salary approval" done in box acc-1?']
    assert fake.posts == [("/email/reply-zero/resolve",
                           {"account_id": BOX, "thread_id": THREAD, "done": True})]


async def test_an_email_id_passed_as_the_thread_id_still_works(fake: Fake) -> None:
    # The 2026-11-11 call: thread_id held the email id from find_priority.
    out = await agents.mark_thread_done(BOX, EMAIL)
    assert out == "Thread marked done."
    assert fake.posts == [("/email/reply-zero/resolve",
                           {"account_id": BOX, "thread_id": THREAD, "done": True})]


async def test_a_real_thread_id_works_as_before(fake: Fake) -> None:
    out = await agents.mark_thread_done(BOX, THREAD, done=False)
    assert out == "Thread reopened."
    assert fake.posts[-1][1] == {"account_id": BOX, "thread_id": THREAD, "done": False}
    # No email lookup: the thread was found at once.
    assert not any(p.startswith("/email/messages/") for p, _ in fake.gets)


async def test_the_email_decides_the_mailbox(monkeypatch: pytest.MonkeyPatch, fake: Fake) -> None:
    fake.email_box = OTHER_BOX
    out = await agents.mark_thread_done(BOX, email_id=EMAIL)
    assert out == "Thread marked done."
    assert fake.posts[-1][1]["account_id"] == OTHER_BOX


async def test_an_unknown_id_writes_nothing_and_names_a_step_that_works(fake: Fake) -> None:
    out = await agents.mark_thread_done(BOX, "nope")
    assert out.startswith("Nothing changed.")
    assert "email_id" in out and "find_priority" in out
    assert fake.posts == [] and fake.cards == []


async def test_no_id_at_all_writes_nothing(fake: Fake) -> None:
    out = await agents.mark_thread_done(BOX)
    assert out.startswith("Nothing changed.")
    assert fake.posts == [] and fake.cards == []
