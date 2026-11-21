"""The tune and held-out split of the chats, and the sealed results (task 3649).

A chat is held out when ``"holdout"`` is in ``Scenario.tags``. The tune chats
guide a fix. The held-out chats only tell whether the fix generalised, so a
fixer must never read their words, their answers or their rows.

Three rules live here:

1. :func:`split_of` names the split of a chat. A row keeps it as ``split``,
   so an old file or a chat that no longer exists reads as ``tune``.
2. :func:`save` writes a results file ``X.json`` that holds the tune rows
   in full and, for the held-out rows, COUNTS only (the block ``sealed``).
   The held-out rows go to the sibling ``X.holdout.json`` (:func:`sibling`).
   The public block is built from scratch from the counts. No key is
   removed from a held-out row, so a key that a later change adds to a
   row never leaks.
3. :func:`load` reads a results file and its sibling back into one dict.
   Each reader and writer goes through :func:`load` and :func:`save`.

The compare rule is in :mod:`.compare`.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

HOLDOUT_TAG = "holdout"
TUNE, HOLDOUT, ALL = "tune", "holdout", "all"
SPLITS = (TUNE, HOLDOUT, ALL)
#: What a console line says in place of the name of a held-out chat.
HIDDEN = "a held-out chat"


def split_of(sc: Any) -> str:
    """``holdout`` when the tags of the chat *sc* hold ``holdout``, else ``tune``."""
    return HOLDOUT if HOLDOUT_TAG in tuple(getattr(sc, "tags", ()) or ()) else TUNE


def scenario_tags(sid: str) -> tuple[str, ...]:
    """The tags of the chat *sid*, or ``()`` for an id that no chat has now."""
    from evals.one_assistant_measure.scenarios import scenario

    try:
        return tuple(scenario(sid).tags)
    except KeyError:
        return ()


def split_of_row(row: dict[str, Any]) -> str:
    """The split of a result row: its stamp, else the tags of its chat today."""
    stamp = row.get("split")
    if stamp in (TUNE, HOLDOUT):
        return str(stamp)
    return HOLDOUT if HOLDOUT_TAG in scenario_tags(str(row.get("scenario") or "")) else TUNE


def tags_of_row(row: dict[str, Any]) -> tuple[str, ...]:
    """The tags of a result row: its stamp, else the tags of its chat today."""
    if isinstance(row.get("tags"), list):
        return tuple(str(t) for t in row["tags"])
    return scenario_tags(str(row.get("scenario") or ""))


def label(sc: Any) -> str:
    """The name of a chat on the console: its id, or :data:`HIDDEN`."""
    return HIDDEN if split_of(sc) == HOLDOUT else str(sc.id)


def check_unique_ids(scenarios: tuple[Any, ...] | None = None) -> None:
    """Raise ``ValueError`` when one chat id is in two chats of *scenarios*
    (default: every chat). ``scenario(sid)`` returns the first match, so a
    twin in the tune file and the holdout file would hide one of them."""
    if scenarios is None:
        from evals.one_assistant_measure.scenarios import all_scenarios

        scenarios = all_scenarios()
    seen: set[str] = set()
    twins = sorted({sc.id for sc in scenarios if sc.id in seen or seen.add(sc.id)})  # type: ignore[func-returns-value]
    if twins:
        raise ValueError(f"a chat id is used by two chats: {', '.join(twins)}")


def keep(scenarios: tuple[Any, ...], which: str) -> tuple[Any, ...]:
    """The chats of *scenarios* in the split *which* (``--split``)."""
    check_unique_ids()
    if which == ALL:
        return scenarios
    return tuple(sc for sc in scenarios if split_of(sc) == which)


def holdout_ids() -> frozenset[str]:
    from evals.one_assistant_measure.scenarios import all_scenarios

    check_unique_ids(all_scenarios())
    return frozenset(sc.id for sc in all_scenarios() if split_of(sc) == HOLDOUT)


def sibling(out: Path) -> Path:
    """``X.holdout.json`` of ``X.json``."""
    return out.with_name(out.stem + ".holdout" + out.suffix)


# ── the counts ───────────────────────────────────────────────────────────────


def turn_passes(row: dict[str, Any]) -> bool | None:
    """True when every check of the row that gives a value passes. None for a
    capped turn, which no check scores."""
    if row.get("capped"):
        return None
    return all(bool(v) for v in (row.get("checks") or {}).values() if v is not None)


def counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The only facts of held-out rows that a public file may hold.

    For each arm: the turns, the turn passes and the capped turns of each
    repeat, and the passes and fails of each check name. Nothing else."""
    out: dict[str, Any] = {}
    for r in rows:
        arm = out.setdefault(str(r.get("arm_label") or r.get("arm")),
                             {"repeats": defaultdict(lambda: {"turns": 0, "passed": 0, "capped": 0}),
                              "checks": defaultdict(lambda: {"pass": 0, "fail": 0}),
                              "cost_usd": 0.0})
        arm["cost_usd"] += sum(float(c.get("cost_usd") or 0.0) for c in r.get("model_calls") or []
                               ) + float(r.get("unseen_usd") or 0.0)
        rep = arm["repeats"][str(r.get("repeat", 1))]
        passed = turn_passes(r)
        if passed is None:
            rep["capped"] += 1
            continue
        rep["turns"] += 1
        rep["passed"] += int(passed)
        for name, value in (r.get("checks") or {}).items():
            if value is not None:
                arm["checks"][str(name)]["pass" if value else "fail"] += 1
    return {a: {"repeats": dict(sorted(v["repeats"].items())),
                "checks": dict(sorted(v["checks"].items())),
                "cost_usd": round(v["cost_usd"], 4)} for a, v in sorted(out.items())}


def _scrub(public: dict[str, Any], hidden: frozenset[str]) -> None:
    """Take the id of a held-out chat out of the header keys that name chats."""
    if isinstance(public.get("dropped"), list):
        public["dropped"] = [{**d, "scenario": HIDDEN} if d.get("scenario") in hidden else d
                             for d in public["dropped"]]
    if isinstance(public.get("chunks_run"), list):
        public["chunks_run"] = [
            {**c, "scenarios": [s for s in c.get("scenarios") or [] if s not in hidden]}
            for c in public["chunks_run"]]
    if isinstance(public.get("replaced"), list):
        public["replaced"] = [d for d in public["replaced"] if d.get("scenario") not in hidden]
    stopped = public.get("stopped")
    if isinstance(stopped, str):
        public["stopped"] = _hide_ids(stopped, hidden)
    elif isinstance(stopped, list):
        public["stopped"] = [_hide_ids(s, hidden) if isinstance(s, str) else s for s in stopped]
    resc = public.get("rescored")
    if isinstance(resc, dict):
        lines = resc.get("changes") or []
        resc["changes"] = public_lines(lines, hidden)


def _hide_ids(text: str, hidden: frozenset[str]) -> str:
    for sid in sorted(hidden, key=len, reverse=True):
        text = text.replace(sid, HIDDEN)
    return text


def public_lines(lines: list[str], hidden: frozenset[str] | None = None) -> list[str]:
    """*lines* without the lines that name a held-out chat. One line says how
    many it left out."""
    ids = holdout_ids() if hidden is None else hidden
    kept = [ln for ln in lines if not any(f" {sid}#" in f" {ln}" for sid in ids)]
    left = len(lines) - len(kept)
    return kept + ([f"({left} changes in held-out chats, counted only)"] if left else [])


def seal(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """``(public, held)`` of the full results *data*.

    *public* holds the tune rows and the block ``sealed`` with the counts of
    the held-out rows. *held* is the body of the sibling file, or None when
    *data* has no held-out row. A *data* that already has a ``sealed`` block
    and no held-out rows keeps the block (a file read without its sibling)."""
    rows = list(data.get("rows") or [])
    tune = [r for r in rows if split_of_row(r) == TUNE]
    held = [r for r in rows if split_of_row(r) == HOLDOUT]
    public = {k: v for k, v in data.items() if k != "rows"}
    public["rows"] = tune
    hidden = frozenset(str(r.get("scenario")) for r in held) | holdout_ids()
    if held:
        public["sealed"] = {"note": "Held-out chats. Counts only. The rows are in the sibling file.",
                            "rows": len(held), "arms": counts(held)}
    _scrub(public, hidden)
    body = None
    if held:
        body = {"note": "SEALED. A fixer brief never names this file.",
                "sha": data.get("sha"), "date": data.get("date"), "rows": held}
    return public, body


# ── the file pair ────────────────────────────────────────────────────────────


def load(path: Path) -> dict[str, Any]:
    """The results of *path* and of its sibling, as one dict with every row."""
    data = json.loads(path.read_text(encoding="utf-8"))
    side = sibling(path)
    if side.is_file():
        data["rows"] = [*data.get("rows", []),
                        *json.loads(side.read_text(encoding="utf-8")).get("rows", [])]
    return data


def save(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Write the public file and the sibling. Returns the public dict."""
    public, body = seal(data)
    path.write_text(json.dumps(public, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    side = sibling(path)
    if body is not None:
        side.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    elif side.is_file() and "sealed" not in public:
        side.unlink()
    return public
