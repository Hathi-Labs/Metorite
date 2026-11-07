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
#: ``reach.outbox`` serves the read ``sent_rows`` of the acts (BR-7b-2), and
#: its writers stay out.
_ALLOWED = ("gateway.brief", "gateway.jobs", "gateway.reach.plan", "gateway.reach.loop",
            "gateway.reach.outbox",
            "gateway.my_day_loop", "gateway.routes.shell.feed", "gateway.routes.shell.state",
            "gateway.routes.shell.handled",
            "gateway.work_schedule", "gateway.db")
#: The names that the brief may take from a module of :data:`_ALLOWED` that
#: also holds a sender.
_ALLOWED_NAMES: dict[str, frozenset[str]] = {
    "gateway.reach.loop": frozenset({"resolve", "snapshot", "Snapshot"}),
    "gateway.reach.outbox": frozenset({"sent_rows", "SentRow"})}
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


#: The packages outside ``gateway`` that hold a send seam or a write to a
#: source system. The brief imports none of them (§8 item 1). Fence 3 used
#: to read ``gateway.*`` only, so these were open.
_FOREIGN_DENIED = ("whatsapp_ingestion", "whatsapp_bridge", "email_ingestion", "ingestion",
                   "action_broker", "meeting_bot", "agent_edge", "web_worker", "reconciler",
                   "customer_console")
#: The words that name a send seam in any module path, foreign or not.
_SEND_WORDS = ("whatsapp", "smtp", "sender", "send_")
#: A foreign package of which the brief takes only these modules. ``acb_audit``
#: holds a writer (``record``), ``orchestrator`` runs agents, and
#: ``acb_skills`` holds the tools that act.
_FOREIGN_NARROW: dict[str, tuple[str, ...]] = {
    "acb_audit": ("acb_audit.read",),
    "orchestrator": ("orchestrator.in_app_path",),
    "acb_skills": ("acb_skills.untrusted",),
}


def import_targets(source: str, package: str = "gateway.brief",
                   ) -> list[tuple[str, str | None, str | None]]:
    """Each import of *source* as ``(module, name, alias)``. A plain
    ``import a.b`` gives ``("a.b", None, alias)``. A relative import
    resolves against *package*. Each name of a parenthesised list is its own
    entry. Pure."""
    out: list[tuple[str, str | None, str | None]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out += [(a.name, None, a.asname) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                parts = package.split(".")[: len(package.split(".")) - node.level + 1]
                module = ".".join(parts + ([module] if module else []))
            out += [(module, a.name, a.asname) for a in node.names]
    return out


def _foreign_ok(module: str, name: str | None) -> bool:
    """True when the brief may take *name* from the non-gateway *module*."""
    full = f"{module}.{name}" if name else module
    top = module.split(".")[0]
    if any(w in full.lower() for w in _SEND_WORDS):
        return False
    if top in _FOREIGN_DENIED:
        return False
    narrow = _FOREIGN_NARROW.get(top)
    if narrow is None:
        return True
    return any(full == n or full.startswith(n + ".") or module == n
               or module.startswith(n + ".") for n in narrow)


def forbidden_imports(source: str, package: str = "gateway.brief") -> list[str]:
    """The imports of *source* that are not on the allowlist: a gateway
    module off :data:`_ALLOWED`, a name off :data:`_ALLOWED_NAMES` or an
    alias of one, and a foreign send package (:func:`_foreign_ok`). Pure."""
    found: list[str] = []
    for module, name, alias in import_targets(source, package):
        full = f"{module}.{name}" if name else module
        if module.split(".")[0] != "gateway":
            if not _foreign_ok(module, name):
                found.append(full)
            continue
        if any(w in full.lower() for w in _SEND_WORDS):
            found.append(full)
        elif name is None:
            if not _ok(module, None):
                found.append(full)
        elif module in _PACKAGE_NAMES:
            if name not in _PACKAGE_NAMES[module]:
                found.append(full)
        elif not _ok(module, name) or (alias and module in _ALLOWED_NAMES):
            found.append(full)
    return found


def _ok(module: str, name: str | None) -> bool:
    if not any(module == a or module.startswith(a + ".") for a in _ALLOWED):
        return False
    allowed = _ALLOWED_NAMES.get(module)
    # A module that also holds a sender gives its named reads only: a bare
    # ``import`` of it would reach the senders too (BR-7b-2).
    return allowed is None or (name is not None and name in allowed)


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
    assert forbidden_imports("from gateway.reach.outbox import sent_rows") == []
    assert forbidden_imports("from gateway.reach.outbox import plan") == [
        "gateway.reach.outbox.plan"]
    assert forbidden_imports("from gateway.reach import outbox") == ["gateway.reach.outbox"]
    assert forbidden_imports("import gateway.reach.outbox") == ["gateway.reach.outbox"]
    # BR-7b-2 repair: a foreign send package, an alias, and a parenthesised list.
    assert forbidden_imports("from gateway.reach.outbox import sent_rows as s") == [
        "gateway.reach.outbox.sent_rows"]
    assert forbidden_imports("from gateway.reach.outbox import (sent_rows, plan)") == [
        "gateway.reach.outbox.plan"]
    assert forbidden_imports("from gateway.reach.outbox import (\n    sent_rows,\n    finish,\n)") == [
        "gateway.reach.outbox.finish"]
    assert forbidden_imports("from whatsapp_ingestion.providers.cloud_api import X") == [
        "whatsapp_ingestion.providers.cloud_api.X"]
    assert forbidden_imports("import whatsapp_ingestion") == ["whatsapp_ingestion"]
    assert forbidden_imports("import whatsapp_ingestion.providers as p") == [
        "whatsapp_ingestion.providers"]
    assert forbidden_imports("from whatsapp_bridge import client") == ["whatsapp_bridge.client"]
    assert forbidden_imports("from email_ingestion.providers.gmail import GmailProvider") != []
    assert forbidden_imports("from action_broker.broker import execute") != []
    assert forbidden_imports("from acb_audit import record") == ["acb_audit.record"]
    assert forbidden_imports("from orchestrator.executor import run_agent") != []
    assert forbidden_imports("from acb_skills.integrations import build_integrations") != []
    assert forbidden_imports("from somepkg.whatsapp_send import go") != []
    assert forbidden_imports("from acb_audit import read") == []
    assert forbidden_imports("from acb_audit import read as audit_read") == []
    assert forbidden_imports("from orchestrator.in_app_path import is_in_app_path") == []
    assert forbidden_imports("from acb_skills.untrusted import UntrustedFrame") == []
    assert forbidden_imports("from acb_llm import routed") == []
    assert forbidden_imports("from . import store\nfrom ..jobs import queue") == []
    assert unlabelled_calls("w.register('a.b', h)") == [1]
    assert unlabelled_calls("register('a.b', h, effect=None)") == [1]
    assert unlabelled_calls("register('a.b', h, effect='read')") == []
