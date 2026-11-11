"""Fence for scripts/stuck_runs_sweep.py (CIV-14e). Fake runs, no network."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import stuck_runs_sweep as sweep  # noqa: E402

NOW = datetime(2026, 11, 5, 17, 0, tzinfo=timezone.utc)


def _run(body, created="2026-11-05T16:00:00+00:00", question=None, status="in_progress"):
    return {
        "id": "r1",
        "task_id": "t1",
        "status": status,
        "open_question": question,
        "latest_line": {"type": "thought", "body": {"untrusted_text": body}, "created_at": created},
    }


def test_waits_on_owner_without_question_is_flagged():
    out = sweep.find_stuck([_run("PR is up. AWAITING-OWNER preview.")], NOW)
    assert [s["run_id"] for s in out] == ["r1"] and out[0]["waiting_minutes"] == 60
    assert "ask_on_task" in out[0]["flag"]


def test_open_question_is_not_flagged():
    assert sweep.find_stuck([_run("waits on the owner", question={"id": "q"})], NOW) == []


def test_young_line_is_not_flagged():
    assert sweep.find_stuck([_run("waiting for the owner", created="2026-11-05T16:45:00+00:00")], NOW) == []


def test_other_text_and_closed_runs_are_not_flagged():
    assert sweep.find_stuck([_run("running the verifier")], NOW) == []
    assert sweep.find_stuck([_run("waits on the owner", status="completed")], NOW) == []


def test_cli_exit_code(tmp_path, capsys):
    import json

    f = tmp_path / "runs.json"
    f.write_text(json.dumps({"runs": [_run("Waiting on the owner's preview")]}), encoding="utf-8")
    assert sweep.main([str(f), "--now", "2026-11-05T17:00:00Z"]) == 1
    assert "stuck, no question" in capsys.readouterr().out


def test_status_is_authoritative_over_state():
    r = _run("waits on the owner", status="completed")
    r["state"] = "in_progress"
    assert sweep.find_stuck([r], NOW) == []


def test_phrase_variants_and_negation():
    yes = ["awaiting your approval", "Awaiting approval from the owner", "waiting for you", "blocked on the owner"]
    for t in yes:
        assert sweep.find_stuck([_run(t)], NOW), t
    assert sweep.find_stuck([_run("I am not waiting on the owner")], NOW) == []


def test_stopping_run_counts_and_bad_timestamp_does_not_abort():
    bad = _run("waits on the owner", created="garbage")
    good = _run("waits on the owner", status="stopping")
    assert len(sweep.find_stuck([bad, good], NOW)) == 1
    assert sweep.find_stuck([{"id": "r", "status": "in_progress", "latest_line": None}], NOW) == []
