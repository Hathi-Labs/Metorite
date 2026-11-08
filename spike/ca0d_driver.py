"""CA-0d spike driver: runs code_task's MAF session (run_maf_code_session) on seeded tasks.

Not product code. Budget guard: sums usage from every chat completion and stops at --cap-usd.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

PRICE_IN, PRICE_OUT = 3.0 / 1e6, 15.0 / 1e6  # ASSUMED Sonnet-class list price, no cache discount
STATS = {"in": 0, "out": 0, "calls": 0, "tools": {}, "cap": 3.0, "spent_prior": 0.0}


class BudgetStop(Exception):
    pass


def cost() -> float:
    return STATS["in"] * PRICE_IN + STATS["out"] * PRICE_OUT


def install_usage_hook() -> None:
    from openai.resources.chat.completions import AsyncCompletions
    orig = AsyncCompletions.create

    async def create(self, *a, **kw):
        if STATS["spent_prior"] + cost() >= STATS["cap"]:
            raise BudgetStop(f"budget cap USD {STATS['cap']} reached")
        STATS["calls"] += 1
        if kw.get("stream"):
            kw.setdefault("stream_options", {"include_usage": True})
        res = await orig(self, *a, **kw)
        if not kw.get("stream"):
            u = getattr(res, "usage", None)
            if u:
                STATS["in"] += u.prompt_tokens
                STATS["out"] += u.completion_tokens
            for c in res.choices:
                for tc in (c.message.tool_calls or []):
                    STATS["tools"][tc.function.name] = STATS["tools"].get(tc.function.name, 0) + 1
            return res

        async def gen():
            seen = set()
            async for ch in res:
                u = getattr(ch, "usage", None)
                if u:
                    STATS["in"] += u.prompt_tokens
                    STATS["out"] += u.completion_tokens
                for c in ch.choices or []:
                    for tc in (c.delta.tool_calls or []):
                        n = tc.function.name if tc.function else None
                        if n and (tc.index, ch.id) not in seen:
                            seen.add((tc.index, ch.id))
                            STATS["tools"][n] = STATS["tools"].get(n, 0) + 1
                yield ch
        return gen()
    AsyncCompletions.create = create


def sh(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True, timeout=120)
    return r.returncode, (r.stdout + r.stderr)[-600:]


def _t2_check(ws):
    r = subprocess.run("python -m app.cli --json", cwd=ws, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        return False
    rows = json.loads(r.stdout.strip())
    plain = subprocess.run("python -m app.cli", cwd=ws, shell=True, capture_output=True, text=True).stdout
    return (rows[0]["name"] == "bolt" and "bolt: 4" in plain and "--json" in (ws / "README.md").read_text()
            and "render_json" in (ws / "app/report.py").read_text())


def _t3_check(ws):
    p = ws / "tests/test_slug.py"
    if not p.exists():
        return False
    if sh("python -m pytest -q tests", ws)[0] != 0:
        return False
    src = (ws / "slug.py").read_text()
    n = p.read_text().count("def test_")
    (ws / "slug.py").write_text(src.replace("s = s[:max_len].rstrip('-')", "s = s[:max_len]"))
    caught = sh("python -m pytest -q tests", ws)[0] != 0
    (ws / "slug.py").write_text(src)
    return n >= 4 and caught


def T():
    t = {}
    t["T1-fix"] = ("small fix", {
        "calc/stats.py": "def mean(xs):\n    return sum(xs) / (len(xs) - 1)\n\ndef median(xs):\n    s = sorted(xs)\n    n = len(s)\n    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2\n",
        "tests/test_stats.py": "from calc.stats import mean, median\n\ndef test_mean():\n    assert mean([1, 2, 3, 4]) == 2.5\n\ndef test_median():\n    assert median([3, 1, 2]) == 2\n    assert median([4, 1, 2, 3]) == 2.5\n",
        "calc/__init__.py": ""},
        "The test suite in this workspace fails. Find the bug in `calc/stats.py`, fix the source (not the tests), and confirm with pytest.",
        lambda ws: sh("python -m pytest -q tests", ws)[0] == 0 and "len(xs) - 1" not in (ws / "calc/stats.py").read_text())
    t["T2-multifile"] = ("multi-file change", {
        "app/cli.py": "import sys\nfrom app.report import render\nfrom app.data import load\n\ndef main(argv):\n    rows = load()\n    print(render(rows))\n\nif __name__ == '__main__':\n    main(sys.argv[1:])\n",
        "app/report.py": "def render(rows):\n    return '\\n'.join(f\"{r['name']}: {r['qty']}\" for r in rows)\n",
        "app/data.py": "def load():\n    return [{'name': 'bolt', 'qty': 4}, {'name': 'nut', 'qty': 9}]\n",
        "app/__init__.py": "", "README.md": "# app\nRun `python -m app.cli`.\n"},
        "Add a `--json` flag to the CLI in `app/cli.py`. With it, print the rows as a JSON list (use a new function `render_json` in `app/report.py`). Without it, behaviour stays the same. Mention the flag in README.md. Show it works by running both forms.",
        _t2_check)
    t["T3-tests"] = ("test writing", {
        "slug.py": "import re\n\ndef slugify(text, max_len=20):\n    s = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')\n    if len(s) > max_len:\n        s = s[:max_len].rstrip('-')\n    return s\n"},
        "Write `tests/test_slug.py` with pytest tests for `slugify` in `slug.py`: cover case, punctuation, collapsing separators, trimming dashes, truncation at max_len (including a trailing dash after truncation) and empty input. Run them and make sure they pass.",
        _t3_check)
    t["T4-refactor"] = ("refactor", {
        "price/a.py": "def total_a(items):\n    t = 0\n    for i in items:\n        t += i['price'] * i['qty'] * (1 - i.get('discount', 0))\n    return round(t, 2)\n",
        "price/b.py": "def total_b(items):\n    t = 0\n    for i in items:\n        t += i['price'] * i['qty'] * (1 - i.get('discount', 0))\n    return round(t * 1.18, 2)\n",
        "price/__init__.py": "",
        "tests/test_price.py": "from price.a import total_a\nfrom price.b import total_b\nI = [{'price': 10, 'qty': 2, 'discount': 0.5}, {'price': 3, 'qty': 1}]\n\ndef test_a():\n    assert total_a(I) == 13.0\n\ndef test_b():\n    assert total_b(I) == 15.34\n"},
        "Refactor `price/a.py` and `price/b.py`: they duplicate the line-total logic. Move it into one shared helper `line_total(item)` in a new `price/common.py` and use it from both. Public function behaviour must not change. Run the tests.",
        lambda ws: sh("python -m pytest -q tests", ws)[0] == 0 and (ws / "price/common.py").exists() and "line_total" in (ws / "price/a.py").read_text() and "line_total" in (ws / "price/b.py").read_text())
    t["T5-script"] = ("data script", {
        "sales.csv": "region,amount\nnorth,100\nsouth,250\nnorth,50\neast,75\nsouth,25\n"},
        "Write `summarize.py` that reads `sales.csv` and writes `summary.json` with the total amount per region (a JSON object region->total). Run it.",
        lambda ws: (ws / "summary.json").exists() and json.loads((ws / "summary.json").read_text()) == {"north": 150, "south": 275, "east": 75})
    t["T6-debug"] = ("multi-step debug", {
        "inv/__init__.py": "",
        "inv/parse.py": "def parse_line(line):\n    name, qty = line.split(',')\n    return name.strip(), int(qty)\n",
        "inv/store.py": "from inv.parse import parse_line\n\ndef load(text):\n    out = {}\n    for line in text.splitlines():\n        n, q = parse_line(line)\n        out[n] = q\n    return out\n",
        "tests/test_store.py": "from inv.store import load\n\ndef test_dupes_add_up():\n    assert load('a, 2\\nb, 3\\na, 4') == {'a': 6, 'b': 3}\n\ndef test_blank_lines():\n    assert load('a, 1\\n\\nb, 2\\n') == {'a': 1, 'b': 2}\n\ndef test_bad_qty_skipped():\n    assert load('a, 1\\nb, x') == {'a': 1}\n"},
        "Three tests in this workspace fail. Fix the code in `inv/` (not the tests) until all pass. Run pytest to confirm.",
        lambda ws: sh("python -m pytest -q tests", ws)[0] == 0)
    return t


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="all")
    ap.add_argument("--tier", default="tier-balanced")
    ap.add_argument("--cap-usd", type=float, default=3.0)
    ap.add_argument("--spent-prior", type=float, default=0.0)
    ap.add_argument("--harness-defaults", action="store_true", help="drop disable_todo/disable_mode")
    ap.add_argument("--out", default="C:/e43/ca0d")
    ap.add_argument("--timeout", type=float, default=420)
    a = ap.parse_args()
    STATS["cap"], STATS["spent_prior"] = a.cap_usd, a.spent_prior
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    from acb_common import get_settings
    from acb_skills.agent_paths import ensure_state_dir
    from acb_skills.write_artifact import bind_artifact_context
    from orchestrator import code_session as cs
    from orchestrator import sandbox_broker as sb
    settings = get_settings()
    Path("C:/e43").mkdir(exist_ok=True)
    clone = Path(tempfile.mkdtemp(prefix="c", dir="C:/e43"))
    setattr(settings, "agents_clone_dir", str(clone))
    setattr(settings, "maf_coding_scope", "code_task:*")
    if a.harness_defaults:
        for k in ("disable_todo", "disable_mode"):
            cs.HARNESS_FLAGS.pop(k, None)
    install_usage_hook()
    tasks = T()
    sel = list(tasks) if a.tasks == "all" else a.tasks.split(",")
    for tid in sel:
        kind, files, prompt, check = tasks[tid]
        inst = f"m:{tid.lower()[:2]}{uuid.uuid4().hex[:4]}"
        agent = "spike-agent"
        ws = ensure_state_dir(agent, inst).resolve()
        for f, body in files.items():
            (ws / f).parent.mkdir(parents=True, exist_ok=True)
            (ws / f).write_text(body, encoding="utf-8")
        binding = sb.RunBinding(org="org-spike", agent=agent, thread=str(uuid.uuid4()), instance=inst,
                                workspace=ws, member="spike@example.invalid")
        sb.read_run_binding = lambda b=binding: b
        bind_artifact_context(agent_name=agent, session_id=binding.thread, workspace_root=str(ws),
                              instance=inst, member=binding.member, org="org-spike")
        b_in, b_out, b_calls = STATS["in"], STATS["out"], STATS["calls"]
        tools0 = dict(STATS["tools"])
        t0 = time.time()
        status, err, report = "pass", "", ""
        try:
            report = await cs.run_maf_code_session(task=prompt, workspace=str(ws), model=a.tier, timeout=a.timeout)
        except BudgetStop as e:
            status, err = "budget-stop", str(e)
        except BaseException as e:
            status, err = "error", f"{type(e).__name__}: {str(e)[:400]}"
        wall = time.time() - t0
        if status == "pass":
            try:
                ok = bool(check(ws))
            except Exception as e:
                ok, err = False, f"checker {type(e).__name__}: {e}"
            status = "pass" if ok else "fail"
        rec = {"task": tid, "kind": kind, "status": status, "err": err, "wall_s": round(wall, 1),
               "in_tok": STATS["in"] - b_in, "out_tok": STATS["out"] - b_out, "model_calls": STATS["calls"] - b_calls,
               "cost_usd": round((STATS["in"] - b_in) * PRICE_IN + (STATS["out"] - b_out) * PRICE_OUT, 4),
               "tools": {k: v - tools0.get(k, 0) for k, v in STATS["tools"].items() if v - tools0.get(k, 0)},
               "report_head": report[:300], "flags": dict(cs.HARNESS_FLAGS)}
        print(json.dumps(rec), flush=True)
        (out / f"{tid}.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
        if status == "budget-stop":
            break
    print("TOTAL_USD", round(cost(), 4), "IN", STATS["in"], "OUT", STATS["out"])


if __name__ == "__main__":
    asyncio.run(main())
