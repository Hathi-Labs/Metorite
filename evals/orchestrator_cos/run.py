"""The chief-of-staff eval of the orchestrator: ``python -m evals.orchestrator_cos.run``.

Spec: ``project-docs/specs/agent_writing_voice.md`` §11 (owner, 2026-10-13).

Twelve fixed cases (``cases.json``). Each case is one member turn: an ask,
and the reads the orchestrator already ran (a delegation, a retrieval), as
tool calls and their results. The model writes the reply. The deterministic
checks of :func:`check` score it first, and an optional model judge adds a
rubric score.

``--scripted`` scores the hand-written answers of ``scripted_answers.json``.
Each ``good`` answer must pass every check, and each ``bad`` answer must
fail exactly its ``expected_fail`` checks. It calls no model, so the unit job
runs it (``tests/unit/test_orchestrator_cos_eval.py``).

``--live`` is a MANUAL run. It needs ``LITELLM_BASE_URL`` (the Router), and
it goes through ``evals/_runner.post_chat_completion``, the one eval HTTP
call. It builds the orchestrator's system prompt the way the executor builds
it (:func:`built_prompt`), or reads ``--prompt-file``. ``--arm`` names the
arm, and the run adds it to ``--out``, so a baseline arm and a new arm sit in
one file. ``--report`` prints the table of every arm in ``--out``.

Exit codes: 0 pass (or a live run that finished), 1 a scripted mismatch,
2 the run cannot start.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

from acb_llm.voice import voice_lint

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
EXIT_PASS, EXIT_FAIL, EXIT_NO_GO = 0, 1, 2

#: The soft cap of a routine reply, in words of prose (spec §11.2). A card's
#: text does not count: it folds.
ROUTINE_CAP = 120

#: The longest run of words a routine reply may copy from one read. A longer
#: run is a relay, not a summary.
COPY_RUN = 20

#: The date line every case adds after the system prompt.
TODAY = "Today is Tuesday 13 Oct 2026."

#: The check ids, in report order.
CHECKS: tuple[str, ...] = (
    "bluf", "length", "no_dump", "no_relay", "recommendation", "mentions",
    "keeps_deliverable", "voice",
)

UI_TOOL = "emit_generative_ui"


def load_cases() -> list[dict[str, Any]]:
    return json.loads((HERE / "cases.json").read_text(encoding="utf-8"))


def load_scripted() -> dict[str, dict[str, Any]]:
    data = json.loads((HERE / "scripted_answers.json").read_text(encoding="utf-8"))
    return data["answers"]


# ---------------------------------------------------------------------------
# The deterministic checks
# ---------------------------------------------------------------------------

_PREAMBLE = re.compile(
    r"\A\W*(?:great question|good question|certainly|absolutely|of course|sure\b|"
    r"okay\b|ok\b|alright\b|let me\b|i'll\b|i will\b|i've\b|i have (?:checked|looked|pulled|asked|gathered)|"
    r"i checked|i looked|i pulled|i asked|i gathered|i ran|based on\b|according to\b|"
    r"to answer\b|you asked\b|thanks\b|here(?:'s| is| are)\b)",
    re.IGNORECASE,
)
#: A deliverable may open by naming itself ("Here is the draft").
_HERE_IS = re.compile(r"\A\W*here(?:'s| is| are)\b", re.IGNORECASE)
_RECOMMEND = re.compile(
    r"\b(recommend\w*|i'd go with|i would go with|go with|my pick|suggest\w*|"
    r"hire\b|counter at|counter with|counter-offer|take it|hold the price|"
    r"best option|the better choice|choose)\b",
    re.IGNORECASE,
)
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_RAW_ID = re.compile(r"\b(?:evt|run|msg|thr)_[0-9a-f]{6,}\b", re.I)
_JSON_LINE = re.compile(r'^\s*[\[{]\s*"|"\w+"\s*:\s*["\[{\d]', re.MULTILINE)
_ISO_STAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
_AGENT_BLOCK = re.compile(r"^\[[a-z]+(?:-[a-z]+)+\]\s*$", re.MULTILINE)
_FENCE = re.compile(r"^```", re.MULTILINE)
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'.,%#/-]*")


def words(text: str) -> int:
    """The words of *text*: tokens that hold a letter or a digit."""
    return len(_WORD.findall(text or ""))


def first_sentence(text: str) -> str:
    """The first sentence of *text*, with Markdown marks taken off its line."""
    for line in (text or "").splitlines():
        line = re.sub(r"^\s*(?:#+|[-*>]|\d+\.)\s*", "", line).replace("**", "").strip()
        if line:
            return re.split(r"(?<=[.!?])\s+", line, maxsplit=1)[0]
    return ""


def _norm_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def longest_copy(reply: str, source: str) -> int:
    """The longest run of words of *reply* that appears in *source*, in order."""
    a, b = _norm_words(reply), _norm_words(source)
    if not a or not b:
        return 0
    best = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def check(case: dict[str, Any], prose: str, card: str = "") -> dict[str, bool | None]:
    """Each check of *case* on one reply: True pass, False fail, None n/a.

    *prose* is the text the chat shows. *card* is the text of a card the
    reply drew, which folds, so it counts for detail and never for the cap.
    """
    c = case["checks"]
    routine = bool(c.get("routine"))
    both = f"{prose}\n{card}"
    lead = first_sentence(prose)
    out: dict[str, bool | None] = dict.fromkeys(CHECKS)

    opens_badly = bool(_PREAMBLE.match(prose or ""))
    if case.get("kind") == "deliverable" and _HERE_IS.match(prose or ""):
        opens_badly = False
    lead_terms = c.get("lead_any") or []
    lead_ok = not lead_terms or any(t.lower() in lead.lower() for t in lead_terms)
    out["bluf"] = bool(lead) and not opens_badly and lead_ok and words(lead) <= 35

    if routine:
        out["length"] = words(prose) <= int(c.get("max_words", ROUTINE_CAP))
    elif c.get("min_words"):
        out["length"] = words(both) >= int(c["min_words"])

    out["no_dump"] = not (
        _JSON_LINE.search(prose) or _UUID.search(_strip_citations(prose))
        or _RAW_ID.search(prose) or _ISO_STAMP.search(prose)
        or _AGENT_BLOCK.search(prose) or _FENCE.search(prose)
    )

    if routine:
        reads = [r["result"] for r in case.get("reads", [])]
        out["no_relay"] = all(longest_copy(prose, r) < COPY_RUN for r in reads)

    if c.get("needs_recommendation"):
        out["recommendation"] = bool(_RECOMMEND.search(prose))

    groups = c.get("must_mention") or []
    if groups:
        low = both.lower()
        out["mentions"] = all(any(t.lower() in low for t in g) for g in groups)

    keep = c.get("keep_verbatim") or []
    if keep:
        flat = " ".join(both.split())
        out["keeps_deliverable"] = all(" ".join(k.split()) in flat for k in keep)

    out["voice"] = not voice_lint(prose)
    return out


def _strip_citations(text: str) -> str:
    return re.sub(r"\[entity:[^\]]*\]", " ", text or "")


def passed(result: dict[str, bool | None]) -> bool:
    return all(v is not False for v in result.values())


# ---------------------------------------------------------------------------
# The scripted run
# ---------------------------------------------------------------------------


def run_scripted(
    cases: list[dict[str, Any]], answers: dict[str, dict[str, Any]],
) -> list[str]:
    """Each scripted answer whose checks differ from what it expects.

    *answers* is a parameter, so a test can pass a changed set and see the
    run fail (R7)."""
    bad: list[str] = []
    for case in cases:
        entry = answers.get(case["id"])
        if entry is None or "good" not in entry:
            bad.append(f"{case['id']}: no good answer")
            continue
        good = entry["good"]
        got = check(case, good["text"], good.get("card", ""))
        fails = sorted(k for k, v in got.items() if v is False)
        if fails:
            bad.append(f"{case['id']} good: fails {fails}")
        for i, wrong in enumerate(entry.get("bad", [])):
            got = check(case, wrong["text"], wrong.get("card", ""))
            fails = sorted(k for k, v in got.items() if v is False)
            if fails != sorted(wrong["expected_fail"]):
                bad.append(
                    f"{case['id']} bad[{i}]: fails {fails}, expected {sorted(wrong['expected_fail'])}",
                )
    return bad


# ---------------------------------------------------------------------------
# The live run
# ---------------------------------------------------------------------------


def built_prompt() -> str:
    """The orchestrator's system prompt, built as the executor builds it.

    Its own factory, its scope, then the real injection, with the database
    reads stubbed (the same stubs ``tests/unit/test_agent_voice.py`` uses).
    """
    import importlib.util  # noqa: PLC0415

    os.environ.setdefault("OPENAI_API_KEY", "sk-eval-dummy")
    import orchestrator._tool_injection as ti  # noqa: PLC0415
    import orchestrator.app_tools as app_tools  # noqa: PLC0415

    ti._build_registry_block = lambda: (  # type: ignore[assignment]
        "Registered agents: projects-assistant, task-manager, crm-assistant, "
        "email-assistant, whatsapp-assistant, app-builder, apis-config"
    )
    ti._load_disabled_skill_families = lambda name: frozenset()  # type: ignore[assignment]
    app_tools.load_app_action_tools = lambda name: []  # type: ignore[assignment]
    path = ROOT / "apps/agents/agent-orchestrator"
    cfg = json.loads((path / "config.json").read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location("cos_orchestrator", path / "agents.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    agents = mod.build_agents()
    ti._apply_own_tool_scope(agents, cfg.get("own_tool_scope") or None)
    ti._inject_agent_tools(
        agents, tool_scope=cfg.get("tool_scope") or None,
        agent_name="orchestrator", agent_config=cfg, no_egress=False,
    )
    return agents[0].default_options["instructions"]


def _tool_def(name: str) -> dict[str, Any]:
    params = {
        "call_agent": {"agent_name": "string", "message": "string"},
        "call_agents_parallel": {"tasks": "string"},
        "retrieve_entity_context": {"query": "string"},
        UI_TOOL: {"ui": "string"},
    }.get(name, {"input": "string"})
    return {"type": "function", "function": {
        "name": name,
        "description": (
            "Render a card in the chat. ui is a JSON string." if name == UI_TOOL
            else f"The {name} tool."
        ),
        "parameters": {
            "type": "object",
            "properties": {k: {"type": v} for k, v in params.items()},
            "required": list(params),
        },
    }}


def messages_for(case: dict[str, Any], system: str) -> list[dict[str, Any]]:
    """The turn as the model sees it: the prompt, any history, the ask, then
    each read as a tool call and its result."""
    msgs: list[dict[str, Any]] = [{"role": "system", "content": f"{system}\n\n{TODAY}"}]
    msgs += [dict(m) for m in case.get("history", [])]
    msgs.append({"role": "user", "content": case["ask"]})
    for i, read in enumerate(case.get("reads", [])):
        call_id = f"call_{case['id']}_{i}"
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": read["tool"], "arguments": json.dumps(read["args"])},
        }]})
        msgs.append({"role": "tool", "tool_call_id": call_id, "content": read["result"]})
    return msgs


def ask_live(case: dict[str, Any], system: str, model: str) -> dict[str, Any]:
    """One reply, through the Router. A card call gets "Rendered." and the
    model goes on. Any other call gets "no new data". Three rounds at most."""
    from evals._runner import post_chat_completion  # noqa: PLC0415

    msgs = messages_for(case, system)
    tools = [_tool_def(n) for n in sorted({r["tool"] for r in case["reads"]} | {UI_TOOL})]
    prose: list[str] = []
    cards: list[str] = []
    extra: list[str] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    served = ""
    for _ in range(3):
        data = post_chat_completion({
            "model": model, "messages": msgs, "tools": tools, "tool_choice": "auto",
            "temperature": 0, "max_tokens": 1500,
        }, timeout=180)
        served = data.get("model") or served
        for k in usage:
            usage[k] += int((data.get("usage") or {}).get(k) or 0)
        msg = data["choices"][0]["message"]
        if msg.get("content"):
            prose.append(msg["content"])
        calls = msg.get("tool_calls") or []
        if not calls:
            break
        msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for call in calls:
            fn = call.get("function") or {}
            if fn.get("name") == UI_TOOL:
                cards.append(_card_text(fn.get("arguments") or ""))
                reply = "Rendered."
            else:
                extra.append(fn.get("name") or "?")
                reply = "No new data. Answer from the results you have."
            msgs.append({"role": "tool", "tool_call_id": call.get("id"), "content": reply})
    return {"text": "\n\n".join(prose), "card": "\n".join(cards),
            "extra_calls": extra, "usage": usage, "served_model": served}


def _card_text(arguments: str) -> str:
    """The words a card shows: every string value of its JSON, joined."""
    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
            except ValueError:
                out.append(v)
                return
            if isinstance(parsed, (dict, list)):
                walk(parsed)
            else:
                out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(arguments)
    return " ".join(out)


JUDGE_SYSTEM = (
    "You grade one reply of a company's AI chief of staff to a busy decision "
    "maker. Score each criterion from 1 (poor) to 5 (excellent):\n"
    "bottom_line: the first sentence gives the answer, the status or the decision needed.\n"
    "distilled: it gives the few things that matter, ranked, and pastes no raw output.\n"
    "proactive: it raises real risks, deadlines or waiting items the member did not ask "
    "about, and adds no padding. Give 5 when nothing needed raising and it raised nothing.\n"
    "decision_support: when a choice is needed, it gives options, a recommendation and the "
    "trade-off, and the next step. Give 5 when no choice is needed.\n"
    "length_fit: the length fits the question (short for a fact, full for an explicit "
    "request for detail).\n"
    'Reply with JSON only: {"bottom_line": n, "distilled": n, "proactive": n, '
    '"decision_support": n, "length_fit": n}'
)
JUDGE_KEYS = ("bottom_line", "distilled", "proactive", "decision_support", "length_fit")


def judge(case: dict[str, Any], reply: dict[str, Any], model: str) -> dict[str, int]:
    from evals._runner import _call_llm  # noqa: PLC0415

    reads = "\n\n".join(r["result"] for r in case["reads"])
    user = (
        f"The member asked: {case['ask']}\n\nWhat the reads returned:\n{reads}\n\n"
        f"The reply (chat text):\n{reply['text']}\n\n"
        f"Text inside a card the reply drew (it folds):\n{reply.get('card') or '(none)'}"
    )
    raw = _call_llm(model, JUDGE_SYSTEM, user, json_mode=True)
    try:
        data = json.loads(raw)
        return {k: int(data[k]) for k in JUDGE_KEYS}
    except (ValueError, KeyError, TypeError):
        return {}


def run_live(
    cases: list[dict[str, Any]], system: str, model: str, judge_model: str | None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        reply = ask_live(case, system, model)
        result = check(case, reply["text"], reply["card"])
        row = {"id": case["id"], "kind": case["kind"], **reply,
               "words": words(reply["text"]), "checks": result, "pass": passed(result)}
        if judge_model:
            row["judge"] = judge(case, reply, judge_model)
        rows.append(row)
    return {"date": date.today().isoformat(), "model": model,
            "prompt_chars": len(system), "summary": summarise(rows, cases), "cases": rows}


def summarise(rows: list[dict[str, Any]], cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {c["id"]: c for c in cases}
    per_check: dict[str, list[int]] = {k: [0, 0] for k in CHECKS}
    for row in rows:
        for k, v in row["checks"].items():
            if v is not None:
                per_check[k][1] += 1
                per_check[k][0] += int(bool(v))
    routine = [r["words"] for r in rows if by_id[r["id"]]["checks"].get("routine")]
    judged = [r["judge"] for r in rows if r.get("judge")]
    return {
        "cases_passed": sum(1 for r in rows if r["pass"]),
        "cases": len(rows),
        "checks": {k: f"{p}/{n}" for k, (p, n) in per_check.items()},
        "routine_words_mean": round(sum(routine) / max(1, len(routine)), 1),
        "routine_words_max": max(routine or [0]),
        "judge_mean": (
            {k: round(sum(j[k] for j in judged) / len(judged), 2) for k in JUDGE_KEYS}
            if judged else None
        ),
    }


def report(arms: dict[str, Any]) -> str:
    names = list(arms)
    lines = ["| Measure | " + " | ".join(names) + " |", "|---" * (len(names) + 1) + "|"]

    def row(label: str, get: Any) -> None:
        lines.append(f"| {label} | " + " | ".join(str(get(arms[n]["summary"])) for n in names) + " |")

    row("Cases that pass every check", lambda s: f"{s['cases_passed']}/{s['cases']}")
    for k in CHECKS:
        row(f"`{k}`", lambda s, k=k: s["checks"][k])
    row("Routine reply, mean words", lambda s: s["routine_words_mean"])
    row("Routine reply, most words", lambda s: s["routine_words_max"])
    for k in JUDGE_KEYS:
        row(f"Judge `{k}` (1 to 5)", lambda s, k=k: (s["judge_mean"] or {}).get(k, "n/a"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scripted", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--report", action="store_true")
    mode.add_argument("--print-prompt", action="store_true")
    parser.add_argument("--model", default=os.environ.get("EVAL_COS_MODEL", "tier-balanced"))
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--prompt-file", default=None)
    parser.add_argument("--arm", default="after")
    parser.add_argument("--only", default=None, help="comma-separated case ids")
    parser.add_argument("--out", default=str(HERE / "results.json"))
    args = parser.parse_args(argv)

    cases = load_cases()
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    if args.scripted:
        bad = run_scripted(cases, load_scripted())
        for line in bad:
            print(line)
        print(f"{len(cases) - len({b.split()[0] for b in bad})} of {len(cases)} cases match")
        return EXIT_FAIL if bad else EXIT_PASS
    if args.print_prompt:
        Path(args.out).write_text(built_prompt(), encoding="utf-8")
        return EXIT_PASS

    out = Path(args.out)
    data = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {"arms": {}}
    if args.report:
        print(report(data["arms"]))
        return EXIT_PASS

    if not os.environ.get("LITELLM_BASE_URL"):
        print("--live needs LITELLM_BASE_URL (the Router or a local proxy).")
        return EXIT_NO_GO
    system = (
        Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file
        else built_prompt()
    )
    data["arms"][args.arm] = run_live(cases, system, args.model, args.judge_model)
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(report(data["arms"]))
    return EXIT_PASS


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
