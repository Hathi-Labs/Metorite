"""WS-BRIEF BR-7a — no job kind sends or changes anything without a policy.

Spec: ``project-docs/specs/core_brief.md`` §8 and §10 row 8. Code:
``gateway/jobs/worker.py`` (``register(effect=, policy=)``).

Four fences.

1. **The registry.** A fresh interpreter imports every module that registers
   a kind, as ``main.py`` does. Each kind then has an effect, and a ``send``
   or ``write`` kind has a policy. A fresh interpreter keeps the kinds that
   other tests register out of the list.
2. **The source.** Every ``register(...)`` call in ``gateway/`` states
   ``effect=``. So a module that ``main.py`` does not import yet cannot
   register a kind without one.
3. **The brief does not act.** The ``gateway/brief/`` package imports no send
   seam and no write route of another app.
4. **The planted violation.** Each fence goes red on a planted case, so a
   green run means the fence can see.

**Mutations (R7).**

* M1. Drop ``effect="read"`` from ``brief/monitor.py``. Fences 1 and 2 go red.
* M2. Add ``from gateway.reach import push_channel`` to ``brief/run.py``, or
  ``from gateway.reach.loop import send_brief``. Fence 3 goes red. Fence 3 is
  an ALLOWLIST of gateway imports, and it resolves relative imports.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from gateway.jobs import worker

_ROOT = Path(__file__).resolve().parents[2]
_GATEWAY = _ROOT / "apps/services/gateway/gateway"

#: Every module that registers a kind at import, as ``main.py`` imports them.
_MODULES = ("gateway.jobs.worker", "gateway.brief.run", "gateway.brief.monitor",
            "gateway.chat_summary_job", "gateway.align.check")

#: The ONLY gateway modules that the brief may import (an allowlist, so a new
#: send seam is refused until someone adds it here on purpose). ``reach.loop``
#: serves the reads ``resolve`` and ``snapshot``, and its senders stay out
#: (:data:`_ALLOWED_NAMES`). ``routes.shell.feed``, ``.state`` and ``.handled`` (BR-2b-1) are reads.
_ALLOWED = ("gateway.brief", "gateway.jobs", "gateway.reach.plan", "gateway.reach.loop",
            "gateway.my_day_loop", "gateway.routes.shell.feed", "gateway.routes.shell.state",
            "gateway.routes.shell.handled",
            "gateway.work_schedule", "gateway.db")
#: The names that the brief may take from a module of :data:`_ALLOWED` that
#: also holds a sender.
_ALLOWED_NAMES: dict[str, frozenset[str]] = {
    "gateway.reach.loop": frozenset({"resolve", "snapshot", "Snapshot"})}
#: ``from gateway.reach import <name>``: only these modules.
_PACKAGE_NAMES: dict[str, frozenset[str]] = {
    "gateway.reach": frozenset({"plan", "loop"}),
    "gateway.routes.shell": frozenset({"feed", "state", "handled"}),
    "gateway": frozenset({"my_day_loop", "work_schedule", "db", "brief", "jobs"})}


def violations(effects: dict[str, tuple[str | None, str | None]]) -> list[str]:
    """The kinds that break the rule of §8 item 3. Pure."""
    out: list[str] = []
    for kind, (effect, policy) in sorted(effects.items()):
        if effect is None:
            out.append(f"{kind}: no effect")
        elif effect not in worker.EFFECTS:
            out.append(f"{kind}: unknown effect {effect!r}")
        elif effect in worker.POLICY_EFFECTS and not policy:
            out.append(f"{kind}: {effect} with no policy")
    return out


def unlabelled_calls(source: str) -> list[int]:
    """The lines of ``register(...)`` calls that state no ``effect=``, or state
    ``effect=None``. It matches the name ``register`` on any owner, so an alias
    (``worker as w``, ``from ... import register``) does not hide a call. A
    call with ``**kw`` counts as unlabelled. Pure."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        fn = node.func
        if (fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")) != "register":
            continue
        eff = [k for k in node.keywords if k.arg == "effect"]
        none = bool(eff) and isinstance(eff[0].value, ast.Constant) and eff[0].value.value is None
        if not eff or none:
            lines.append(node.lineno)
    return lines


def forbidden_imports(source: str, package: str = "gateway.brief") -> list[str]:
    """The imports of *source* that are not on the allowlist. A relative
    import resolves against *package*. Pure."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names
                      if a.name.split(".")[0] == "gateway" and not _ok(a.name, None)]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                parts = package.split(".")[: len(package.split(".")) - node.level + 1]
                module = ".".join(parts + ([module] if module else []))
            if module.split(".")[0] != "gateway":
                continue
            names = [a.name for a in node.names]
            if module in _PACKAGE_NAMES:
                found += [f"{module}.{n}" for n in names if n not in _PACKAGE_NAMES[module]]
            else:
                found += [f"{module}.{n}" for n in names if not _ok(module, n)]
    return found


def _ok(module: str, name: str | None) -> bool:
    if not any(module == a or module.startswith(a + ".") for a in _ALLOWED):
        return False
    allowed = _ALLOWED_NAMES.get(module)
    return allowed is None or name is None or name in allowed


def _registry() -> dict[str, tuple[str | None, str | None]]:
    code = ("import json\n" + "".join(f"import {m}\n" for m in _MODULES)
            + "from gateway.jobs import worker\nprint(json.dumps(worker.effects()))\n")
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                          check=True, cwd=_ROOT, env=None)
    return {k: tuple(v) for k, v in json.loads(done.stdout.splitlines()[-1]).items()}


# ── Fence 1: the registry ───────────────────────────────────────────────────


def test_every_registered_kind_states_its_effect_and_its_policy() -> None:
    effects = _registry()
    assert {"brief.plan", "brief.run", "brief.slo", "jobs.prune"} <= set(effects)
    assert violations(effects) == []


def test_register_refuses_a_send_or_write_with_no_policy() -> None:
    async def h(db, payload) -> None:
        return None

    for effect in ("send", "write"):
        with pytest.raises(ValueError):
            worker.register("fence.bad", h, effect=effect)
    with pytest.raises(ValueError):
        worker.register("fence.bad", h, effect="launch")
    worker.register("fence.ok", h, effect="send", policy="D131 ask each run, own mail")
    try:
        assert worker.effects()["fence.ok"] == ("send", "D131 ask each run, own mail")
    finally:
        worker.unregister("fence.ok")


# ── Fence 2: the source ─────────────────────────────────────────────────────


def test_every_register_call_in_the_gateway_states_an_effect() -> None:
    bad: list[str] = []
    for path in sorted(_GATEWAY.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "register" not in text:
            continue
        for ln in unlabelled_calls(text):
            # The definition's own signature is not a call. Other
            # ``register`` methods (routers, atexit) take no kind.
            if "jobs" in text or path.name == "worker.py":
                bad.append(f"{path.relative_to(_GATEWAY)}:{ln}")
    assert bad == []


def test_the_module_list_covers_every_module_that_registers_a_kind() -> None:
    """A module that calls ``worker.register`` must be in ``_MODULES``, so
    fence 1 sees its kind."""
    missing: list[str] = []
    for path in sorted(_GATEWAY.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "jobs" not in text or "register(" not in text:
            continue
        if unlabelled_calls(text) or "effect=" in text:
            mod = "gateway." + ".".join(path.relative_to(_GATEWAY).with_suffix("").parts)
            if mod not in _MODULES:
                missing.append(mod)
    assert missing == []


# ── Fence 3: the brief does not act ─────────────────────────────────────────


def test_the_brief_package_imports_no_send_seam_and_no_write_route() -> None:
    bad: dict[str, list[str]] = {}
    for path in sorted((_GATEWAY / "brief").glob("*.py")):
        found = forbidden_imports(path.read_text(encoding="utf-8"))
        if found:
            bad[path.name] = found
    assert bad == {}


# ── Fence 4: the planted violations ─────────────────────────────────────────


def test_the_fences_see_a_planted_violation() -> None:
    assert violations({"x.send": ("send", None)}) == ["x.send: send with no policy"]
    assert violations({"x.none": (None, None)}) == ["x.none: no effect"]
    assert violations({"x.ok": ("write", "D131 level 2")}) == []
    assert unlabelled_calls("worker.register('a.b', h, mode='detached')") == [1]
    assert unlabelled_calls("worker.register('a.b', h, effect='read')") == []
    assert forbidden_imports("from gateway.reach import push_channel") == [
        "gateway.reach.push_channel"]
    assert forbidden_imports("from gateway.routes.email.send import go") != []
    assert forbidden_imports("from gateway.reach.loop import send_brief") != []
    assert forbidden_imports("from gateway.reach import push_message") != []
    assert forbidden_imports("from ..reach import push_channel") != []
    assert forbidden_imports("from ..reach.loop import member_pass") != []
    assert forbidden_imports("from gateway.reach import plan") == []
    assert forbidden_imports("from gateway.reach.loop import resolve, snapshot") == []
    assert forbidden_imports("from . import store\nfrom ..jobs import queue") == []
    assert unlabelled_calls("w.register('a.b', h)") == [1]
    assert unlabelled_calls("register('a.b', h, effect=None)") == [1]
    assert unlabelled_calls("register('a.b', h, effect='read')") == []
