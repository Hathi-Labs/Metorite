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


OTHER = "99999999-8888-7777-6666-555555555555"


def _report(hits, **kw):
    kw.setdefault("org", ORG)
    kw.setdefault("board_org", ORG)
    return sweep.build_report(hits, SINCE, NOW, **kw)


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
        ("write to asha.k+x@example.co.in now", "asha", "{email}"),
        ("call +91 98765 43210 today", "98765", "{phone}"),
        ("call (080) 4123-4567", "4123", "{phone}"),
        ("open https://evil.example/x?y=1 please", "evil", "{url}"),
        ("see www.example.org/page", "example", "{url}"),
        ("my key is sk-live_ABCdef123456", "ABCdef", "{secret}"),
        ("token ghp_0123456789abcdefABCDEF", "0123456789", "{secret}"),
        ("slack xoxb-1234-abcd", "1234", "{secret}"),
        ("jwt eyJhbGciOiJIUzI1NiJ9.e30.x", "hbGci", "{secret}"),
        ("hash deadbeefdeadbeefdeadbeef", "deadbeef", "{secret}"),
        ("blob QWxhZGRpbjpvcGVuIHNlc2FtZQ9z", "QWxhZG", "{secret}"),
        ("invoice 4500123 is due", "4500123", "{number}"),
        ("id 123e4567-e89b-12d3-a456-426614174000", "426614174000", "{id}"),
        ("ask @raj about it", "raj", "{handle}"),
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
    text = sweep.render(_report(_three_apps()))
    assert text.startswith('**Chat "I can\'t" answers, 2026-11-15 to 2026-11-22 (7 days)**')
    assert "| Email | 3 | 60% |" in text and "| Knowledge | 1 | 20% |" in text
    assert sweep.EXAMPLES_HEADER in text
    assert text.splitlines()[-1] == sweep.FOOTER
    assert "s1" not in text and "m0" not in text and "I can't do that" not in text


def test_counts_only_prints_no_example():
    report = _report(_three_apps(), counts_only=True)
    text = sweep.render(report)
    assert report["examples"] == []
    assert sweep.EXAMPLES_HEADER not in text and "email attachment" not in text
    assert "| Email | 3 | 60% |" in text


def test_the_capped_read_says_so():
    text = sweep.render(_report(_three_apps(), capped=True))
    assert f"The read stopped at {sweep.ROW_CAP} messages." in text


def test_no_hit_prints_exactly_the_empty_line():
    assert sweep.render(_report([])) == "No gaps found this week."


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
        ["--org", ORG],
        ["--org", ORG, "--board-org", ""],
        ["--org", ORG, "--board-org", "owner-board"],
        ["--board-org", ORG],
        ["--org", ORG, "--board-org", ORG, "--days", "0"],
        ["--org", ORG, "--board-org", ORG, "--examples", "-1"],
        ["--org", ORG, "--board-org", ORG, "--now", "yesterday"],
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
    assert (
        sweep.main(["--org", ORG.upper(), "--board-org", ORG, "--now", "2026-11-22T12:00:00Z"]) == 0
    )
    assert calls == [(ORG, int(SINCE.timestamp() * 1000), int(NOW.timestamp() * 1000))]
    assert "| Email | 3 | 100% |" in capsys.readouterr().out


def test_json_holds_counts_and_masked_examples_only(monkeypatch, capsys):
    read, _ = _fake_read(_pair("s9", "mail it to a@b.com", 1))
    monkeypatch.setattr(sweep, "read_rows", read)
    assert (
        sweep.main(["--org", ORG, "--board-org", ORG, "--now", "2026-11-22T12:00:00Z", "--json"])
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["total"] == 1 and data["apps"][0]["app"] == "email"
    assert data["examples"] == [
        {"app": "email", "name": "Email", "day": "2026-11-22", "text": "mail it to {email}"}
    ]
    assert "s9" not in json.dumps(data)


def test_a_failed_read_exits_1_with_a_fixed_line(monkeypatch, capsys):
    def boom(*_a, **_k):
        raise RuntimeError("postgresql://acb:hunter2@db/x row text: secret words")

    monkeypatch.setattr(sweep, "read_rows", boom)
    assert sweep.main(["--org", ORG, "--board-org", ORG]) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.strip() == f"{sweep.READ_FAILED} (RuntimeError)"


def test_no_hit_run_prints_the_empty_line(monkeypatch, capsys):
    read, _ = _fake_read([_row("s1", "user", "hi", 1), _row("s1", "assistant", "Hello.", 0.9)])
    monkeypatch.setattr(sweep, "read_rows", read)
    assert sweep.main(["--org", ORG, "--board-org", ORG]) == 0
    assert capsys.readouterr().out == "No gaps found this week.\n"


# ── Fix round: the board, Markdown, secrets, Unicode, the answer map ───────


def test_another_board_gets_counts_only_and_no_member_text():
    """P0. Epic 1238 is on the owner's board. A customer's words never go there."""
    report = _report(_three_apps(), board_org=OTHER)
    text = sweep.render(report)
    assert report["examples"] == [] and report["counts_only"] is True
    assert sweep.NOT_BOARD_OWNER in text and f"Organization: {ORG}" in text
    assert sweep.EXAMPLES_HEADER not in text
    assert "email attachment" not in text and "knowledge page" not in text
    assert "| Email | 3 | 60% |" in text


def test_the_owner_board_gets_the_examples_and_the_org_id():
    text = sweep.render(_report(_three_apps()))
    assert f"Organization: {ORG}" in text and sweep.NOT_BOARD_OWNER not in text
    assert sweep.EXAMPLES_HEADER in text and "email attachment 0" in text


def test_main_with_another_board_prints_no_member_text(monkeypatch, capsys):
    read, _ = _fake_read(_pair("s1", "attach the uniquewords file to the email", 1))
    monkeypatch.setattr(sweep, "read_rows", read)
    assert sweep.main(["--org", ORG, "--board-org", OTHER]) == 0
    out = capsys.readouterr().out
    assert "uniquewords" not in out and sweep.NOT_BOARD_OWNER in out
    assert sweep.main(["--org", ORG, "--board-org", OTHER, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["examples"] == [] and data["counts_only"] is True
    assert "uniquewords" not in json.dumps(data)


def _example_text(ask: str) -> str:
    """The printed example line of one member message, after the prefix."""
    hits = sweep.find_hits(_pair("s1", ask, 1))
    ex = sweep.pick_examples(hits)
    assert len(ex) == 1, ask
    line = next(ln for ln in sweep.render(_report(hits)).splitlines() if ln.startswith("- "))
    assert line.endswith(f'"{ex[0]["text"]}"')
    return ex[0]["text"]


@pytest.mark.parametrize(
    "ask",
    [
        "[url](//evil.com/phish) create a task",
        "[click](javascript:alert(1)) a task",
        "[number](/settings/members) a task",
        "![x](http://a/b.png) a task",
        "<script>alert(1)</script> a task",
        "**bold** __under__ a task",
        "# heading a task",
        "> quote a task",
        "a | b task",
        "`code` task",
    ],
)
def test_no_markdown_construct_survives(ask):
    out = _example_text(ask)
    for ch in "[]()<>`|*_":
        assert ch not in out, (ask, out)
    assert not out.startswith(("#", ">")), out
    assert "evil.com" not in out and "javascript:alert(" not in out


def test_an_at_sign_never_sits_next_to_an_address():
    out = _example_text("tell @name and x@y.com about the task")
    assert "@" not in out and "{handle}" in out and "{email}" in out


@pytest.mark.parametrize(
    "ask",
    [
        "reset my password in the task",
        "what is the PASSWD",
        "my pwd is hunter2",
        "the passcode for the door",
        "set the api  key for zoho",
        "add the API_KEY",
        "paste my private key",
        "use the bearer header",
        "the authorization header",
        "store the credentials",
        "send the OTP",
        "card cvv 123",
        "a token for slack",
        "keep this secret",
        "connect postgresql://admin:pw@db.host/x",
        "[secret](javascript:alert(1)) a task",
    ],
)
def test_a_message_that_names_a_secret_is_dropped_from_examples_and_counts(ask):
    hits = sweep.find_hits(_pair("s1", ask, 1, answer="I can't set that. Open Settings."))
    assert hits == []
    assert sweep.render(_report(hits)) == sweep.NO_GAPS


def test_the_password_refusal_never_lands_in_settings():
    rows = _pair("s1", "change my password", 2, answer="I can't change a password. Open Settings.")
    rows += _pair("s2", "change my theme", 1)
    assert [h["app"] for h in sweep.find_hits(rows)] == ["settings"]


def test_a_boilerplate_list_in_the_answer_is_unmapped():
    answer = "I can't do that. Open Email, Calendar or Tasks."
    assert sweep.map_app("do the thing for me", answer) == sweep.UNMAPPED
    assert sweep.map_app("do the thing for me", "I can't. Open the Email page.") == "email"
    assert sweep.map_app("make a task", answer) == "tasks"


@pytest.mark.parametrize(
    ("raw", "gone", "label"),
    [
        ("pan ABCDE1234F here", "ABCDE1234F", "{id}"),
        ("aadhaar 1234 5678 9012 here", "5678", "{number}"),
        ("aadhaar 1234-5678-9012 here", "5678", "{number}"),
        ("card 4111 1111 1111 1111 here", "1111", "{number}"),
        ("card 4111-1111-1111-1111 here", "1111", "{number}"),
        ("born 14/03/1988 here", "1988", "{date}"),
        ("born 1988-03-14 here", "1988", "{date}"),
        ("iban GB82 WEST 1234 5698 7654 32 here", "WEST", "{number}"),
        ("dsn postgresql://admin:hunter2@db.host/x here", "hunter2", "{url}"),
        ("open drive.google.com/x/y here", "google", "{url}"),
        ("open //evil.com/phish here", "evil", "{url}"),
        ("see docs.acme.cloud here", "acme", "{url}"),
    ],
)
def test_more_shapes_are_masked(raw, gone, label):
    out = sweep.mask(raw)
    assert gone not in out and label in out, out
    assert out.endswith(" here"), out


def test_a_domain_with_a_path_goes_whole():
    assert sweep.mask("drive.google.com/x") == "{url}"


def test_bidi_zero_width_fullwidth_and_tag_characters_cannot_hide_a_part():
    assert sweep.mask("a\u200b@b.com") == "{email}"
    assert sweep.mask("john\uff20x.com") == "{email}"
    bidi = sweep.mask("\u202emoc.x@nhoj\u202c and \u2066x\u2069 and \u2060y")
    assert not any(ch in bidi for ch in "\u202e\u202c\u2066\u2069\u2060"), repr(bidi)
    tags = sweep.mask("plain \U000e0041\U000e0042\U000e007f text \ufeff\u2028\u2029end")
    assert tags == "plain text end", repr(tags)


def test_one_hit_says_one_time():
    text = sweep.render(_report(sweep.find_hits(_pair("s1", "an email", 1))))
    assert "cannot act 1 time." in text and "1 times" not in text
