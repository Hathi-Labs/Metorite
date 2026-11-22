"""present_email_groups: the agent-driven categorized email board.

The tool takes the agent's JSON grouping ({title, email_ids, note?}), hydrates
each row's label via one batched summaries lookup, and emits a parseable board:
a "## <title> (<n>)" header per group over "• id=… | sender: subject" rows.
These lock that contract (the frontend EmailGroupsCard parses exactly this).
"""
from __future__ import annotations

import contextlib
import importlib.util
import inspect
import json
from pathlib import Path

import pytest

_AGENT = (
    Path(__file__).resolve().parents[2]
    / "apps" / "agents" / "agent-email-assistant" / "agents.py"
)


def _load_agents():
    spec = importlib.util.spec_from_file_location("email_assistant_agents", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load_agents()


@pytest.fixture()
def fake_summaries(monkeypatch):
    """Stub the gateway summaries POST with a fixed id → label map."""
    meta = {
        "a1": {"id": "a1", "from": "HR Team", "subject": "Onboarding"},
        "b2": {"id": "b2", "from": "Payroll", "subject": "Payslip"},
        "c3": {"id": "c3", "from": "Lab", "subject": "Test results"},
    }

    async def fake_post(path, body):
        assert path == "/email/messages/summaries"
        return {"summaries": [meta[i] for i in body["ids"] if i in meta]}

    monkeypatch.setattr(agents, "_post", fake_post)
    return meta


async def test_present_groups_emits_parseable_board(fake_summaries) -> None:
    out = await agents.present_email_groups(
        '[{"title": "HR", "email_ids": ["a1"], "note": "onboarding"},'
        ' {"title": "Finance", "email_ids": ["b2"]},'
        ' {"title": "R&D", "email_ids": ["c3"]}]'
    )
    lines = out.splitlines()
    assert lines[0] == "Categorized emails — 3 across 3 group(s):"
    assert "## HR (1) — onboarding" in lines
    assert "## Finance (1)" in lines
    assert "## R&D (1)" in lines
    assert "• id=a1 | HR Team: Onboarding" in lines
    assert "• id=b2 | Payroll: Payslip" in lines


async def test_present_groups_dedupes_ids_across_groups(fake_summaries) -> None:
    # b2 appears in two groups — it stays only in the first (one category each).
    out = await agents.present_email_groups(
        '[{"title": "First", "email_ids": ["a1", "b2"]},'
        ' {"title": "Second", "email_ids": ["b2", "c3"]}]'
    )
    assert out.count("id=b2") == 1
    assert "## First (2)" in out
    assert "## Second (1)" in out  # b2 dropped from the second group


async def test_present_groups_renders_unknown_ids(monkeypatch) -> None:
    # A lookup that returns nothing must still render a row (never silently drop).
    async def empty_post(path, body):
        return {"summaries": []}

    monkeypatch.setattr(agents, "_post", empty_post)
    out = await agents.present_email_groups('[{"title": "X", "email_ids": ["z9"]}]')
    assert "id=z9" in out
    assert "## X (1)" in out


async def test_present_groups_rejects_bad_json() -> None:
    out = await agents.present_email_groups("not json")
    assert "Couldn't parse" in out


async def test_present_groups_empty_is_handled() -> None:
    assert "No groups" in await agents.present_email_groups("[]")


# ── Task 3863: the model copies the list label with the id ──────────────────
#
# R7 fences: ``email-id-label-present-groups`` (the board) and
# ``email-id-label-tools`` (each id-taking tool strips ``id=`` before its
# route) here, and ``email-id-label-summaries-route`` in
# ``test_email_summaries_ids.py``.

_UID = "41c0e817-fb4a-4e45-870a-2da9a953bf4b"
_UID2 = "5b2f1c9e-0000-4000-8000-000000000002"


async def test_present_groups_strips_the_id_label(monkeypatch) -> None:
    sent: list[dict] = []

    async def fake_post(path, body):
        sent.append(body)
        return {"summaries": [{"id": _UID, "from": "HR Team", "subject": "Onboarding"}]}

    monkeypatch.setattr(agents, "_post", fake_post)
    out = await agents.present_email_groups(
        '[{"title": "HR", "email_ids": ["id=' + _UID + '", "ID= ' + _UID2 + '"]}]'
    )
    assert sent == [{"ids": [_UID, _UID2]}]
    assert f"• id={_UID} | HR Team: Onboarding" in out.splitlines()
    assert "id=id=" not in out
    # The same id with and without the label is one id.
    out2 = await agents.present_email_groups(
        '[{"title": "A", "email_ids": ["id=' + _UID + '", "' + _UID + '"]}]'
    )
    assert out2.count(f"id={_UID}") == 1


def test_clean_email_id_changes_only_the_label() -> None:
    clean = agents._clean_email_id
    assert clean(f"id={_UID}") == _UID
    assert clean(f' "ID = {_UID}" ') == _UID
    assert clean(_UID) == _UID
    assert clean("email:" + _UID) == "email:" + _UID  # the HRD-2 prefix is _email_ref's
    assert clean(None) == ""
    assert agents._clean_email_ids([f"id={_UID}", "", "  "]) == [_UID]


def test_docstring_says_pass_the_id_not_the_label() -> None:
    doc = agents.present_email_groups.__doc__ or ""
    assert "the value after ``id=``" in doc
    assert "the ``id=…`` values" not in doc


_ID_TOOLS = [
    ("read_email", {}),
    ("read_email_attachment", {"attachment": "a.pdf"}),
    ("read_thread", {}),
    ("draft_reply", {"account_id": "acc"}),
    ("snooze_email", {}),
    ("forward_email", {"to": ["x@example.com"]}),
    ("compose_reply", {"account_id": "acc"}),
    ("run_rules", {"account_id": "acc", "scope": "message"}),
    ("test_rule_match", {"account_id": "acc"}),
    ("mark_thread_done", {"account_id": "acc"}),
]


def _recorders(monkeypatch):
    seen: list[str] = []

    async def fake_get(path, params=None):
        seen.append(path + " " + json.dumps(params, default=str))
        return {}

    async def fake_write(path, body=None, *a, **k):
        seen.append(path + " " + json.dumps(body, default=str))
        return {}

    monkeypatch.setattr(agents, "_get", fake_get)
    for name in ("_post", "_patch", "_put", "_delete"):
        monkeypatch.setattr(agents, name, fake_write)

    async def one_box(account_id, tool_name):
        return account_id or "acc", ""

    async def no_accounts():
        return []

    monkeypatch.setattr(agents, "_one_mailbox", one_box)
    from acb_skills import ask_tools

    monkeypatch.setattr(ask_tools, "reply_card_channel_open", lambda: True)
    monkeypatch.setattr(agents, "_accounts", no_accounts)
    return seen


def _bare(name):
    fn = getattr(agents, name)
    return getattr(fn, "__wrapped__", fn)


@pytest.mark.parametrize("tool,extra", _ID_TOOLS)
async def test_each_id_tool_strips_the_label_before_its_route(
    monkeypatch, tool, extra
) -> None:
    seen = _recorders(monkeypatch)
    with contextlib.suppress(Exception):  # a later step may fail on the empty fakes
        await _bare(tool)(email_id=f"id={_UID}", **extra)
    text = " ".join(seen)
    assert _UID in text, (tool, seen)
    assert "id=" + _UID not in text and "id%3D" not in text, (tool, seen)


async def test_manage_inbox_strips_the_label(monkeypatch) -> None:
    seen = _recorders(monkeypatch)
    with contextlib.suppress(Exception):  # a later step may fail on the empty fakes
        await _bare("manage_inbox")(action="trash", message_ids=[f"id={_UID}"])
    text = " ".join(seen)
    assert _UID in text, seen
    assert "id=" + _UID not in text and "id%3D" not in text, seen


async def test_send_email_reply_strips_the_label(monkeypatch) -> None:
    seen = _recorders(monkeypatch)
    with contextlib.suppress(Exception):  # a later step may fail on the empty fakes
        await _bare("send_email")(account_id="acc", body="Hi", reply_to_email_id=f"id={_UID}")
    text = " ".join(seen)
    assert _UID in text, seen
    assert "id=" + _UID not in text and "id%3D" not in text, seen


_ID_PARAMS = {"email_id", "reply_to_email_id", "message_id", "message_ids", "email_ids",
              "ids", "thread_id"}
#: Tools that take one of those parameters, and the test that covers each.
_COVERED = {t for t, _ in _ID_TOOLS} | {"manage_inbox", "send_email"}


def test_every_id_taking_tool_is_in_the_strip_tests() -> None:
    found = {
        name for name, fn in vars(agents).items()
        if inspect.iscoroutinefunction(getattr(fn, "__wrapped__", fn))
        and not name.startswith("_")
        and _ID_PARAMS & set(inspect.signature(getattr(fn, "__wrapped__", fn)).parameters)
    }
    assert found <= _COVERED, f"a tool takes an email id and no strip test covers it: {found - _COVERED}"
