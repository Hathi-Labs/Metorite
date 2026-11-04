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
* M2. Add ``from gateway.reach import push_channel`` to ``brief/run.py``.
  Fence 3 goes red.
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

#: The imports that the brief must never make: a send seam, or a route of
#: another app. ``gateway.routes.shell.feed`` and ``.state`` are reads.
_FORBIDDEN = ("gateway.reach.push_channel", "gateway.reach.outbox", "gateway.reach.whatsapp",
              "gateway.reach.due_push", "gateway.push", "gateway.email",
              "gateway.routes.email", "gateway.routes.projects", "gateway.routes.tasks",
              "gateway.routes.crm", "gateway.routes.calendar")


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
    """The lines of ``register(...)`` calls that state no ``effect=``. Pure."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        owner = fn.value.id if isinstance(fn, ast.Attribute) and isinstance(
            fn.value, ast.Name) else ""
        if name == "register" and owner == "worker" and node.args and not any(
                k.arg == "effect" for k in node.keywords):
            lines.append(node.lineno)
    return lines


def forbidden_imports(source: str) -> list[str]:
    """The forbidden modules that *source* imports. Pure."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        for name in names:
            if any(name == f or name.startswith(f + ".") for f in _FORBIDDEN):
                found.append(name)
    return found


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
        if path.name == "worker.py" and path.parent.name == "jobs":
            tree = ast.parse(path.read_text(encoding="utf-8"))
            # Skip the definition itself; the bare call at the bottom is checked.
            src_calls = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Name) and n.func.id == "register"
                         and not any(k.arg == "effect" for k in n.keywords)]
            bad += [f"{path.name}:{ln}" for ln in src_calls]
            continue
        text = path.read_text(encoding="utf-8")
        if "worker.register(" not in text:
            continue
        bad += [f"{path.relative_to(_GATEWAY)}:{ln}" for ln in unlabelled_calls(text)]
    assert bad == []


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
    assert forbidden_imports("from gateway.reach import plan") == []
