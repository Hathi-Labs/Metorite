"""A preview of the action door makes no outbound call (STU-I11-0).

Spec: ``project-docs/specs/ai_studio.md`` §14 rows I11-0, G4 and B1b. Code:
``gateway/routes/studio/invoke.py`` (``refusal_for``, the preview branch of
``invoke_action``) and ``gateway/outbound_guard.py``.

Today no code can call a provider from a preview: the ``uses`` grammar has no
``mcp:`` source, the gateway has no connection client, and the door has no
executor for a connection source. B1b opens the first such source. This file
fences the fail-closed behaviour BEFORE that, so B1b cannot open a source into
a preview by accident (R7).

**The tripwire.** The fixture ``no_outbound`` makes each public outbound entry
point of ``outbound_guard`` raise, and every ``httpx`` client send, and every
non-local socket connect. A call that reaches one fails with the text in
``TRIPWIRE``. ``test_the_tripwire_fires`` proves it is not vacuous.

**The mutations (R7).** Each was run on 2026-11-14, then restored with git.

* M1. ``invoke_action``: drop ``and not preview`` from the arm that refuses
  an action of another source. A preview then answers ``source_not_open`` for a
  class B or C action. 3 tests go red, the three cases of
  ``test_a_class_b_or_c_of_another_source_is_simulated``.
* M2. ``refusal_for``: pass a class A action in a preview, whatever its source.
  8 tests go red: the four class A cases of
  ``test_refusal_for_in_a_preview_per_source_and_class``,
  ``test_the_real_catalog_rows_each_get_a_preview_decision``, and the
  ``pages``, ``pull`` and ``flow_a`` cases of
  ``test_each_other_answer_is_a_refusal_and_sends_nothing``.
"""

from __future__ import annotations

import socket
import uuid
from typing import Any, get_args, get_type_hints

import httpx
import pytest
from acb_common.studio_manifest import _USES_RE, UsesRef, parse_uses
from gateway import outbound_guard
from gateway.routes.studio import actions, invoke

from tests.unit.test_studio_action_door import (  # noqa: F401
    CATALOG,
    MANIFEST,
    WORKFLOW,
    _client,
    _code,
    _package,
    _setup,
    dw,
    world,
)

TRIPWIRE = "outbound call in a preview"

#: Each public function of ``outbound_guard`` that opens or resolves a network
#: connection. A new one must be added here with a preview decision.
OUTBOUND_ENTRY_POINTS = frozenset(
    {"check_url", "check_mcp_url", "send", "request", "request_https", "mcp_client"})

WF_A = str(uuid.uuid4())


@pytest.fixture
def no_outbound(monkeypatch):
    """Each outbound seam raises ``AssertionError(TRIPWIRE)``, and records."""
    hits: list[str] = []

    def _boom(name: str):
        async def _raise(*_a: Any, **_k: Any) -> Any:
            hits.append(name)
            raise AssertionError(TRIPWIRE)
        return _raise

    for name in OUTBOUND_ENTRY_POINTS:
        monkeypatch.setattr(outbound_guard, name, _boom(f"outbound_guard.{name}"))

    async def _async_send(self: Any, *_a: Any, **_k: Any) -> Any:
        hits.append("httpx.AsyncClient")
        raise AssertionError(TRIPWIRE)

    monkeypatch.setattr(httpx.AsyncClient, "send", _async_send)
    monkeypatch.setattr(httpx.AsyncClient, "request", _async_send)

    real_send = httpx.Client.send

    def _sync_send(self: Any, request: httpx.Request, *a: Any, **k: Any) -> Any:
        # The TestClient of the suite is an httpx.Client on host "testserver".
        if request.url.host != "testserver":
            hits.append("httpx.Client")
            raise AssertionError(TRIPWIRE)
        return real_send(self, request, *a, **k)

    monkeypatch.setattr(httpx.Client, "send", _sync_send)

    real_connect = socket.socket.connect

    def _connect(self: socket.socket, address: Any) -> Any:
        if self.family != getattr(socket, "AF_UNIX", None):
            hits.append("socket.connect")
            raise AssertionError(TRIPWIRE)
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", _connect)
    return hits


# ── The tripwire itself ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_tripwire_fires(no_outbound):
    """Non-vacuity: each seam raises when called directly."""
    for name in sorted(OUTBOUND_ENTRY_POINTS):
        with pytest.raises(AssertionError, match=TRIPWIRE):
            await getattr(outbound_guard, name)("GET" if name in ("send", "request") else "x")
    with pytest.raises(AssertionError, match=TRIPWIRE):
        async with httpx.AsyncClient() as c:
            await c.get("http://example.test/")
    with pytest.raises(AssertionError, match=TRIPWIRE):
        httpx.Client().get("http://example.test/")
    s = socket.socket()
    try:
        with pytest.raises(AssertionError, match=TRIPWIRE):
            s.connect(("127.0.0.1", 9))
    finally:
        s.close()
    assert len(no_outbound) == len(OUTBOUND_ENTRY_POINTS) + 3


def test_every_public_outbound_function_is_wrapped():
    """A new public async function of ``outbound_guard`` fails here by name.
    Add it to ``OUTBOUND_ENTRY_POINTS`` if it reaches the network, or give the
    preview decision in the PR that adds it."""
    import inspect

    found = {n for n, f in vars(outbound_guard).items()
             if not n.startswith("_") and inspect.iscoroutinefunction(f)
             and getattr(f, "__module__", "") == outbound_guard.__name__}
    assert found == OUTBOUND_ENTRY_POINTS


# ── The grammar and the sources ────────────────────────────────────────────

#: One sample of each alternative of the ``uses`` grammar.
SAMPLES = {
    "object": "object:visit.create",
    "integration": "integration:zoho.push",
    "workflow": f"workflow:{WORKFLOW}",
    "manifest": "knowledge.list_pages",
    "logic": "logic:totals",
}

SOURCES = ("object", "manifest", "integration", "workflow", "logic")
CLASSES = ("A", "B", "C", "X", None)


def test_a_new_source_needs_a_preview_decision():
    """A source added to ``_USES_RE`` or to ``UsesRef`` fails here until this
    file covers it. B1b adds ``mcp``: it must add its row to ``SAMPLES``."""
    assert set(get_args(get_type_hints(UsesRef)["source"])) == set(SOURCES)
    assert set(SAMPLES) == set(SOURCES)
    assert set(_USES_RE.groupindex) == {"obj", "op", "integ", "wf", "app", "appop", "logic"}
    for source, uses in SAMPLES.items():
        ref = parse_uses(uses)
        assert ref is not None and ref.source == source


#: What the door answers in a preview, per source and class, today. ``None``
#: means that an executor or the simulated answer runs (no refusal).
EXPECTED = {
    ("manifest", "A"): "source_not_open", ("manifest", "B"): None,
    ("manifest", "C"): None, ("manifest", "X"): "class_x",
    ("integration", "A"): "source_not_open", ("integration", "B"): None,
    ("integration", "C"): None, ("integration", "X"): "class_x",
    ("workflow", "A"): "source_not_open", ("workflow", "B"): None,
    ("workflow", "C"): None, ("workflow", "X"): "class_x",
    ("object", "X"): "class_x",
    # B4a: a logic module is a first-party module with no session and no
    # network. The class is B. With the flag off (the default here) the door
    # answers source_not_open, and class X and an id outside the catalog stay refused.
    ("logic", "A"): "source_not_open", ("logic", "B"): "source_not_open",
    ("logic", "C"): "source_not_open", ("logic", "X"): "class_x",
}


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("cls", CLASSES)
def test_refusal_for_in_a_preview_per_source_and_class(no_outbound, source, cls):
    ref = parse_uses(SAMPLES[source])
    got = invoke.refusal_for(ref, cls, preview=True)
    if cls is None:
        assert got == "not_in_catalog"
    elif (source, cls) in EXPECTED:
        assert got == EXPECTED[(source, cls)]
    elif source == "object" and cls == "A":
        # object:visit.create is a write, so a class A object row is not a
        # shape the catalog gives. The class A object ops are get and list.
        assert got == "source_not_open"
    else:
        assert source == "object" and cls in ("B", "C")
        assert got is None  # the object write runs inside the rollback SAVEPOINT
    assert no_outbound == []


def test_the_real_catalog_rows_each_get_a_preview_decision(no_outbound):
    """Each real row of the door ops and the integration tools, and one
    workflow row, gets a decision that opens no source other than the object
    and door-op reads."""
    rows = [*actions._manifest_rows(), *actions._integration_rows(),
            {"id": f"workflow:{WF_A}", "class": "B", "source": "workflow"}]
    assert rows
    for row in rows:
        ref = parse_uses(row["id"])
        assert ref is not None, row["id"]
        got = invoke.refusal_for(ref, row["class"], preview=True)
        if row["class"] == "A":
            assert got == "source_not_open", row["id"]  # door_ops off by default
        elif row["class"] in ("B", "C"):
            assert got is None, row["id"]
        else:
            assert got == "class_x", row["id"]
    assert no_outbound == []


def test_the_integration_source_of_the_real_catalog_is_empty():
    """B1b makes the first row of a connection source. It must then extend
    ``EXTRA`` below with that row, so this file decides its preview answer."""
    assert actions._integration_rows() == []


# ── The door, in a preview ─────────────────────────────────────────────────

EXTRA_ACTIONS = [
    {"id": "pull", "uses": "integration:zoho.pull"},
    {"id": "flow_a", "uses": f"workflow:{WF_A}"},
    {"id": "noclass", "uses": "integration:zoho.noclass"},
    {"id": "ghost", "uses": "integration:zoho.ghost"},
]
EXTRA_CATALOG = [
    {"id": "integration:zoho.pull", "class": "A", "source": "integration"},
    {"id": f"workflow:{WF_A}", "class": "A", "source": "workflow"},
    {"id": "integration:zoho.noclass", "source": "integration"},
]


@pytest.fixture
def pw(dw, no_outbound):  # noqa: F811
    dw.catalog.extend(EXTRA_CATALOG)
    dw.hits = no_outbound
    return dw


def _post(c: Any, pid: str, action: str, body: Any) -> Any:
    return c.post(f"/studio/apps/{pid}/actions/{action}", json=body, params={"preview": "1"})


def _pid(pw: Any) -> tuple[str, str]:
    manifest = {**MANIFEST, "actions": [*MANIFEST["actions"], *EXTRA_ACTIONS]}
    pid = _package(pw, manifest=manifest)
    _, _, rid = _setup_in(pw, pid)
    return pid, rid


def _setup_in(pw: Any, pid: str) -> tuple[str, str, str]:
    from tests.unit.test_studio_action_door import _object, _record
    oid = _object(pw, pid)
    return pid, oid, _record(pw, oid)


class TestThePreviewDoorSendsNothing:
    def test_an_object_read_is_real_and_sends_nothing(self, pw):
        pid, rid = _pid(pw)
        r = _post(_client(pw), pid, "peek", {"id": rid})
        assert r.status_code == 200 and r.json()["record"]["id"] == rid
        assert "preview" not in r.json()
        r = _post(_client(pw), pid, "find", {})
        assert r.status_code == 200, r.text
        assert pw.hits == []

    @pytest.mark.parametrize("action,body", [
        ("add", {"data": {"site": "Plant 9"}}),
        ("change", {"id": str(uuid.uuid4()), "data": {"notes": "Moved"}}),
        ("remove", {"id": str(uuid.uuid4())})])
    def test_an_object_write_rolls_back_and_sends_nothing(self, pw, action, body):
        pid, _ = _pid(pw)
        r = _post(_client(pw), pid, action, body)
        assert r.status_code == 200 and r.json()["preview"] is True
        assert "rollback_savepoint" in pw.log
        assert pw.hits == []

    @pytest.mark.parametrize("action,cls", [
        ("flow", "B"), ("propose", "B"), ("sync", "C")])
    def test_a_class_b_or_c_of_another_source_is_simulated(self, pw, action, cls):
        pid, _ = _pid(pw)
        r = _post(_client(pw), pid, action, {"to": "someone@example.test"})
        got = r.json()
        assert r.status_code == 200 and got["preview"] is True
        assert got["simulated"]["class"] == cls
        assert pw.writes == [] and pw.hits == []

    @pytest.mark.parametrize("action,code", [
        ("pages", "source_not_open"),     # class A door op, switch off
        ("pull", "source_not_open"),      # class A integration
        ("flow_a", "source_not_open"),    # class A workflow
        ("noclass", "source_not_open"),   # a row with no class
        ("forbidden", "class_x"),
        ("gone_flow", "not_in_catalog"),  # an id outside the catalog
        ("ghost", "not_in_catalog")])
    def test_each_other_answer_is_a_refusal_and_sends_nothing(self, pw, action, code):
        pid, _ = _pid(pw)
        r = _post(_client(pw), pid, action, {})
        assert (r.status_code, _code(r)) == (403, code)
        assert pw.writes == [] and pw.hits == []

    def test_a_preview_does_not_catch_a_hit_it_never_made(self, pw):
        """Control: the fake client sees nothing, so ``hits`` stays empty."""
        pid, _ = _pid(pw)
        _post(_client(pw), pid, "sync", {})
        assert pw.hits == []


def test_a_logic_module_runs_in_a_preview_and_sends_nothing(no_outbound):
    """B4a. With ``STUDIO_LOGIC_ORGS`` open, a preview runs a logic module: its
    writes roll back with the batch and no record event fires. The module has
    no session and no network, so a preview needs no store of its own. The
    ``refusal_for`` answer is None for a direct call and for a preview, and a
    delegated call needs approval. A viewer stays read-only."""
    ref = parse_uses(SAMPLES["logic"])
    assert invoke.refusal_for(ref, "B", preview=True, logic=True) is None
    assert invoke.refusal_for(ref, "B", direct=True, logic=True) is None
    assert invoke.refusal_for(ref, "B", delegated=True, logic=True) == "approval_required"
    assert invoke.refusal_for(ref, "B", preview=True, logic=True, viewer=True) == "read_only"
    assert invoke.refusal_for(ref, "B", preview=True, logic=False) == "source_not_open"
    assert no_outbound == []


def test_the_logic_package_reaches_no_network():
    """The reason a logic preview needs no store: ``gateway/studio/logic`` imports
    no HTTP client, no socket and no outbound seam. A new import of one fails
    here, and the PR that adds it gives the preview decision."""
    import re
    from pathlib import Path

    root = Path(invoke.__file__).resolve().parents[2] / "studio" / "logic"
    banned = re.compile(r"^\s*(?:import|from)\s+(?:httpx|socket|requests|aiohttp|urllib|"
                        r"gateway\.outbound_guard|acb_common\.outbound_guard)"
                        r"|^\s*from\s+gateway\s+import\s+.*outbound_guard", re.M)
    for path in sorted(root.glob("*.py")):
        assert not banned.search(path.read_text(encoding="utf-8")), path.name
