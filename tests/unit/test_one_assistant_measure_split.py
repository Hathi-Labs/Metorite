"""The fence of the held-out split, the sealed results and the win margin (task 3649).

No network and no model. The results files here are synthetic. A held-out row
holds a SENTINEL in each text field, and the tests prove that the sentinel
reaches the sibling file only: never the public file, never a printed table.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evals.one_assistant_measure import compare, run, scenarios, splits
from evals.one_assistant_measure.scenarios import Check, Plan, Scenario, Turn

SENTINEL = "ZZ-SEALED-ZZ"
HOLD_ID = "syn_hold_chat"


def _scenario(sid: str, tags: tuple[str, ...]) -> Scenario:
    turn = Turn(f"{SENTINEL} words", (Check("answer_all", (r"alpha",), "has_alpha"),), Plan())
    return Scenario(sid, "synthetic", (turn,), tags=tags)


@pytest.fixture(autouse=True)
def synthetic_chats(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chat list holds the synthetic chats, so a tag lookup finds them."""
    chats = (_scenario(HOLD_ID, ("holdout",)), _scenario("syn_tune_chat", ()))
    monkeypatch.setattr(scenarios, "all_scenarios", lambda: chats)


def row(sid: str, turn: int, repeat: int, passed: bool, *, arm: str = "pack",
        tags: tuple[str, ...] = (), set_: str = "email", capped: bool = False,
        split: str | None = None, check: str = "has_alpha") -> dict[str, Any]:
    """A result row with every text field set to the sentinel."""
    held = split == "holdout" or (split is None and sid == HOLD_ID)
    word = SENTINEL if held else "tune text"
    out: dict[str, Any] = {
        "scenario": sid, "turn": turn, "arm": arm, "repeat": repeat, "arm_label": arm,
        "tier_map": {}, "words": word, "checks": {"ran": True, check: passed},
        "passed": passed, "answer": word, "error": None, "email_tools": [word],
        "tasks": [word], "refused": 0, "cards": 0, "first_text_s": 1.0, "total_s": 2.0,
        "model_calls": [{"agent": "orchestrator", "model": "m", "served": "m", "prompt": 10,
                         "cached": 0, "completion": 5, "cost_usd": 0.01, "dur_s": 1.0,
                         "tools": 3}],
        "sent": [], "router_sends": 0, "unseen_sends": 0, "unseen_usd": 0.0,
        "capped": "refused" if capped else None, "handoffs": [], "set": set_, "surface": "",
        "work_tools": [word], "specialist_handoffs": {}, "handoffs_refused": {},
        "loaded_by_model": [], "handoff_reasons": {}, "spawned": 0, "research": False,
        "items_problems": [], "web_fetches": 0, "web_searches": 0, "web_unknown": [],
        "work_writes": [word], "unknown_routes": [], "max_tools": 3, "pack_events": [],
        "mark_writes": 0, "searches": 0, "found_tools": [], "schema_tokens": 0, "held": False,
        "find_tools_offered": False, "find_tools_requests": 0,
        "split": "holdout" if held else (split or "tune"), "tags": list(tags),
    }
    if capped:
        out["passed"] = None
    return out


def data_of(rows: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"date": "2026-11-17", "sha": "abc", "rows": rows, "spent_usd": 0.1,
            "spent_before_usd": 0.0, "stopped": None, "dropped": [], **extra}


# ── the split ────────────────────────────────────────────────────────────────


def test_a_chat_is_held_out_only_by_its_tag() -> None:
    assert splits.split_of(_scenario("a", ("holdout",))) == "holdout"
    assert splits.split_of(_scenario("a", ("navy", "t2874"))) == "tune"
    assert splits.split_of(_scenario("a", ())) == "tune"


def test_a_row_reads_its_stamp_then_its_chat_then_tune() -> None:
    assert splits.split_of_row({"scenario": "x", "split": "holdout"}) == "holdout"
    assert splits.split_of_row({"scenario": HOLD_ID}) == "holdout"  # an old file, no stamp
    assert splits.split_of_row({"scenario": "gone_chat"}) == "tune"  # a chat that left


def test_split_flag_keeps_the_right_chats() -> None:
    chats = scenarios.all_scenarios()
    assert [s.id for s in splits.keep(chats, "holdout")] == [HOLD_ID]
    assert [s.id for s in splits.keep(chats, "tune")] == ["syn_tune_chat"]
    assert len(splits.keep(chats, "all")) == 2


# ── the seal ─────────────────────────────────────────────────────────────────


def full_data() -> dict[str, Any]:
    rows = [row("syn_tune_chat", 1, 1, True), row("syn_tune_chat", 1, 2, False),
            row(HOLD_ID, 1, 1, True), row(HOLD_ID, 1, 2, False)]
    return data_of(rows, dropped=[{"scenario": HOLD_ID, "arm": "pack", "repeat": 1, "usd": 0.0}],
                   chunks_run=[{"chunk": "a", "sha": "abc", "rows": 4, "repeats": [1, 2],
                                "scenarios": [HOLD_ID, "syn_tune_chat"]}])


def test_the_public_file_holds_counts_for_held_out_chats_and_no_text(tmp_path: Path) -> None:
    out = tmp_path / "X.json"
    run._write(out, full_data())
    public = out.read_text(encoding="utf-8")
    assert HOLD_ID not in public
    side = splits.sibling(out)
    assert side.name == "X.holdout.json"
    sealed_file = side.read_text(encoding="utf-8")
    assert SENTINEL in sealed_file and HOLD_ID in sealed_file
    body = json.loads(public)
    assert [r["scenario"] for r in body["rows"]] == ["syn_tune_chat", "syn_tune_chat"]
    arm = body["sealed"]["arms"]["pack"]
    assert arm["repeats"] == {"1": {"turns": 1, "passed": 1, "capped": 0},
                              "2": {"turns": 1, "passed": 0, "capped": 0}}
    assert arm["checks"]["has_alpha"] == {"pass": 1, "fail": 1}
    assert arm["checks"]["ran"] == {"pass": 2, "fail": 0}


def test_a_key_that_a_later_row_adds_never_leaks(tmp_path: Path) -> None:
    held = row(HOLD_ID, 1, 1, True)
    held["a_future_key"] = SENTINEL
    out = tmp_path / "Y.json"
    run._write(out, data_of([held]))
    assert SENTINEL not in out.read_text(encoding="utf-8")


def test_no_printed_table_holds_a_held_out_word(tmp_path: Path,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "X.json"
    run._write(out, full_data())
    assert run.main(["--report", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert SENTINEL not in printed and HOLD_ID not in printed
    assert "Held-out chats (sealed" in printed
    # merge: the chunk is read with its sibling, and the new file is sealed again
    merged = tmp_path / "M.json"
    assert run.main(["--merge", str(out), "--out", str(merged)]) == 0
    printed = capsys.readouterr().out
    assert SENTINEL not in printed and HOLD_ID not in printed
    assert SENTINEL not in merged.read_text(encoding="utf-8")
    assert SENTINEL in splits.sibling(merged).read_text(encoding="utf-8")
    assert HOLD_ID not in merged.read_text(encoding="utf-8")


def test_rescore_seals_its_changes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A change of a check on a held-out turn is counted, never named."""
    out = tmp_path / "R.json"
    rows = [row("syn_tune_chat", 1, 1, True), row(HOLD_ID, 1, 1, True)]
    for r in rows:
        r["answer"] = "no match here"  # the stored answer fails ``has_alpha`` now
    run._write(out, data_of(rows))
    assert run.main(["--rescore", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert HOLD_ID not in printed and SENTINEL not in printed
    assert "1 changes in held-out chats" in printed
    public = out.read_text(encoding="utf-8")
    assert HOLD_ID not in public and SENTINEL not in public
    assert "syn_tune_chat#1" in public  # a tune change stays named


def test_reading_the_public_file_alone_keeps_the_counts(tmp_path: Path) -> None:
    out = tmp_path / "X.json"
    run._write(out, full_data())
    splits.sibling(out).unlink()
    data = splits.load(out)
    public, held = splits.seal(data)
    assert held is None and public["sealed"]["rows"] == 2


def test_a_rerun_with_no_held_out_row_removes_the_stale_sibling(tmp_path: Path) -> None:
    out = tmp_path / "X.json"
    run._write(out, full_data())
    run._write(out, data_of([row("syn_tune_chat", 1, 1, True)]))
    assert not splits.sibling(out).exists()


def test_a_live_console_line_hides_a_held_out_turn(capsys: pytest.CaptureFixture[str]) -> None:
    from evals.one_assistant_measure.run import splits as used

    assert used.label(_scenario(HOLD_ID, ("holdout",))) == "a held-out chat"
    assert used.label(_scenario("plain", ())) == "plain"


# ── the compare rule ─────────────────────────────────────────────────────────


def put(tmp_path: Path, name: str, rows: list[dict[str, Any]], **extra: Any) -> Path:
    out = tmp_path / name
    run._write(out, data_of(rows, **extra))
    return out


def chats(spec: dict[str, tuple[bool, bool]], *, tags: tuple[str, ...] = (),
          set_: str = "email", split: str | None = None) -> list[dict[str, Any]]:
    """One turn for each chat id: its pass in repeat 1 and in repeat 2."""
    return [row(sid, 1, rep + 1, ok, tags=tags, set_=set_, split=split or "tune")
            for sid, both in spec.items() for rep, ok in enumerate(both)]


T = ("t2874",)


def base_rows(*, t_after: bool = True, noisy: int = 0, t_noisy: int = 0, h_after: bool = True,
              e_after: bool = True, case_after: bool = True,
              ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """B and A with 3 T chats and 1 case chat (all fail in B), 2 held-out chats and
    2 E chats (all pass in B, but *noisy* of them fail in repeat 2 of B). *t_noisy* of the
    T chats pass in repeat 2 of B and fail in repeat 1: noise in T."""
    t_ids = {f"t{i}": (False, i < t_noisy) for i in range(3)}
    t_ids_a = {k: (t_after, t_after) for k in t_ids}
    e_b = {"e1": (True, noisy < 1), "e2": (True, noisy < 2)}
    b = (chats(t_ids, tags=T)
         + chats({"case1": (False, False)}, tags=(*T, "case:1"))
         + chats({"h1": (True, True), "h2": (True, True)}, split="holdout", tags=("holdout",))
         + chats(e_b))
    a = (chats(t_ids_a, tags=T)
         + chats({"case1": (case_after, case_after)}, tags=(*T, "case:1"))
         + chats({"h1": (h_after, h_after), "h2": (True, True)}, split="holdout", tags=("holdout",))
         + chats({"e1": (e_after, e_after), "e2": (True, True)}))
    return b, a


def verdict(tmp_path: Path, b: list[dict[str, Any]], a: list[dict[str, Any]]) -> tuple[str, int]:
    return compare.compare(put(tmp_path, "B.json", b), put(tmp_path, "A.json", a))


def test_a_clear_gain_wins(tmp_path: Path) -> None:
    b, a = base_rows()
    text, code = verdict(tmp_path, b, a)
    assert code == compare.EXIT_WIN, text
    assert "VERDICT: WIN" in text
    assert "noise n = 0 of 6 T and H turns" in text
    assert "T (tune, tag t2874): 4 turns. Passes B [0, 0], A [4, 4]" in text
    assert "H (held-out): 2 turns" in text
    assert "Other tune (not T, not E): 0 turns" in text
    assert "Tune total: 6 turns. Passes B [2, 2], A [6, 6]." in text


def test_the_noise_is_the_flip_count_between_two_repeats_of_b(tmp_path: Path) -> None:
    b, a = base_rows(t_noisy=2)
    text, _ = verdict(tmp_path, b, a)
    assert "noise n = 2 of 6 T and H turns" in text


def test_the_noise_of_the_email_set_does_not_raise_n(tmp_path: Path) -> None:
    b, a = base_rows(noisy=2)  # both E chats flip between the repeats of B
    text, _ = verdict(tmp_path, b, a)
    assert "noise n = 0 of 6 T and H turns" in text


def test_rule_2_passes_when_a_passes_every_t_turn_in_both_repeats(tmp_path: Path) -> None:
    # B fails one T turn in one repeat: the gain is 1, below 2, and the noise is 1.
    t = {f"t{i}": (i != 0, True) for i in range(3)}
    case = {"case1": (True, True)}
    rest = (chats({"h1": (True, True)}, split="holdout", tags=("holdout",))
            + chats({"e1": (True, True)}))
    b = chats(t, tags=T) + chats(case, tags=(*T, "case:1")) + rest
    a = (chats({k: (True, True) for k in t}, tags=T) + chats(case, tags=(*T, "case:1"))
         + chats({"h1": (True, True)}, split="holdout", tags=("holdout",))
         + chats({"e1": (True, True)}))
    text, code = verdict(tmp_path, b, a)
    assert "rule 2  T gain 1" in text and "ok, A passes every T turn in both repeats" in text
    assert code == compare.EXIT_WIN, text
    # one T turn of A fails in one repeat: the branch closes, and the gain of 1 is too small
    for r in a:
        if r["scenario"] == "t1" and r["repeat"] == 2:
            r["checks"]["has_alpha"] = False
    text, code = verdict(tmp_path, b, a)
    assert code == compare.EXIT_LOSS and "rule 2" in text and "FAILS" in text


def test_rule_2_boundary_is_two_with_no_noise(tmp_path: Path) -> None:
    # Only the case chat gains: A 2 - B 0 = 2, and max(2, 0 + 1) = 2.
    b, a = base_rows(t_after=False)
    text, code = verdict(tmp_path, b, a)
    assert "rule 2  T gain 2 (A 2 - B 0) needs at least 2" in text and "ok, by the gain" in text, text
    assert code == compare.EXIT_WIN
    # One gained turn in one repeat is below two.
    for r in a:
        if r["scenario"] == "case1" and r["repeat"] == 2:
            r["checks"]["has_alpha"] = False
    text, code = verdict(tmp_path, b, a)
    assert "rule 2  T gain 1" in text and code == compare.EXIT_LOSS


def test_rule_2_needs_more_when_the_noise_is_higher(tmp_path: Path) -> None:
    # The same gain of 2 loses when B flips two turns between its repeats: need 3.
    b, a = base_rows(t_after=False, t_noisy=2)
    text, code = verdict(tmp_path, b, a)
    assert "needs at least 3" in text and "FAILS" in text, text
    assert code == compare.EXIT_LOSS


def test_rule_1_a_case_chat_must_pass_both_repeats_of_a(tmp_path: Path) -> None:
    b, a = base_rows()
    for r in a:
        if r["scenario"] == "case1" and r["repeat"] == 2:
            r["checks"]["has_alpha"] = False
    text, code = verdict(tmp_path, b, a)
    assert code == compare.EXIT_LOSS
    assert "case:1 case1: B [False, False], A [True, False]: FAILS (fixed)" in text


def test_rule_1_a_case_that_passed_in_b_must_still_pass(tmp_path: Path) -> None:
    b, a = base_rows()
    for r in b:
        if r["scenario"] == "case1":
            r["checks"]["has_alpha"] = True
    for r in a:
        if r["scenario"] == "case1" and r["repeat"] == 1:
            r["checks"]["has_alpha"] = False
    text, code = verdict(tmp_path, b, a)
    assert code == compare.EXIT_LOSS
    assert "(kept)" in text and "FAILS" in text


def test_rule_1_a_capped_case_in_a_fails(tmp_path: Path) -> None:
    b, a = base_rows()
    for r in a:
        if r["scenario"] == "case1" and r["repeat"] == 2:
            r["capped"] = "refused"
    text, code = verdict(tmp_path, b, a)
    assert code == compare.EXIT_LOSS
    assert "case1: B [False, False], A [True, None]: FAILS" in text
    assert "Left out (missing or capped in one run): 1" in text


def test_rule_3_held_out_may_fall_by_the_noise_only(tmp_path: Path) -> None:
    b, a = base_rows(h_after=False)  # h1 falls in both repeats: -2, noise 0
    text, code = verdict(tmp_path, b, a)
    assert code == compare.EXIT_LOSS
    assert "rule 3  H A 2 >= B 4 - n 0: FAILS" in text
    # one noisy turn does not let a fall of 2 through, two noisy turns do
    b, a = base_rows(h_after=False, t_noisy=2)
    text, code = verdict(tmp_path, b, a)
    assert "rule 3  H A 2 >= B 4 - n 2: ok" in text


def test_rule_4_the_email_set_may_fall_by_the_noise_only(tmp_path: Path) -> None:
    b, a = base_rows(e_after=False)  # e1 falls in both repeats: A 2, B 4
    text, code = verdict(tmp_path, b, a)
    assert code == compare.EXIT_LOSS
    assert "rule 4  E A 2 >= B 4 - n 0: FAILS" in text
    b, a = base_rows(e_after=False, t_noisy=2)  # two noisy turns let a fall of 2 pass
    text, _ = verdict(tmp_path, b, a)
    assert "rule 4  E A 2 >= B 4 - n 2: ok" in text, text


def test_the_held_out_turns_are_counted_never_named(tmp_path: Path) -> None:
    b, a = base_rows(h_after=False)
    text, _ = verdict(tmp_path, b, a)
    assert "h1" not in text.replace("H (held-out)", "")
    assert "Flipped to fail: 2" in text


def test_h_with_no_turn_is_said(tmp_path: Path) -> None:
    b, a = base_rows()
    b = [r for r in b if r["split"] != "holdout"]
    a = [r for r in a if r["split"] != "holdout"]
    text, code = verdict(tmp_path, b, a)
    assert "H: 0 turns" in text and code == compare.EXIT_WIN


def test_compare_needs_two_repeats_and_the_sibling_file(tmp_path: Path) -> None:
    b, a = base_rows()
    one = [r for r in b if r["repeat"] == 1]
    text, code = compare.compare(put(tmp_path, "B1.json", one), put(tmp_path, "A1.json", a))
    assert code == compare.EXIT_NO_GO and "needs two repeats" in text
    pa = put(tmp_path, "A2.json", a)
    pb = put(tmp_path, "B2.json", b)
    splits.sibling(pa).unlink()
    text, code = compare.compare(pb, pa)
    assert code == compare.EXIT_NO_GO and "sibling" in text


def test_compare_refuses_two_configurations(tmp_path: Path) -> None:
    b, a = base_rows()
    pb = put(tmp_path, "B.json", b, mirror_prod={"read": "2026-11-17"})
    pa = put(tmp_path, "A.json", a)
    text, code = compare.compare(pb, pa)
    assert code == compare.EXIT_NO_GO and "mirror_prod" in text


def test_the_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    b, a = base_rows()
    pb, pa = put(tmp_path, "B.json", b), put(tmp_path, "A.json", a)
    assert run.main(["--compare", f"{pb},{pa}"]) == 0
    assert run.main(["--compare", f"{pa},{pb}"]) == run.EXIT_LOSS
    printed = capsys.readouterr().out
    assert SENTINEL not in printed and HOLD_ID not in printed
    assert run.main(["--compare", str(pb)]) == run.EXIT_NO_GO
    assert run.EXIT_LOSS not in (run.EXIT_PASS, run.EXIT_FAIL, run.EXIT_NO_GO)


def test_the_t_tag_is_a_parameter(tmp_path: Path) -> None:
    b, a = base_rows()
    pb, pa = put(tmp_path, "B.json", b), put(tmp_path, "A.json", a)
    text, _ = compare.compare(pb, pa, t_tag="t9999")
    assert "T (tune, tag t9999): 0 turns" in text
