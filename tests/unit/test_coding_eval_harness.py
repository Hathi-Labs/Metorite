"""WS-43v: the harness of the light eval, with no model and no Docker.

Spec: ``project-docs/specs/maf_coding_engine.md``, the WS-43v slice and §16.

What this file holds, and why each part matters:

1. **The stub of the Projects API** (``evals/coding_engine/stub_api.py``)
   keeps the HR gate of the real dataset route. Without the grant a row never
   carries ``estimate_mins`` or ``cycle_hours``, and a group by person never
   carries an HR measure. WS43-E14 is worth nothing on a stub that leaks them.
2. **The preflight never passes a run it cannot judge.** With no sandbox
   tools a task is SKIPPED, and with no Router on this machine the sweep is
   NO-GO. A production address is refused (WS43-G6).
3. **The scripted runs go through the REAL executor.** ``run_agent_stream``
   runs the real projects-assistant factory, and only the model is
   ``ScriptedModel``. On a checkout with no sandbox tools, each task is
   SKIPPED. With the tool checks off, the HR refusal passes from the real
   stream, and the chart FAILS, because no PNG exists. That is the "never a
   false pass" rule, end to end.
4. **The eval approves nothing.** A confirmation card is declined, and no
   write reaches the API.

The run with the sandbox tools in a container is the sweep itself. It needs
PR #603 and a Docker daemon, so it is not in this file (the README says so).
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from evals.coding_engine import dataset as D
from evals.coding_engine import preflight as P
from evals.coding_engine import run as R
from evals.coding_engine import scripted as S
from evals.coding_engine import stub_api
from evals.coding_engine import tasks as T

pytest.importorskip("agent_framework", reason="agent_framework not installed")

DS = D.load(date(2026, 10, 3))
PRIYA = DS.member(T.MEMBER)
HR = D.Member(email="hr.admin@eval.example", name="Hana Ross", hr_read=True)


def _get(stub: stub_api.ProjectsStub, path: str, member: str = T.MEMBER) -> tuple[int, Any]:
    return stub.handle("GET", path, member)


# ── 1. the stub keeps the HR gate ───────────────────────────────────────────


def test_the_tree_holds_every_project_under_its_space() -> None:
    status, body = _get(stub_api.ProjectsStub(DS), "/projects/tree")
    assert status == 200
    names = {c["name"] for row in body["rows"] for c in row["children"]}
    assert names == {p.name for p in DS.projects}


def test_a_row_never_carries_an_hr_column_without_the_grant() -> None:
    alpha = DS.project("Alpha").id
    status, body = _get(
        stub_api.ProjectsStub(DS),
        f"/projects/analytics/dataset?project_id={alpha}&columns=title,assignees,cycle_hours,estimate_mins",
    )
    assert status == 200
    assert body["hidden_columns"] == ["estimate_mins", "cycle_hours"]
    assert all("cycle_hours" not in r and "estimate_mins" not in r for r in body["rows"])
    assert all("assignees" in r for r in body["rows"])


def test_an_hr_member_gets_the_hr_columns() -> None:
    ds = D.Dataset(DS.today, DS.organization_id, (*DS.members, HR), DS.projects, DS.tasks)
    alpha = DS.project("Alpha").id
    _, body = _get(stub_api.ProjectsStub(ds),
                   f"/projects/analytics/dataset?project_id={alpha}&state=all"
                   "&columns=title,cycle_hours",
                   member=HR.email)
    assert body["hidden_columns"] == []
    assert any(r.get("cycle_hours") for r in body["rows"])


def test_a_group_by_person_hides_the_speed_without_the_grant() -> None:
    design = DS.project("Design").id
    _, body = _get(
        stub_api.ProjectsStub(DS),
        f"/projects/analytics/dataset?project_id={design}&state=closed"
        "&group_by=assignee&measure=cycle_hours_median",
    )
    assert body["measure_hidden"] is True
    assert body["groups"] and all("value" not in g for g in body["groups"])
    assert all(g["n"] > 0 for g in body["groups"])  # a count is for every member


def test_a_small_group_hides_its_speed_without_the_grant() -> None:
    _, body = _get(
        stub_api.ProjectsStub(DS),
        "/projects/analytics/dataset?state=closed&group_by=status&measure=cycle_hours_median",
    )
    assert all("value" in g for g in body["groups"] if g.get("measure_hidden") is not True)
    _, beta = _get(
        stub_api.ProjectsStub(DS),
        f"/projects/analytics/dataset?project_id={DS.project('Beta').id}&state=closed"
        "&group_by=status&measure=cycle_hours_median",
    )
    assert beta["groups"][0].get("measure_hidden") is True  # one person: their figure


def test_the_filters_give_the_fixture_sets() -> None:
    stub = stub_api.ProjectsStub(DS)
    alpha = DS.project("Alpha").id
    _, overdue = _get(stub, f"/projects/analytics/dataset?project_id={alpha}&overdue=true&columns=number")
    assert [r["number"] for r in overdue["rows"]] == [t.number for t in D.overdue(DS)]
    first, _last = D.previous_month(DS.today)
    _, closed = _get(stub, (
        f"/projects/analytics/dataset?project_id={alpha}&state=closed&columns=number"
        f"&completed_after={first}&completed_before={DS.today.replace(day=1)}"
    ))
    assert [r["number"] for r in closed["rows"]] == [t.number for t in D.closed_last_month(DS)]


@pytest.mark.parametrize("path, member, status", [
    ("/projects/nodes/x/summary", T.MEMBER, 404),
    ("/projects/analytics/dataset?viewer=someone", T.MEMBER, 422),
    ("/projects/tree", "stranger@elsewhere.example", 403),
    ("/projects/tree", "", 403),
])
def test_the_stub_refuses_what_the_gateway_refuses(path: str, member: str, status: int) -> None:
    got, body = _get(stub_api.ProjectsStub(DS), path, member)
    assert got == status
    assert body["detail"]


def test_the_real_tool_reads_the_stub_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """``task_dataset`` itself, over the wire, says which columns the route hid."""
    import skill_projects.client as client
    from acb_skills.memory_tools import _bind_memory_user_id, _unbind_memory_user_id
    from skill_projects.reads import task_dataset

    running = stub_api.serve(DS)
    monkeypatch.setattr(client, "gateway_url", lambda: running.url)
    binding = _bind_memory_user_id(T.MEMBER)
    try:
        text = asyncio.run(task_dataset(project_id=DS.project("Alpha").id,
                                        columns="title,cycle_hours"))
    finally:
        _unbind_memory_user_id(binding)
        running.close()
    assert "Hidden columns: cycle_hours" in text
    assert DS.tasks_in("Alpha")[0].title in text
    assert running.stub.requests[-1].member == T.MEMBER


# ── 2. the preflight ────────────────────────────────────────────────────────


def _find_spec_with(present: bool) -> Any:
    import importlib.util

    real = importlib.util.find_spec

    def fake(name: str, *a: Any, **k: Any) -> Any:
        if name == "acb_skills.sandbox_tools":
            return object() if present else None
        return real(name, *a, **k)

    return fake


def test_no_sandbox_module_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(P.importlib.util, "find_spec", _find_spec_with(False))
    gate = P.sandbox_tools(DS.organization_id)
    assert gate.ok is False
    assert gate.reason.startswith(P.SKIPPED_ABSENT)


@pytest.mark.parametrize("covered", [False, True])
def test_the_broker_cover_decides(monkeypatch: pytest.MonkeyPatch, covered: bool) -> None:
    from orchestrator import sandbox_broker

    monkeypatch.setattr(P.importlib.util, "find_spec", _find_spec_with(True))
    monkeypatch.setattr(sandbox_broker, "covers", lambda agent, org: covered, raising=False)
    gate = P.sandbox_tools(DS.organization_id)
    assert gate.ok is covered
    if not covered:
        assert gate.reason.startswith(P.SKIPPED_ABSENT)
        assert f"MAF_CODING_SCOPE=projects:{DS.organization_id}" in gate.reason


def test_the_offered_tools_must_hold_run_command_and_the_file_tools() -> None:
    assert P.tools_offered_problem(["projects_tree", "write_artifact"]).startswith(P.SKIPPED_ABSENT)
    assert P.tools_offered_problem(sorted(P.REQUIRED_TOOLS | {"load_skill"})) is None


@pytest.mark.parametrize("url, local", [
    ("http://127.0.0.1:8080", True), ("http://localhost:8080/v1", True), ("http://[::1]:80", True),
    ("https://app.metorite.com", False), ("http://10.0.0.5:8080", False),
])
def test_only_this_machine_is_local(url: str, local: bool) -> None:
    assert P.is_local(url) is local


def test_a_production_router_is_no_go() -> None:
    gate = P.router("https://app.metorite.com/v1", "k", "tier-balanced", T.MEMBER)
    assert gate.ok is False
    assert "WS43-G6" in gate.reason


def _client(serving: bool, completion: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/settings/llm":
            return httpx.Response(200, json={"router_serving": serving})
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(completion, json={"choices": []})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("serving, completion, ok", [
    (False, 200, False), (True, 404, False), (True, 200, True),
])
def test_the_router_check(serving: bool, completion: int, ok: bool) -> None:
    gate = P.router("http://127.0.0.1:8080/v1", "k", "tier-balanced", T.MEMBER,
                    client=_client(serving, completion))
    assert gate.ok is ok
    assert gate.reason.startswith(P.NO_GO) is not ok


def test_a_stack_that_does_not_answer_is_no_go() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    gate = P.router("http://127.0.0.1:9", "k", "tier-balanced", T.MEMBER,
                    client=httpx.Client(transport=httpx.MockTransport(down)))
    assert gate.ok is False and "does not answer" in gate.reason


def test_main_is_no_go_without_a_local_router(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "https://router.example.com")
    assert R.main(["--tasks", "WS43-E10", "--out", str(tmp_path)]) == R.EXIT_NO_GO
    assert "WS43-G6" in json.loads((tmp_path / "NO-GO.json").read_text(encoding="utf-8"))["no_go"]


# ── tasks and the summary ───────────────────────────────────────────────────


def test_the_task_selector() -> None:
    assert [t.id for t in T.select("all")] == list(T.TASK_IDS)
    assert [t.id for t in T.select("WS43-E10..WS43-E17")] == list(T.TASK_IDS)
    assert [t.id for t in T.select("WS43-E12, WS43-E10")] == ["WS43-E12", "WS43-E10"]
    with pytest.raises(ValueError):
        T.select("WS43-E9")


def test_the_eight_tasks_have_a_checker_and_a_script() -> None:
    from evals.coding_engine.checkers import CHECKERS

    assert set(T.TASK_IDS) == set(CHECKERS) == {f"WS43-E{n}" for n in range(10, 18)}
    for spec in T.TASKS:
        assert S.sessions_of(spec.id) == len(spec.sessions), spec.id


@pytest.mark.parametrize("statuses, code", [
    (["pass", "pass"], R.EXIT_PASS), (["pass", "skipped"], R.EXIT_SKIPPED),
    (["skipped", "fail"], R.EXIT_FAIL), (["error"], R.EXIT_FAIL),
])
def test_the_exit_code(statuses: list[str], code: int) -> None:
    assert R.exit_code([{"status": s} for s in statuses]) == code


# ── 3. scripted runs through the REAL executor ──────────────────────────────


@pytest.fixture
def harness(tmp_path: Path):
    from orchestrator import executor

    with R.Harness(dataset=D.load(), org=DS.organization_id, state_root=tmp_path / "state",
                   tier="tier-balanced", scripted=True, clean_blob_store=False) as h:
        yield h
    executor._RUN_QUEUES.clear()
    executor._pending_user_input.clear()


def _sweep(h: R.Harness, ids: list[str], out: Path, **kw: Any) -> list[dict[str, Any]]:
    return asyncio.run(R.sweep(h, [T.by_id(i) for i in ids], 1, out, **kw))


def test_with_no_sandbox_tools_every_task_is_skipped(
    harness: R.Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(P.importlib.util, "find_spec", _find_spec_with(False))
    records = _sweep(harness, list(T.TASK_IDS), tmp_path / "out")
    assert [r["status"] for r in records] == ["skipped"] * len(T.TASK_IDS)
    assert all(r["failure"].startswith(P.SKIPPED_ABSENT) for r in records)
    assert R.exit_code(records) == R.EXIT_SKIPPED
    assert len(list((tmp_path / "out").glob("WS43-E*-run1.json"))) == len(T.TASK_IDS)


def test_a_model_that_was_not_offered_the_tools_is_skipped(
    harness: R.Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The preflight says yes, and the first request still shows no ``run_command``."""
    monkeypatch.setattr(P, "sandbox_tools", lambda org: P.Check(True, "forced"))
    [record] = _sweep(harness, ["WS43-E10"], tmp_path / "out")
    assert record["status"] == "skipped"
    assert "was not offered" in record["failure"] and "run_command" in record["failure"]


def test_the_refusal_passes_and_the_chart_fails_without_the_tools(
    harness: R.Harness, tmp_path: Path,
) -> None:
    e14, e10 = _sweep(harness, ["WS43-E14", "WS43-E10"], tmp_path / "out", check_sandbox=False)
    assert e14["status"] == "pass", e14["failure"]
    assert [c["name"] for c in e14["sessions"][0]["tool_calls"]] == ["task_dataset"]
    assert e10["status"] == "fail"
    assert e10["failure"].startswith("png_in_outputs")
    failed = {r["rule"] for r in e10["rules"] if not r["pass"]}
    assert {"png_in_outputs", "artifact_card"} <= failed
    for key in ("task", "run", "repeat", "status", "rules", "wall_s", "tool_calls",
                "failed_tool_calls", "tokens", "failure", "sha", "tier", "mode", "sessions"):
        assert key in e10, key
    assert e10["mode"] == "scripted" and e10["failed_tool_calls"] >= 1
    on_disk = json.loads((tmp_path / "out" / "WS43-E10-run1.json").read_text(encoding="utf-8"))
    assert on_disk["status"] == "fail"
    paths = [q.path for q in harness.stub.stub.requests]
    assert "/projects/analytics/dataset" in paths and "/projects/tree" in paths


def test_the_eval_declines_every_card(
    harness: R.Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    steps = [("tool", "create_personal_task", json.dumps({"title": "Plan the sprint"})),
             ("text", "I did not capture it.")]
    monkeypatch.setattr(S, "steps_for", lambda task_id, session, ds: steps)
    [record] = _sweep(harness, ["WS43-E15"], tmp_path / "out", check_sandbox=False)
    assert record["cards_declined"] == 1
    assert record["approvals"] == 0
    [call] = record["sessions"][0]["tool_calls"]
    assert call["name"] == "create_personal_task" and "Cancelled" in call["result"]
    assert [q for q in harness.stub.stub.requests if q.method != "GET"] == []
