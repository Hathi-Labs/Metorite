"""Fence for scripts/chat_cant_sweep.py (PAR-7, task 3116). Plain rows, no database.

Spec: ``project-docs/specs/chat_parity.md`` §6.5. The R8 twin is
``test_chat_cant_sweep_r8.py``: it proves the tenant bind on a real Postgres.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import chat_cant_sweep as sweep
from acb_skills.chat_parity import APPS

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=7)
ORG = "11111111-2222-3333-4444-555555555555"


def _ms(hours_ago: float) -> int:
    return int((NOW - timedelta(hours=hours_ago)).timestamp() * 1000)


def _row(sid, role, content, hours_ago, typed=True):
    return {
        "session_id": sid,
        "role": role,
        "content": content,
        "timestamp_ms": _ms(hours_ago),
        "member_typed": typed,
    }


def _pair(sid, ask, hours_ago, answer="I can't do that from the chat.", typed=True):
    return [
        _row(sid, "user", ask, hours_ago + 0.01, typed),
        _row(sid, "assistant", answer, hours_ago),
    ]


# ── Detection ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "I can't invite a member from here.",
        "I can\u2019t attach a file to that email.",
        "Sorry, I cannot create a Knowledge page.",
        "I am not able to change roles.",
        "I'm unable to send that.",
        "I don't have a tool for WhatsApp groups.",
        "I have no tool that edits a workflow.",
        "There is no tool for that yet.",
        "The only pages I can open are Projects and Calendar.",
        "That is not something I can do from here.",
        "I'm not set up to change billing.",
    ],
)
def test_a_refusal_is_detected(text):
    assert sweep.is_cant(text)


@pytest.mark.parametrize(
    "text",
    [
        "Done. I created the task.",
        "I can't find that task. Can you name it again?",
        "I can't be sure the meeting moved.",
        "I can help with that.",
        "AI can't replace you.",
        "",
        None,
    ],
)
def test_a_normal_answer_is_not_a_refusal(text):
    assert not sweep.is_cant(text)


# ── Pairing ────────────────────────────────────────────────────────────────


def test_an_answer_pairs_with_the_nearest_earlier_member_row_of_its_session():
    rows = [
        _row("s1", "user", "make a calendar event", 5),
        _row("s1", "user", "invite raj to our org", 4),
        _row("s2", "user", "send a whatsapp", 3.5),
        _row("s1", "assistant", "I can't invite people.", 3),
    ]
    hits = sweep.find_hits(list(reversed(rows)))  # the input order does not matter
    assert [(h["session_id"], h["user_text"], h["app"]) for h in hits] == [
        ("s1", "invite raj to our org", "admin")
    ]


def test_an_answer_with_no_earlier_member_row_is_skipped_and_system_is_ignored():
    rows = [
        _row("s1", "system", "invite everyone", 6),
        _row("s1", "assistant", "I can't do that.", 5),
        _row("s1", "user", "later question", 4),
    ]
    assert sweep.find_hits(rows) == []


def test_one_member_message_gives_one_hit():
    rows = [
        *_pair("s1", "attach the file to the email", 2),
        _row("s1", "assistant", "I cannot do that either.", 1.9),
    ]
    assert len(sweep.find_hits(rows)) == 1


def test_a_server_written_row_counts_but_is_never_an_example():
    """member_typed FALSE is a pill or a card action: a gap, not the member's words."""
    rows = _pair("s1", "Open the CRM pipeline", 2, typed=False) + _pair(
        "s2", "add a lead", 1, typed=None
    )
    hits = sweep.find_hits(rows)
    assert [h["app"] for h in hits] == ["crm", "crm"]
    examples = sweep.pick_examples(hits)
    assert [e["text"] for e in examples] == ["add a lead"]


# ── Mapping ────────────────────────────────────────────────────────────────


def test_every_mapping_key_is_a_key_of_apps():
    keys = [key for key, _ in sweep.APP_KEYWORDS]
    assert len(keys) == len(set(keys))
    assert set(keys) <= set(APPS), set(keys) - set(APPS)


@pytest.mark.parametrize(
    ("user", "answer", "app"),
    [
        ("please invite raj to the organization", "", "admin"),
        ("add a new member", "", "admin"),
        ("invite raj to the 3pm meeting", "", "calendar"),
        ("put the file on the email to Asha", "", "email"),
        ("make a knowledge page about onboarding", "", "knowledge"),
        ("write the meeting notes", "", "notes"),
        ("message the group on WhatsApp", "", "whatsapp"),
        ("move the deal to won", "", "crm"),
        ("make a task from this", "", "tasks"),
        ("run my workflow", "", "workflows"),
        ("do it", "You can do that on the Knowledge page.", "knowledge"),
        ("do it", "Sorry.", sweep.UNMAPPED),
    ],
)
def test_the_map(user, answer, app):
    assert sweep.map_app(user, answer) == app


# ── Masking ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "gone", "label"),
    [
        ("write to asha.k+x@example.co.in now", "asha", "[email]"),
        ("call +91 98765 43210 today", "98765", "[phone]"),
        ("call (080) 4123-4567", "4123", "[phone]"),
        ("open https://evil.example/x?y=1 please", "evil", "[url]"),
        ("see www.example.org/page", "example", "[url]"),
        ("my key is sk-live_ABCdef123456", "ABCdef", "[secret]"),
        ("token ghp_0123456789abcdefABCDEF", "0123456789", "[secret]"),
        ("slack xoxb-1234-abcd", "1234", "[secret]"),
        ("jwt eyJhbGciOiJIUzI1NiJ9.e30.x", "hbGci", "[secret]"),
        ("hash deadbeefdeadbeefdeadbeef", "deadbeef", "[secret]"),
        ("blob QWxhZGRpbjpvcGVuIHNlc2FtZQ9z", "QWxhZG", "[secret]"),
        ("invoice 4500123 is due", "4500123", "[number]"),
        ("id 123e4567-e89b-12d3-a456-426614174000", "426614174000", "[id]"),
        ("ask @raj about it", "raj", "[handle]"),
    ],
)
def test_mask_removes_private_parts(raw, gone, label):
    out = sweep.mask(raw)
    assert gone not in out and label in out, out


def test_mask_collapses_space_and_control_characters_and_cuts_to_160():
    out = sweep.mask("a\x00b\n\n\tc   d\u200b" + "x " * 200)
    assert "\n" not in out and "\x00" not in out and "  " not in out
    assert out.startswith("a b c d")
    assert len(out) <= sweep.CUT and out.endswith("\u2026")
    assert sweep.mask("short") == "short"


def test_the_markdown_cannot_carry_a_link_html_or_a_table_break():
    hits = sweep.find_hits(_pair("s1", 'email `x` <b>[click](y)</b> | "q"', 1))
    text = sweep.render(sweep.build_report(hits, SINCE, NOW))
    line = next(ln for ln in text.splitlines() if ln.startswith("- Email"))
    for bad in ("`", "<b>", "](", "| ", '"q"'):
        assert bad not in line.split(": ", 1)[1].strip('"'), line


# ── Limits ─────────────────────────────────────────────────────────────────


def test_examples_are_newest_first_two_per_app_one_per_session_and_capped():
    rows = []
    for i in range(5):
        rows += _pair(f"e{i}", f"attach file {i} to the email", 10 - i)
    rows += _pair("e4", "and another email attachment", 0.5)  # same session as e4
    for i in range(12):
        rows += _pair(f"k{i}", f"knowledge page {i}", 20 + i)
    for i in range(12):
        rows += _pair(f"t{i}", f"task {i}", 40 + i)
    hits = sweep.find_hits(rows)
    ex = sweep.pick_examples(hits, limit=10)
    assert [e["text"] for e in ex if e["app"] == "email"] == [
        "and another email attachment",
        "attach file 3 to the email",
    ]
    assert Counter(e["app"] for e in ex) == {"email": 2, "knowledge": 2, "tasks": 2}
    assert len(sweep.pick_examples(hits, limit=3)) == 3
    assert sweep.pick_examples(hits, limit=0) == []
    assert all(set(e) == {"app", "name", "day", "text"} for e in ex)


# ── Ranking and rendering ──────────────────────────────────────────────────


def _three_apps():
    rows = []
    for i in range(3):
        rows += _pair(f"m{i}", f"email attachment {i}", i + 1)
    rows += _pair("k0", "a knowledge page", 5)
    rows += _pair("u0", "do the thing", 6, answer="I can't.")
    return sweep.find_hits(rows)


def test_rank_orders_by_count_and_the_share_adds_up():
    ranked = sweep.rank(_three_apps())
    assert [(r["app"], r["count"]) for r in ranked] == [
        ("email", 3),
        ("knowledge", 1),
        (sweep.UNMAPPED, 1),
    ]
    assert ranked[0]["share"] == 0.6 and ranked[0]["name"] == "Email"
    assert ranked[2]["name"] == sweep.UNMAPPED_NAME
    assert abs(sum(r["share"] for r in ranked) - 1.0) < 0.001


def test_render_is_one_comment_with_a_table_the_examples_and_the_footer():
    text = sweep.render(sweep.build_report(_three_apps(), SINCE, NOW))
    assert text.startswith('**Chat "I can\'t" answers, 2026-11-15 to 2026-11-22 (7 days)**')
    assert "| Email | 3 | 60% |" in text and "| Knowledge | 1 | 20% |" in text
    assert sweep.EXAMPLES_HEADER in text
    assert text.splitlines()[-1] == sweep.FOOTER
    assert "s1" not in text and "m0" not in text and "I can't do that" not in text


def test_counts_only_prints_no_example():
    report = sweep.build_report(_three_apps(), SINCE, NOW, counts_only=True)
    text = sweep.render(report)
    assert report["examples"] == []
    assert sweep.EXAMPLES_HEADER not in text and "email attachment" not in text
    assert "| Email | 3 | 60% |" in text


def test_the_capped_read_says_so():
    text = sweep.render(sweep.build_report(_three_apps(), SINCE, NOW, capped=True))
    assert f"The read stopped at {sweep.ROW_CAP} messages." in text


def test_no_hit_prints_exactly_the_empty_line():
    assert sweep.render(sweep.build_report([], SINCE, NOW)) == "No gaps found this week."


# ── The command line ───────────────────────────────────────────────────────


def _fake_read(rows, capped=False):
    calls = []

    def read(org, since_ms, until_ms, cap=sweep.ROW_CAP):
        calls.append((org, since_ms, until_ms))
        return rows, capped

    return read, calls


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--org", ""],
        ["--org", "acme"],
        ["--org", "1; DROP TABLE x"],
        ["--org", ORG, "--days", "0"],
        ["--org", ORG, "--examples", "-1"],
        ["--org", ORG, "--now", "yesterday"],
    ],
)
def test_bad_input_exits_2_with_no_read(argv, monkeypatch):
    read, calls = _fake_read([])
    monkeypatch.setattr(sweep, "read_rows", read)
    with pytest.raises(SystemExit) as out:
        sweep.main(argv)
    assert out.value.code == 2 and calls == []


def test_a_read_passes_the_window_and_prints_the_comment(monkeypatch, capsys):
    rows = []
    for i in range(3):
        rows += _pair(f"m{i}", f"email attachment {i}", i + 1)
    read, calls = _fake_read(rows)
    monkeypatch.setattr(sweep, "read_rows", read)
    assert sweep.main(["--org", ORG.upper(), "--now", "2026-11-22T12:00:00Z"]) == 0
    assert calls == [(ORG, int(SINCE.timestamp() * 1000), int(NOW.timestamp() * 1000))]
    assert "| Email | 3 | 100% |" in capsys.readouterr().out


def test_json_holds_counts_and_masked_examples_only(monkeypatch, capsys):
    read, _ = _fake_read(_pair("s9", "mail it to a@b.com", 1))
    monkeypatch.setattr(sweep, "read_rows", read)
    assert sweep.main(["--org", ORG, "--now", "2026-11-22T12:00:00Z", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total"] == 1 and data["apps"][0]["app"] == "email"
    assert data["examples"] == [
        {"app": "email", "name": "Email", "day": "2026-11-22", "text": "mail it to [email]"}
    ]
    assert "s9" not in json.dumps(data)


def test_a_failed_read_exits_1_with_a_fixed_line(monkeypatch, capsys):
    def boom(*_a, **_k):
        raise RuntimeError("postgresql://acb:hunter2@db/x row text: secret words")

    monkeypatch.setattr(sweep, "read_rows", boom)
    assert sweep.main(["--org", ORG]) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.strip() == f"{sweep.READ_FAILED} (RuntimeError)"


def test_no_hit_run_prints_the_empty_line(monkeypatch, capsys):
    read, _ = _fake_read([_row("s1", "user", "hi", 1), _row("s1", "assistant", "Hello.", 0.9)])
    monkeypatch.setattr(sweep, "read_rows", read)
    assert sweep.main(["--org", ORG]) == 0
    assert capsys.readouterr().out == "No gaps found this week.\n"
