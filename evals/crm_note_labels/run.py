"""The runner of the note-labels eval: ``python -m evals.crm_note_labels.run`` (WS-53 CRM-Q26).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q26 item 11.

``--scripted`` packs the notes with the REAL :func:`note_labels.pack`, the
requests that the step sends. It checks the shape of each request, answers
each question from ``scripted_answers.json``, and reads each answer with the
REAL :func:`note_labels.reader`, the reader that writes each row. Then it
scores the rows with :mod:`evals.crm_note_labels.checkers`. It calls no
model, no Router and no database.

The model sweep asks the decide tier through the Router on a local stack.
It is not built yet, so without ``--scripted`` the runner says so and exits
with code 2.

Exit codes: 0 every bar passed, 1 a bar failed, 2 the run cannot start.
"""
from __future__ import annotations

import argparse
import json
import sys
from types import MappingProxyType
from typing import Any

from gateway import decide_features as df
from gateway.crm_sync import note_labels as nl

from evals.crm_note_labels import checkers
from evals.crm_note_labels.dataset import EvalNote, load_answers, load_notes

EXIT_PASS, EXIT_FAIL, EXIT_NO_GO = 0, 1, 2


def _answer(question: Any, scripted: dict[str, Any]) -> Any:
    """One typed answer, in the shape of the ``acb_llm`` facade."""
    import acb_llm

    if question.type == "boolean":
        return acb_llm.BooleanAnswer(probability=scripted["probability"])
    if question.type == "score":
        return acb_llm.ScoreAnswer(
            score=scripted["score"],
            probabilities=MappingProxyType(dict(scripted.get("probabilities") or {})),
            confidence=scripted.get("confidence"), level=scripted.get("level"))
    return acb_llm.ChoiceAnswer(
        choice=scripted["choice"],
        probabilities=MappingProxyType(dict(scripted.get("probabilities") or {})),
        confidence=scripted.get("confidence"))


def check_shape(state: dict[str, Any], questions: dict[str, Any]) -> list[str]:
    """The problems of one request: too many notes or questions, a body past
    the clip, or a question that names no note of its state."""
    problems = []
    refs = [n["id"] for n in state["notes"]]
    if len(refs) > nl.NOTES_PER_REQUEST:
        problems.append(f"{refs[0]}:notes={len(refs)}")
    if len(questions) > df.QUESTION_LIMIT:
        problems.append(f"{refs[0]}:questions={len(questions)}")
    for note in state["notes"]:
        if len(note["body"]) > nl.BODY_CLIP:
            problems.append(f"{note['id']}:body={len(note['body'])}")
    for qid, question in questions.items():
        ref = qid.split(".", 1)[0]
        if ref not in refs or f"note {ref}" not in question.instructions:
            problems.append(f"{qid}:unnamed")
    return problems


def _step_notes(notes: list[EvalNote]) -> list[Any]:
    return [nl.Note(id=n.id, subject=n.subject, body=n.body, body_md5=n.id) for n in notes]


def run_scripted(
    notes: list[EvalNote], answers: dict[str, dict[str, Any]],
) -> tuple[checkers.Produced, list[str]]:
    """Each row the reader makes, by note id, and the shape problems.

    *answers* is a parameter so that a test can pass a changed set and see a
    bar fail (R7)."""
    import acb_llm

    produced: checkers.Produced = {}
    problems: list[str] = []
    for index, (state, questions, refs) in enumerate(nl.pack(_step_notes(notes))):
        problems.extend(check_shape(state, questions))
        ref_to_id = {ref: note.id for ref, note in refs}
        built: dict[str, Any] = {}
        for qid, question in questions.items():
            ref, part = qid.split(".", 1)
            scripted = answers.get(ref_to_id[ref], {}).get(part)
            if scripted is not None:
                built[qid] = _answer(question, scripted)
        decision = acb_llm.Decision(answers=MappingProxyType(built),
                                    request_id=f"scripted-{index}")
        rows, _fields = nl.reader(refs)(decision)
        for row in rows:
            produced[row["activity_id"]] = row
    return produced, problems


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m evals.crm_note_labels.run",
                                description=__doc__.splitlines()[0])
    p.add_argument("--scripted", action="store_true",
                   help="replay scripted_answers.json, and call no model")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.scripted:
        print("The model sweep is not built yet (crm_platform.md 18.11.2). "
              "Run with --scripted.", file=sys.stderr)
        return EXIT_NO_GO
    notes = load_notes()
    produced, problems = run_scripted(notes, load_answers())
    result = checkers.score(notes, produced, scripted=True, shape_problems=problems)
    report = {"mode": "scripted", "feature": nl.FEATURE,
              "requests": len(nl.pack(_step_notes(notes))), **result.summary()}
    sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
    return EXIT_PASS if result.passed() else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
