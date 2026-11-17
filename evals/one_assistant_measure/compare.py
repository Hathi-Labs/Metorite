"""The win margin: compare two results files (task 3649, ``--compare B.json,A.json``).

B is the run before a change. A is the run after it. Each file holds two
repeats of the same chats on unchanged code per file (the first two repeats
that it holds). The rule below says when A wins. It reads the sibling file of
each side, so the held-out turns count. It prints counts only for them.

A **turn passes** when every check that gives a value passes (a check that
gives None does not count). A capped turn is left out. A turn is scored only
when it is present and not capped in all four runs (B1, B2, A1, A2).

The **noise** ``n`` is the number of scored turns, tune and held-out together,
whose pass or fail differs between repeat 1 and repeat 2 of B. Two repeats of
unchanged code give it.

A **wins** when all four rules hold:

1. Each chat tagged ``case:<k>`` passes in both repeats of A. A case that
   failed in B is then fixed. A case that passed in both repeats of B stays
   passed. A case that is missing or capped in A fails.
2. On T (the tune chats tagged ``t_tag``, default ``t2874``): the passes of A
   minus the passes of B, summed over both repeats, is at least
   ``max(2, n + 1)``.
3. On H (the held-out chats): the passes of A (summed over both repeats) are at
   least the passes of B minus ``n``.
4. On E (the tune chats of the ``email`` set, minus T): the same as rule 3.

The passes of rules 2 to 4 are summed over both repeats. ``n`` is a count of
turns of one repeat pair, so a sum over two repeats is the stricter reading.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from evals.one_assistant_measure import splits

EXIT_WIN, EXIT_NO_GO, EXIT_LOSS = 0, 2, 3
DEFAULT_T_TAG = "t2874"
DEFAULT_ARM = "pack"
CASE_PREFIX = "case:"

Key = tuple[str, int]


class CompareError(Exception):
    """The two files cannot be compared. The exit code is 2."""


def _arm_rows(data: dict[str, Any], arm: str) -> list[dict[str, Any]]:
    return [r for r in data.get("rows") or [] if (r.get("arm_label") or r.get("arm")) == arm]


def _repeats(rows: list[dict[str, Any]]) -> list[int]:
    return sorted({int(r.get("repeat", 1)) for r in rows})[:2]


def _by_repeat(rows: list[dict[str, Any]], repeats: list[int]) -> dict[int, dict[Key, dict[str, Any]]]:
    out: dict[int, dict[Key, dict[str, Any]]] = {rep: {} for rep in repeats}
    for r in rows:
        rep = int(r.get("repeat", 1))
        if rep in out:
            out[rep][(str(r["scenario"]), int(r["turn"]))] = r
    return out


class Side:
    """One results file, read for one arm: the turn results of its two repeats."""

    def __init__(self, path: Path, arm: str) -> None:
        data = splits.load(path)
        sealed = data.get("sealed") or {}
        held_rows = [r for r in data.get("rows") or [] if splits.split_of_row(r) == splits.HOLDOUT]
        if sealed.get("rows") and not held_rows:
            raise CompareError(f"{path.name}: it holds held-out counts but its sibling file "
                               f"{splits.sibling(path).name} is missing")
        self.path = path
        self.header = data.get("mirror_prod")
        rows = _arm_rows(data, arm)
        self.repeats = _repeats(rows)
        if len(self.repeats) < 2:
            raise CompareError(f"{path.name}: arm {arm!r} needs two repeats, it has "
                               f"{self.repeats or 'none'}")
        self.by_repeat = _by_repeat(rows, self.repeats)
        self.rows = {rep: rs for rep, rs in self.by_repeat.items()}

    def result(self, rep_index: int, key: Key) -> bool | None:
        row = self.by_repeat[self.repeats[rep_index]].get(key)
        return None if row is None else splits.turn_passes(row)

    def row(self, key: Key) -> dict[str, Any] | None:
        for rep in self.repeats:
            if key in self.by_repeat[rep]:
                return self.by_repeat[rep][key]
        return None


def _case_of(tags: tuple[str, ...]) -> str:
    return next((t for t in tags if t.startswith(CASE_PREFIX)), "")


def compare(b_path: Path, a_path: Path, *, t_tag: str = DEFAULT_T_TAG,  # noqa: C901 - one rule
            arm: str = DEFAULT_ARM) -> tuple[str, int]:
    """``(report, exit code)`` of the win rule on B (before) and A (after)."""
    try:
        b, a = Side(b_path, arm), Side(a_path, arm)
    except CompareError as exc:
        return f"NO-GO: {exc}", EXIT_NO_GO
    if b.header != a.header:
        return ("NO-GO: the two files ran with different mirror_prod headers. "
                "Compare runs of one configuration."), EXIT_NO_GO

    every = {k for rep in b.repeats for k in b.by_repeat[rep]} | {
        k for rep in a.repeats for k in a.by_repeat[rep]}
    scored: list[Key] = []
    dropped = 0
    for key in sorted(every):
        if all(side.result(i, key) is not None for side in (b, a) for i in (0, 1)):
            scored.append(key)
        else:
            dropped += 1

    def row_of(key: Key) -> dict[str, Any]:
        return b.row(key) or a.row(key) or {}

    group: dict[Key, str] = {}
    for key in scored:
        row = row_of(key)
        tags = splits.tags_of_row(row)
        if splits.split_of_row(row) == splits.HOLDOUT:
            group[key] = "H"
        elif t_tag in tags:
            group[key] = "T"
        elif row.get("set") == "email":
            group[key] = "E"
        else:
            group[key] = "other"

    noise = sum(1 for k in scored if b.result(0, k) != b.result(1, k))
    lines = [f"compare: B {b_path.name} (before), A {a_path.name} (after), arm {arm}",
             f"repeats: B {b.repeats}, A {a.repeats}",
             f"scored turns: {len(scored)}. Left out (missing or capped in one run): {dropped}.",
             f"noise n = {noise} of {len(scored)} turns (pass or fail differs between repeat 1 "
             "and repeat 2 of B)", ""]

    def passes(side: Side, name: str) -> list[int]:
        return [sum(1 for k in scored if group[k] == name and side.result(i, k)) for i in (0, 1)]

    verdict: dict[str, bool] = {}
    stats: dict[str, tuple[int, list[int], list[int]]] = {}
    for name, title in (("T", f"T (tune, tag {t_tag})"), ("H", "H (held-out)"),
                        ("E", "E (tune, email set, not T)"),
                        ("other", "Other tune (not T, not E)")):
        turns = sum(1 for k in scored if group[k] == name)
        pb, pa = passes(b, name), passes(a, name)
        stats[name] = (turns, pb, pa)
        up = down = 0
        named_up: list[str] = []
        named_down: list[str] = []
        for i in (0, 1):
            for k in scored:
                if group[k] != name:
                    continue
                before, after = b.result(i, k), a.result(i, k)
                if before is False and after is True:
                    up += 1
                    named_up.append(f"{k[0]}#{k[1]} r{b.repeats[i]}")
                elif before is True and after is False:
                    down += 1
                    named_down.append(f"{k[0]}#{k[1]} r{b.repeats[i]}")
        lines.append(f"{title}: {turns} turns. Passes B {pb}, A {pa}. "
                     f"Flipped to pass: {up}. Flipped to fail: {down}.")
        if name != "H":
            if named_up:
                lines.append("  to pass: " + ", ".join(named_up))
            if named_down:
                lines.append("  to fail: " + ", ".join(named_down))
    tune_turns = sum(1 for g in group.values() if g != "H")
    tune_b = [sum(1 for k in scored if group[k] != "H" and b.result(i, k)) for i in (0, 1)]
    tune_a = [sum(1 for k in scored if group[k] != "H" and a.result(i, k)) for i in (0, 1)]
    lines.append(f"Tune total: {tune_turns} turns. Passes B {tune_b}, A {tune_a}.")
    if stats["H"][0] == 0:
        lines.append("H: 0 turns. Rule 3 holds with no evidence.")

    # Rule 1: the cases.
    chats: dict[str, list[Key]] = {}
    case_of_chat: dict[str, str] = {}
    for key in every:
        case = _case_of(splits.tags_of_row(row_of(key)))
        if case:
            chats.setdefault(key[0], []).append(key)
            case_of_chat[key[0]] = case
    lines.append("")
    rule1 = True
    for sid in sorted(chats):
        keys = sorted(chats[sid])

        def state(side: Side, i: int, keys: list[Key] = keys) -> bool | None:
            results = [side.result(i, k) for k in keys]
            if any(r is None for r in results):
                return None
            return all(results)

        b_state = [state(b, 0), state(b, 1)]
        a_state = [state(a, 0), state(a, 1)]
        b_pass = b_state == [True, True]
        a_pass = a_state == [True, True]
        kind = "kept" if b_pass else "fixed"
        rule1 = rule1 and a_pass
        name = case_of_chat[sid] if splits.split_of_row(row_of(keys[0])) == splits.TUNE else "held-out case"
        sid_shown = sid if splits.split_of_row(row_of(keys[0])) == splits.TUNE else splits.HIDDEN
        lines.append(f"  {name} {sid_shown}: B {b_state}, A {a_state}: "
                     f"{'ok' if a_pass else 'FAILS'} ({kind})")
    if not chats:
        lines.append("  no chat tagged case:<k> in the files")
    verdict["1"] = rule1

    n = noise
    t_turns, tb, ta = stats["T"]
    gain = sum(ta) - sum(tb)
    need = max(2, n + 1)
    verdict["2"] = gain >= need
    h_turns, hb, ha = stats["H"]
    verdict["3"] = sum(ha) >= sum(hb) - n
    _, eb, ea = stats["E"]
    verdict["4"] = sum(ea) >= sum(eb) - n
    lines += [
        "",
        f"rule 1  every case chat passes both repeats of A: {'ok' if verdict['1'] else 'FAILS'}",
        f"rule 2  T gain {gain} (A {sum(ta)} - B {sum(tb)}) needs at least {need}: "
        f"{'ok' if verdict['2'] else 'FAILS'}",
        f"rule 3  H A {sum(ha)} >= B {sum(hb)} - n {n}: {'ok' if verdict['3'] else 'FAILS'}",
        f"rule 4  E A {sum(ea)} >= B {sum(eb)} - n {n}: {'ok' if verdict['4'] else 'FAILS'}",
    ]
    win = all(verdict.values())
    lines.append("VERDICT: " + ("WIN" if win else "LOSS"))
    del t_turns, h_turns
    return "\n".join(lines), EXIT_WIN if win else EXIT_LOSS
