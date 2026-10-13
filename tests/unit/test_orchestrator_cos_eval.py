"""The chief-of-staff eval runs offline, and each check fires (spec §11.5).

``evals/orchestrator_cos/run.py --scripted`` scores hand-written answers.
Each good answer passes every check, and each bad answer fails exactly the
checks it names. A live run needs the Router, so CI never runs it.

Mutations this file catches (R7): a check that stops firing (a bad answer
then passes it), a case set that loses a kind the owner named, a cap that
drifts, and a live run that starts with no endpoint.
"""
from __future__ import annotations

import copy

from evals.orchestrator_cos import run


def test_the_case_set_covers_each_kind_the_owner_named() -> None:
    cases = run.load_cases()
    assert 8 <= len(cases) <= 12
    assert len({c["id"] for c in cases}) == len(cases)
    kinds = {c["kind"] for c in cases}
    for kind in ("status", "attention", "decision", "relay", "fact", "detail", "expand"):
        assert kind in kinds, kind
    # A fact stays short, and an explicit request for detail stays long.
    facts = [c for c in cases if c["kind"] == "fact"]
    assert facts and all(c["checks"].get("max_words", 999) <= 40 for c in facts)
    details = [c for c in cases if c["kind"] in ("detail", "expand")]
    assert details and all(not c["checks"]["routine"] and c["checks"]["min_words"] for c in details)


def test_the_scripted_run_passes() -> None:
    assert run.run_scripted(run.load_cases(), run.load_scripted()) == []
    assert run.main(["--scripted"]) == run.EXIT_PASS


def test_every_check_is_proved_to_fire_by_some_bad_answer() -> None:
    fired = {f for a in run.load_scripted().values() for b in a.get("bad", []) for f in b["expected_fail"]}
    assert fired >= set(run.CHECKS) - {"voice"}


def test_a_pasted_read_fails_the_scripted_run() -> None:
    answers = copy.deepcopy(run.load_scripted())
    case = next(c for c in run.load_cases() if c["id"] == "C04")
    answers["C04"]["good"]["text"] = case["reads"][0]["result"]
    bad = run.run_scripted(run.load_cases(), answers)
    assert bad and bad[0].startswith("C04 good")


def test_an_em_dash_fails_the_voice_check() -> None:
    case = next(c for c in run.load_cases() if c["id"] == "C08")
    got = run.check(case, "Rohan Kulkarni owns the Helios deal — since August.")
    assert got["voice"] is False


def test_the_routine_cap_is_120_words() -> None:
    assert run.ROUTINE_CAP == 120
    case = next(c for c in run.load_cases() if c["id"] == "C04")
    long = "Kestrel is on track for the 30 Oct release. " + "More words here. " * 40
    assert run.check(case, long)["length"] is False


def test_a_card_counts_for_detail_and_never_for_the_cap() -> None:
    detail = next(c for c in run.load_cases() if c["id"] == "C06")
    short = "Nine invoices are open, worth Rs 11,38,500. INV-2207 is overdue."
    assert run.check(detail, short)["length"] is False
    assert run.check(detail, short, "INV-2226 " + "row " * 100)["length"] is True


def test_a_citation_is_not_a_raw_id() -> None:
    case = next(c for c in run.load_cases() if c["id"] == "C08")
    assert run.check(case, "Rohan Kulkarni owns it [entity:a41f].")["no_dump"] is True
    raw = "Rohan Kulkarni owns it (a41f0000-1111-2222-3333-444455556666)."
    assert run.check(case, raw)["no_dump"] is False


def test_longest_copy_counts_words_in_order() -> None:
    assert run.longest_copy("a b c d", "x a b c y") == 3
    assert run.longest_copy("", "a") == 0


def test_the_live_run_refuses_without_an_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    assert run.main(["--live", "--out", "unused-cos.json"]) == run.EXIT_NO_GO
