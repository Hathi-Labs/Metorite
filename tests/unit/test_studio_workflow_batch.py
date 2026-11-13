"""STU-E8b: the ``studio_batch`` node and its run, the hermetic half (task 3096).

Spec: ``project-docs/specs/ai_studio.md`` §14 E8b. Code:
``routes/workflows/engine/graph.py`` (the node and its graph checks),
``engine/handlers.py`` (the seam), ``routes/workflows/batch.py`` (the flag, the
park, the bound decision, the scan SQL), ``service._studio_batcher`` and
``service.start_batch_run``, ``scheduler.scan_open_batches``,
``routes/studio/invoke.run_workflow_batch`` and the E8a store calls of E8b.
``test_studio_workflow_batch_r8.py`` holds the real-database half.

No Postgres: the graph rules, the flag reader, the refusals of the seam and
the source fences.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import itertools
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_common import env_guard, get_settings
from acb_common.no_person import no_person_scope
from acb_common.settings import Settings
from gateway.routes.studio import invoke
from gateway.routes.workflows import batch, service, unattended
from gateway.routes.workflows.engine import graph
from gateway.routes.workflows.engine.graph import compile_graph, runtime_gate_of, validate_graph
from gateway.routes.workflows.engine.handlers import NODE_TIMEOUTS, NodeExecutionError, NodeServices
from gateway.routes.workflows.engine.runner import RUN_TIMEOUT_SECS
from gateway.studio import batch_store

_ROOT = Path(__file__).resolve().parents[2]
_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway"
_WORKFLOWS = _GATEWAY / "routes" / "workflows"

ORG = "0d4b8f5e-0000-4000-8000-0000000000e8"
PKG = "7a1c0e55-0000-4000-8000-0000000000e8"


def _node(nid: str, ntype: str, config: dict | None = None) -> dict:
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": {"label": nid, "config": config or {}}}


def _edges(*pairs: tuple[str, str]) -> list[dict]:
    return [{"id": f"{a}->{b}", "source": a, "target": b} for a, b in pairs]


def _items(n: int = 3) -> list[dict]:
    return [{"key": f"e{i:04d}", "data": {"emp": f"e{i:04d}", "amount": 100 + i}}
            for i in range(n)]


def _batch_config(**over: Any) -> dict:
    return {"package_id": PKG, "action": "pay", "items": _items(), **over}


def _graph(cfg: dict | None = None) -> dict:
    """trigger -> gate -> batch -> done."""
    nodes = [_node("start", "trigger", {"kind": "schedule"}), _node("gate", "approval"),
             _node("batch", "studio_batch", cfg or _batch_config()),
             _node("done", "output", {"value": "end"})]
    chain = ["start", "gate", "batch", "done"]
    return {"nodes": nodes, "edges": _edges(*itertools.pairwise(chain))}


def _codes(g: dict) -> list[str]:
    return [i.code for i in validate_graph(g)]


# ── The node and its graph rules ─────────────────────────────────────────────


def test_the_node_type_exists_and_a_valid_graph_compiles() -> None:
    assert "studio_batch" in graph.NODE_TYPES
    compiled = compile_graph(_graph())
    assert {b["id"]: b["type"] for b in compiled["blocks"]}["batch"] == "studio_batch"
    assert runtime_gate_of(compiled, "batch") == "gate"


def test_a_batch_must_follow_an_approval_directly() -> None:
    nodes = [_node("start", "trigger"), _node("gate", "approval"),
             _node("mid", "set", {"assignments": {}}),
             _node("batch", "studio_batch", _batch_config())]
    two_down = {"nodes": nodes, "edges": _edges(("start", "gate"), ("gate", "mid"),
                                                ("mid", "batch"))}
    assert "studio_write_without_approval" in _codes(two_down)
    no_gate = {"nodes": [nodes[0], nodes[3]], "edges": _edges(("start", "batch"))}
    assert "studio_write_without_approval" in _codes(no_gate)


def test_the_gate_of_a_batch_leads_to_the_batch_only() -> None:
    """A new run replays each node before the batch. A second step under the
    gate would run again after a stop, so publish refuses it, and so does the
    run-time twin for a draft."""
    nodes = [_node("start", "trigger"), _node("gate", "approval"),
             _node("batch", "studio_batch", _batch_config()),
             _node("note", "set", {"assignments": {"x": 1}})]
    edges = _edges(("start", "gate"), ("gate", "batch"), ("gate", "note"))
    assert _codes({"nodes": nodes, "edges": edges}) == ["approval_leads_to_the_batch_only"]
    run_model = {"blocks": [{"id": n["id"], "type": n["type"]} for n in nodes],
                 "connections": [{"source": e["source"], "target": e["target"]} for e in edges]}
    assert runtime_gate_of(run_model, "batch") is None


def test_one_approval_clears_one_write_node_of_either_type() -> None:
    nodes = [_node("start", "trigger"), _node("gate", "approval"),
             _node("batch", "studio_batch", _batch_config()),
             _node("write", "studio_write", {"package_id": PKG, "action": "pay", "data": {}})]
    edges = _edges(("start", "gate"), ("gate", "batch"), ("gate", "write"))
    codes = _codes({"nodes": nodes, "edges": edges})
    assert "approval_clears_one_write" in codes
    run_model = {"blocks": [{"id": n["id"], "type": n["type"]} for n in nodes],
                 "connections": [{"source": e["source"], "target": e["target"]} for e in edges]}
    assert runtime_gate_of(run_model, "write") is None
    assert runtime_gate_of(run_model, "batch") is None


def _bad_item(**over: Any) -> list[dict]:
    return [*_items(2), {"key": "e9999", "data": {"emp": "x"}, **over}]


@pytest.mark.parametrize(("cfg", "code"), [
    (_batch_config(items=[{"key": "e1", "data": {"emp": "{{trigger.who}}"}}]),
     "templated_config"),
    (_batch_config(organization_id=ORG), "unknown_field"),
    (_batch_config(member="x@y.test"), "unknown_field"),
    (_batch_config(package_id="not-a-uuid"), "missing_config"),
    (_batch_config(action="Bad Action"), "missing_config"),
    (_batch_config(items={"e1": {}}), "missing_config"),
    (_batch_config(items=[]), "missing_config"),
    (_batch_config(items=[{"key": f"k{i}", "data": {}} for i in range(1001)]), "missing_config"),
    (_batch_config(items=_bad_item(extra=1)), "missing_config"),
    (_batch_config(items=_bad_item(data=["x"])), "missing_config"),
    (_batch_config(items=_bad_item(record_id="nope")), "missing_config"),
    (_batch_config(items=[*_items(2), {"key": "e0001", "data": {}}]), "missing_config"),
    (_batch_config(items=[{"key": " e1", "data": {}}]), "missing_config"),
    (_batch_config(items=[{"key": "k" * 121, "data": {}}]), "missing_config"),
    (_batch_config(items=[{"data": {}}]), "missing_config"),
])
def test_the_config_is_literal_and_each_item_has_the_shape(cfg: dict, code: str) -> None:
    assert code in _codes(_graph(cfg))


def test_a_list_of_1000_items_publishes() -> None:
    items = [{"key": f"k{i:04d}", "data": {"n": i}} for i in range(1000)]
    assert _codes(_graph(_batch_config(items=items))) == []


def test_the_engine_copies_of_the_store_caps_agree() -> None:
    """The engine takes no import edge into the store, so it copies two caps.
    This fence fails when either side moves."""
    assert graph.MAX_BATCH_ITEMS == batch_store.MAX_BATCH_ITEMS == 1000
    assert graph.MAX_BATCH_KEY_CHARS == batch_store.MAX_KEY_CHARS


def test_the_node_time_fits_in_the_run_time() -> None:
    assert NODE_TIMEOUTS["studio_batch"] < RUN_TIMEOUT_SECS


def test_the_node_without_a_seam_fails() -> None:
    from gateway.routes.workflows.engine.handlers import execute_node

    async def _none(*_a: Any) -> Any:
        return None

    services = NodeServices(run_agent=_none, run_tool=_none, get_module_code=_none)
    with pytest.raises(NodeExecutionError):
        asyncio.run(execute_node({"id": "batch", "type": "studio_batch",
                                  "config": _batch_config()}, {}, services))


# ── The flag ────────────────────────────────────────────────────────────────


def _listed(monkeypatch, raw: str) -> None:
    monkeypatch.setattr(get_settings(), "studio_batch_orgs", raw)


def test_the_setting_ships_empty() -> None:
    assert Settings.model_fields["studio_batch_orgs"].default == ""


def test_an_empty_list_allows_no_org(monkeypatch) -> None:
    _listed(monkeypatch, "")
    assert batch.batch_allowed(ORG) is False


def test_no_org_is_never_allowed(monkeypatch) -> None:
    _listed(monkeypatch, ORG)
    assert batch.batch_allowed(None) is False and batch.batch_allowed("") is False


def test_a_comma_list_is_trimmed_and_case_blind(monkeypatch) -> None:
    _listed(monkeypatch, f" Org-A , {ORG.upper()},, ")
    assert batch.batch_allowed("org-a") is True
    assert batch.batch_allowed(ORG) is True
    assert batch.batch_allowed("org-c") is False


def test_a_star_is_not_a_wildcard(monkeypatch) -> None:
    _listed(monkeypatch, "*")
    assert batch.batch_allowed(ORG) is False


def test_only_the_owner_writes_the_flag() -> None:
    assert env_guard.is_platform_env("STUDIO_BATCH_ORGS") is True


def test_the_example_file_ships_the_flag_empty() -> None:
    body = (_ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^STUDIO_BATCH_ORGS=\s*$", body, re.M)


def test_the_reader_module_is_the_only_reader() -> None:
    """ONE reader (R7): no other module of the apps or the packages reads the
    setting by attribute, by ``getattr`` or by its name. ``settings.py`` only
    defines it. A comment or a docstring may name it."""
    names = {"studio_batch_orgs"}
    found: set[str] = set()
    for base in ("apps", "packages"):
        for path in (_ROOT / base).rglob("*.py"):
            if "node_modules" in path.parts or ".venv" in path.parts:
                continue
            body = path.read_text(encoding="utf-8", errors="replace")
            if "studio_batch_orgs" not in body.lower():
                continue
            for node in ast.walk(ast.parse(body)):
                hit = (isinstance(node, ast.Attribute) and node.attr in names) or (
                    isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value.strip().lower() in names)
                if hit:
                    found.add(path.relative_to(_ROOT).as_posix())
    assert found == {"apps/services/gateway/gateway/routes/workflows/batch.py"}, found


def _all_flags(monkeypatch, orgs: str = ORG) -> None:
    settings = get_settings()
    for key in ("auto_park_orgs", "decisions_orgs", "studio_orgs", "studio_batch_orgs"):
        monkeypatch.setattr(settings, key, orgs)
    monkeypatch.setattr(unattended, "get_settings",
                        lambda: SimpleNamespace(workflow_unattended_orgs=orgs))


@pytest.mark.parametrize("off", ["auto_park_orgs", "decisions_orgs", "studio_orgs",
                                 "studio_batch_orgs", "workflow_unattended_orgs"])
def test_an_org_is_ready_only_with_all_five_flags(monkeypatch, off: str) -> None:
    _all_flags(monkeypatch)
    assert batch.org_ready(ORG) is True
    if off == "workflow_unattended_orgs":
        monkeypatch.setattr(unattended, "get_settings",
                            lambda: SimpleNamespace(workflow_unattended_orgs=""))
    else:
        monkeypatch.setattr(get_settings(), off, "")
    assert batch.org_ready(ORG) is False


# ── The refusals of the seam ────────────────────────────────────────────────

_PARKED = service.ParkedApproval(decision_id=str(uuid.uuid4()), pause_id=str(uuid.uuid4()),
                                 gate="gate")


def _batcher(**over: Any) -> Any:
    kw: dict[str, Any] = {"run_id": str(uuid.uuid4()), "workflow_id": str(uuid.uuid4()),
                          "organization_id": ORG, "trigger_kind": "schedule",
                          "serialized": compile_graph(_graph()), "parked": _PARKED}
    kw.update(over)
    return service._studio_batcher(**kw)


def _spy(monkeypatch, *, ok: bool = True) -> list[str]:
    """Fakes of each read past the refusals: the owner, the session, the batch,
    the decision and the door. Each call is recorded."""
    seen: list[str] = []

    async def _owner(*_a: Any) -> tuple[str, str]:
        seen.append("owner")
        return ORG, "owner@a.test"

    @contextlib.asynccontextmanager
    async def _session(_org: str):
        seen.append("session")
        yield SimpleNamespace()

    async def _batch_of_run(_db: Any, _run: str, _node: str) -> Any:
        seen.append("batch")
        return SimpleNamespace(id=str(uuid.uuid4()), list_digest="d" * 64)

    async def _approved(_db: Any, **_k: Any) -> Any:
        seen.append("decision")
        return batch.BoundDecision(decision_id=_PARKED.decision_id, gate="gate",
                                   origin_run_id="r", pause_id=_PARKED.pause_id,
                                   list_digest="d" * 64)

    async def _door(**_k: Any) -> dict:
        seen.append("door")
        return {"ok": ok, "code": None if ok else "items_failed", "written": 3, "failed": 0}

    monkeypatch.setattr(service, "_workflow_owner", _owner)
    monkeypatch.setattr(service, "_tenant_session", _session)
    monkeypatch.setattr(batch_store, "batch_of_run", _batch_of_run)
    monkeypatch.setattr(batch, "approved_batch", _approved)
    monkeypatch.setattr(invoke, "run_workflow_batch", _door)
    return seen


async def _call(writer: Any, *, marked: bool = True) -> Any:
    if not marked:
        return await writer("batch", _batch_config())
    with no_person_scope():
        return await writer("batch", _batch_config())


@pytest.mark.parametrize(("over", "marked", "code"), [
    ({"organization_id": None}, True, service.STUDIO_NO_ORG),
    ({"trigger_kind": "manual"}, True, service.STUDIO_NOT_SCHEDULED),
    ({"trigger_kind": "api"}, True, service.STUDIO_NOT_SCHEDULED),
    ({"trigger_kind": "event"}, True, service.STUDIO_NOT_SCHEDULED),
    ({"trigger_kind": "webhook"}, True, service.STUDIO_NOT_SCHEDULED),
    ({}, False, service.STUDIO_UNMARKED),
    ({"parked": None}, True, service.STUDIO_NO_DECISION),
    ({"parked": service.ParkedApproval(decision_id=str(uuid.uuid4()),
                                       pause_id=str(uuid.uuid4()), gate="done")},
     True, service.STUDIO_BAD_GATE),
])
def test_each_run_that_is_not_the_parked_path_refuses_before_any_read(
    monkeypatch, over: dict, marked: bool, code: str,
) -> None:
    _all_flags(monkeypatch)
    seen = _spy(monkeypatch)
    with pytest.raises(NodeExecutionError) as refused:
        asyncio.run(_call(_batcher(**over), marked=marked))
    assert f"({code})" in str(refused.value)
    assert seen == []


@pytest.mark.parametrize("off", ["auto_park_orgs", "decisions_orgs", "studio_orgs",
                                 "studio_batch_orgs", "workflow_unattended_orgs"])
def test_each_of_the_five_flags_off_refuses_before_any_read(monkeypatch, off: str) -> None:
    _all_flags(monkeypatch)
    if off == "workflow_unattended_orgs":
        monkeypatch.setattr(unattended, "get_settings",
                            lambda: SimpleNamespace(workflow_unattended_orgs=""))
    else:
        monkeypatch.setattr(get_settings(), off, "")
    seen = _spy(monkeypatch)
    with pytest.raises(NodeExecutionError) as refused:
        asyncio.run(_call(_batcher()))
    assert f"({service.STUDIO_FLAG_OFF})" in str(refused.value)
    assert seen == []


def test_one_decision_clears_one_batch_node_and_a_spent_gate_refuses(monkeypatch) -> None:
    _all_flags(monkeypatch)
    seen = _spy(monkeypatch)
    writer = _batcher()

    async def twice() -> tuple[Any, BaseException | None]:
        first = await _call(writer)
        try:
            await _call(writer)
        except NodeExecutionError as exc:
            return first, exc
        return first, None

    first, second = asyncio.run(twice())
    assert first["ok"] is True
    assert second is not None and f"({service.STUDIO_SPENT})" in str(second)
    # The second call read nothing: the spend comes before the owner read.
    assert seen == ["owner", "session", "batch", "decision", "door"]


def test_a_decision_that_is_not_the_parked_one_refuses(monkeypatch) -> None:
    _all_flags(monkeypatch)
    seen = _spy(monkeypatch)

    async def _other(_db: Any, **_k: Any) -> Any:
        return batch.BoundDecision(decision_id=str(uuid.uuid4()), gate="gate", origin_run_id="r",
                                   pause_id="p", list_digest="d" * 64)

    monkeypatch.setattr(batch, "approved_batch", _other)
    with pytest.raises(NodeExecutionError) as refused:
        asyncio.run(_call(_batcher()))
    assert f"({service.STUDIO_NOT_APPROVED})" in str(refused.value)
    assert "door" not in seen


def test_a_batch_that_ends_with_a_failed_item_fails_the_node(monkeypatch) -> None:
    _all_flags(monkeypatch)
    _spy(monkeypatch, ok=False)
    with pytest.raises(NodeExecutionError) as refused:
        asyncio.run(_call(_batcher()))
    assert "(items_failed)" in str(refused.value)


def test_the_internal_door_refuses_with_no_org_or_the_switch_off(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", "")

    async def go(org: str) -> dict:
        return await invoke.run_workflow_batch(
            organization_id=org, member="o@a.test", package_id=PKG, action="pay",
            items=_items(), run_id="r", node_id="batch", batch_id=str(uuid.uuid4()),
            expected_digest="d", decision_id="x")

    assert asyncio.run(go(""))["code"] == invoke.WORKFLOW_NO_ORG
    assert asyncio.run(go(ORG))["code"] == invoke.WORKFLOW_STUDIO_OFF


# ── The question ────────────────────────────────────────────────────────────


def test_the_question_names_the_verb_the_count_the_object_and_the_package() -> None:
    plan = SimpleNamespace(package_name="Payroll", object="pay_line", op="create",
                           items=tuple(range(300)), uses="object:pay_line.create",
                           list_digest="a" * 64)
    assert batch.batch_question("Pay run", plan) == (
        'Let "Pay run" add 300 records of pay_line in "Payroll"?')
    long = SimpleNamespace(package_name="P" * 200, object="o" * 200, op="update",
                           items=tuple(range(1000)), uses="object:o.update", list_digest="a")
    assert len(batch.batch_question("W" * 200, long)) <= 140
    detail = batch.batch_detail(plan, "b1")
    assert "300 items" in detail and "a" * 64 in detail and "object:pay_line.create" in detail


# ── The store calls of E8b ──────────────────────────────────────────────────


_STORE = (_GATEWAY / "studio" / "batch_store.py").read_text(encoding="utf-8")


def test_the_end_codes_are_a_closed_set() -> None:
    assert batch_store.END_CODES == ("query_timeout", "write_mismatch")
    with pytest.raises(batch_store.BatchStoreError):
        asyncio.run(batch_store.end_stuck_item(object(), str(uuid.uuid4()), code="some text"))


def test_the_rebind_is_a_compare_and_set_under_the_bound() -> None:
    sql = re.search(r"_REBIND_SQL = \(\n(.*?)\n\)", _STORE, re.S).group(1)
    for part in ("run_id = CAST(:old AS uuid)", "status = 'open'", "resumes < :cap",
                 "resumes = resumes + 1", "RETURNING resumes", "_BOUND_ORG"):
        assert part in sql, part


def test_run_chunk_requires_the_viewer_and_passes_it_on() -> None:
    import inspect

    params = inspect.signature(batch_store.run_chunk).parameters
    assert params["viewer"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["viewer"].default is inspect.Parameter.empty
    assert "viewer=call.viewer" in _STORE


def test_a_viewer_that_is_not_the_actor_is_refused_before_any_statement() -> None:
    """The store decides "owner" from the viewer, and the actor only names
    ``created_by``. An owner viewer under a member actor fails closed."""
    from acb_common.studio_access import Viewer

    seen: list[str] = []

    class _Db:
        async def execute(self, *_a: Any, **_k: Any) -> Any:
            seen.append("statement")
            raise AssertionError("a statement ran")

    with pytest.raises(batch_store.BatchStoreError, match="viewer"):
        asyncio.run(batch_store.run_chunk(_Db(), "member@a.test", str(uuid.uuid4()), {},
                                          viewer=Viewer("owner@a.test")))
    with pytest.raises(batch_store.BatchStoreError, match="viewer"):
        asyncio.run(batch_store.run_chunk(_Db(), "member@a.test", str(uuid.uuid4()), {},
                                          viewer="member@a.test"))  # type: ignore[arg-type]
    assert seen == []


def test_the_door_builds_the_viewer_from_the_member_it_acts_as() -> None:
    """One call of the door passes the actor and the viewer, both from
    ``run.member``, which the seam binds to the workflow owner."""
    src = (_GATEWAY / "routes" / "studio" / "invoke.py").read_text(encoding="utf-8")
    fn = src[src.index("async def _batch_drive"):src.index("def _batch_audit")]
    call = fn[fn.index("batch_store.run_chunk("):fn.index("except batch_store.DigestMismatch")]
    assert "db, run.member, run.batch_id" in call
    assert "viewer=_row_viewer(run.member)" in call


def test_a_busy_chunk_names_what_its_earlier_groups_wrote() -> None:
    busy = batch_store.ChunkBusy("busy", {"a": "r"}, {"b": "rule_refused"})
    assert isinstance(busy, batch_store.StoreBusy)
    assert (busy.written, busy.failed) == ({"a": "r"}, {"b": "rule_refused"})


# ── The source fences ───────────────────────────────────────────────────────


def _src(name: str) -> str:
    return (_WORKFLOWS / name).read_text(encoding="utf-8")


def test_only_the_workflow_seam_calls_the_batch_door() -> None:
    callers: set[str] = set()
    for path in (_ROOT / "apps").rglob("*.py"):
        if "node_modules" in path.parts or ".venv" in path.parts:
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\brun_workflow_batch\s*\(", body):
            callers.add(path.relative_to(_ROOT).as_posix())
    assert callers == {
        "apps/services/gateway/gateway/routes/studio/invoke.py",
        "apps/services/gateway/gateway/routes/workflows/service.py",
    }


def test_the_http_route_never_reaches_the_batch() -> None:
    tree = ast.parse((_GATEWAY / "routes" / "studio" / "invoke.py").read_text(encoding="utf-8"))
    route = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.AsyncFunctionDef) and n.name == "invoke_action")
    assert "run_workflow_batch" not in ast.unparse(route)


def test_the_batch_scan_lives_in_the_one_org_loop_after_the_list_check() -> None:
    src = _src("scheduler.py")
    tree = ast.parse(src)
    wait = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "scan_due_waits")
    loop = ast.get_source_segment(src, wait) or ""
    assert loop.index("unattended.org_allowed(org)") < loop.index("scan_open_batches(org)")
    callers = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
               and getattr(n.func, "id", None) == "scan_open_batches"]
    assert len(callers) == 1
    scan = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "scan_open_batches")
    body = [s for s in scan.body if not (isinstance(s, ast.Expr)
                                         and isinstance(s.value, ast.Constant))]
    first = body[0]
    assert isinstance(first, ast.If) and "batch.org_ready(org)" in ast.unparse(first.test)
    assert src.count("await _organization_ids()") == 2


def test_the_scan_never_reads_a_payload_and_needs_the_decision_row() -> None:
    src = _src("scheduler.py")
    body = src[src.index("async def scan_open_batches"):src.index("async def _scheduler_loop")]
    assert "trigger_payload" not in body
    assert "batch.approved_batch(" in body
    assert "_tenant_session(org)" in body and "_tenant_session()" not in body
    approved = _src("batch.py")
    fn = approved[approved.index("async def approved_batch"):approved.index("# ── The scan")]
    assert "park.outcome_of(decision) != park.RESUME" in fn
    assert "_owner_in_org" in fn


def test_the_new_run_moves_the_batch_in_the_transaction_of_its_row() -> None:
    src = _src("service.py")
    fn = src[src.index("async def start_batch_run"):src.index("# ── Programmatic entry points")]
    assert fn.index("INSERT INTO workflow_runs") < fn.index("batch_store.rebind_run(")
    assert "_run_session(org)" in fn and "_RebindLost" in fn
    assert "trigger_kind=\"schedule\"" in fn


def test_the_park_freezes_the_list_in_the_transaction_of_the_decision() -> None:
    src = _src("park.py")
    fn = src[src.index("async def park_with_decision"):src.index("# ── The settle")]
    assert fn.index("_park_batch_below(") < fn.index("ask_decision(")
    assert "**bound_keys" in fn
    assert "commit(" not in _src("batch.py")


def test_this_suite_is_named_in_both_ci_skip_guards() -> None:
    for name in ("pr-check.yml", "deploy.yml"):
        flow = (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", flow)
        assert block, f"{name}: the --must-run argument list is missing"
        assert re.search(r"^[ \t]+tests/unit/test_studio_workflow_batch_r8\.py(?: \\)?$",
                         block.group(1), re.M), name


def test_the_migration_adds_the_resume_count_expand_only() -> None:
    found = [p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
             if "ADD COLUMN IF NOT EXISTS resumes" in p.read_text(encoding="utf-8")]
    assert len(found) == 1
    sql = found[0].read_text(encoding="utf-8")
    assert "DROP " not in sql.upper() and "RENAME" not in sql.upper()
    assert "NOT NULL DEFAULT 0" in sql
    assert "CHECK (resumes BETWEEN 0 AND 100)" in sql
