"""A push of agent code from a chat run needs the reviewer of the queue.

Found in the review of PR #1167 (2026-10-19). The addendum told the agent to
run ``git push --no-verify origin HEAD`` when the member said "commit and
push". ``_detect_agent_commits`` then recorded that commit as ``approved`` by
``chat:autopush``. The agent's clone holds the operator's ``GITHUB_TOKEN`` in
its remote URL, so the push used the operator's credentials, and it skipped
the reviewer that #1167 puts on ``/agent/mutations/*``.

The rule is one rule, in one place: ``acb_auth.agent_operator``. The member
holds ``agents:manage`` AND the member's org is not empty AND it is the
first-party org OR it is in ``AGENT_REGISTRY_ORGS``. The queue's gate
(``gateway.routes.agent._require_mutation_reviewer``) and the push gate both
call it. Three server-side controls read the push gate:

1. the Copilot permission guard refuses a ``git push`` shell request of a run
   that did not bind ``agent_push_allowed=True``;
2. ``run_script`` refuses a script whose text runs ``git push``, on the same
   flag;
3. ``_detect_agent_commits`` records a pushed commit of this run as approved
   only when the member passes the rule. Otherwise it stays pending.

The decision reads the member and the org from the run binding, never from
the request (R5). No database: the access resolver and the settings are
stubbed where the code imports them.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import acb_auth
import acb_common
import pytest
from acb_auth import agent_operator
from acb_auth.agent_operator import (
    AGENT_COMMIT_REVIEW_PERMISSION,
    is_registry_operator_org,
    may_review_agent_commits,
    registry_operator_orgs,
)
from acb_auth.permissions import EffectiveAccess, build_access

OP_ORG = "aaaaaaaa-0000-4000-8000-000000000001"     # in AGENT_REGISTRY_ORGS
CUST_ORG = "bbbbbbbb-0000-4000-8000-000000000002"   # a customer org
FP_ORG = "cccccccc-0000-4000-8000-000000000003"     # first_party, NOT listed
OP_ADMIN = "admin@operator.example"                  # agents:manage, OP_ORG
OP_EMPLOYEE = "employee@operator.example"            # no agents:manage
CUST_OWNER = "owner@customer.example"                # '*' in a customer org
FP_ADMIN = "admin@fracktal.example"                  # agents:manage, FP_ORG
FP_EMPLOYEE = "employee@fracktal.example"            # no agents:manage

_ACCESS: dict[str, EffectiveAccess] = {
    OP_ADMIN: build_access(["agents:manage", "agents:run:*"], roles=["admin"]),
    OP_EMPLOYEE: build_access(["agents:run:*"], roles=["employee"]),
    CUST_OWNER: build_access(["*"], roles=["owner"]),
    FP_ADMIN: build_access(["agents:manage", "agents:run:*"], roles=["admin"]),
    FP_EMPLOYEE: build_access(["agents:run:*"], roles=["employee"]),
}


@pytest.fixture()
def first_party_reads(monkeypatch) -> list[Any]:
    """Stub the ONE first_party read. Only FP_ORG is first-party.

    Each call is logged, so a test can show which org was read, and that an
    empty org never reaches the read (it would answer for a sole org).
    """
    calls: list[Any] = []

    async def _read(org: str | None) -> bool | None:
        calls.append(org)
        if org == FP_ORG:
            return True
        return False if org in (OP_ORG, CUST_ORG) else None
    monkeypatch.setattr(agent_operator, "read_first_party", _read)
    return calls


@pytest.fixture()
def operator_settings(monkeypatch, first_party_reads) -> list[Any]:
    """``AGENT_REGISTRY_ORGS`` names the operator org only, not FP_ORG."""
    monkeypatch.setattr(
        acb_common, "get_settings",
        lambda: SimpleNamespace(agent_registry_orgs=OP_ORG.upper()),
    )
    return first_party_reads


# ── 1. The one rule ─────────────────────────────────────────────────────────

def test_the_rule_reads_agents_manage() -> None:
    assert AGENT_COMMIT_REVIEW_PERMISSION == "agents:manage"


def test_the_setting_parses_and_fails_closed() -> None:
    assert registry_operator_orgs("") == frozenset()
    assert registry_operator_orgs(" A , b,,") == frozenset({"a", "b"})
    assert not is_registry_operator_org("a", raw="")
    assert is_registry_operator_org("A", raw="a")
    assert is_registry_operator_org("anything", raw="*")
    assert not is_registry_operator_org("", raw="*")
    assert not is_registry_operator_org(None, raw="*")


def _may(org: str | None, has: Any) -> bool:
    return asyncio.run(may_review_agent_commits(org, has))


@pytest.mark.parametrize("member, org, allowed", [
    (OP_ADMIN, OP_ORG, True),
    (FP_ADMIN, FP_ORG, True),         # first-party, NOT in the registry list
    (FP_EMPLOYEE, FP_ORG, False),     # first-party, no agents:manage
    (OP_EMPLOYEE, OP_ORG, False),     # the operator org, no agents:manage
    (CUST_OWNER, CUST_ORG, False),    # '*' in a customer org is not enough
    (OP_ADMIN, CUST_ORG, False),      # the right permission, the wrong org
    (OP_ADMIN, "dddddddd-0000-4000-8000-000000000004", False),  # no row
    (OP_ADMIN, None, False),
    (OP_ADMIN, "", False),
    (OP_ADMIN, "   ", False),
])
def test_the_rule_needs_both_halves(operator_settings, member, org, allowed) -> None:
    assert _may(org, _ACCESS[member].has) is allowed


@pytest.mark.parametrize("org", [None, "", "   "])
def test_an_empty_org_never_reaches_the_sole_org_read(operator_settings, org) -> None:
    """``read_first_party(None)`` answers for a sole org. It must never run here."""
    assert _may(org, _ACCESS[FP_ADMIN].has) is False
    assert operator_settings == []


def test_an_operator_org_needs_no_first_party_read(operator_settings) -> None:
    assert _may(OP_ORG, _ACCESS[OP_ADMIN].has) is True
    assert operator_settings == []


def test_a_member_without_the_permission_causes_no_org_read(operator_settings) -> None:
    assert _may(FP_ORG, _ACCESS[FP_EMPLOYEE].has) is False
    assert operator_settings == []


def test_the_first_party_read_is_given_the_org_itself(operator_settings) -> None:
    assert _may(FP_ORG, _ACCESS[FP_ADMIN].has) is True
    assert operator_settings == [FP_ORG]


def test_a_failed_first_party_read_reads_as_no(monkeypatch, operator_settings) -> None:
    async def _boom(_org: str | None) -> bool | None:
        raise RuntimeError("database down")
    monkeypatch.setattr(agent_operator, "read_first_party", _boom)
    assert _may(FP_ORG, _ACCESS[FP_ADMIN].has) is False


def test_a_permission_check_that_raises_reads_as_no(operator_settings) -> None:
    def _boom(_p: str) -> bool:
        raise RuntimeError("resolver down")
    assert _may(OP_ORG, _boom) is False


def test_mutation_reads_first_party_through_the_one_query(monkeypatch) -> None:
    """One query: ``orchestrator.mutation._read_first_party`` delegates to it."""
    from orchestrator import mutation

    seen: list[Any] = []

    async def _read(org: str | None) -> bool | None:
        seen.append(org)
        return True
    monkeypatch.setattr(agent_operator, "read_first_party", _read)
    assert asyncio.run(mutation._read_first_party(FP_ORG)) is True
    assert seen == [FP_ORG]
    assert "SELECT first_party" not in inspect.getsource(mutation)


def test_the_gateway_reads_the_same_parse(monkeypatch) -> None:
    """One parse of AGENT_REGISTRY_ORGS: the queue's gate delegates to it."""
    import gateway.routes.agent as agent_routes

    monkeypatch.setattr(
        agent_routes, "get_settings",
        lambda: SimpleNamespace(agent_registry_orgs=" X ,y"),
    )
    assert agent_routes._registry_orgs() == registry_operator_orgs(" X ,y")
    src = inspect.getsource(agent_routes._registry_orgs)
    assert "registry_operator_orgs" in src


# ── 2. The decision reads the run binding, server-side ─────────────────────

@pytest.fixture()
def run_binding(monkeypatch, operator_settings):
    """Bind a member and an org the way a run boundary does."""
    import orchestrator.executor as executor

    state: dict[str, Any] = {}

    def _ctx() -> dict[str, Any]:
        out: dict[str, Any] = {}
        if state.get("member"):
            out["user"] = state["member"]
            out["member_verified"] = state.get("verified", "1")
        return out

    async def _resolve(email: str | None, **_kw: Any) -> EffectiveAccess:
        return _ACCESS.get(str(email or "").lower(), EffectiveAccess(is_active=False))

    monkeypatch.setattr(acb_common, "get_run_context", _ctx)
    monkeypatch.setattr(executor, "_current_run_org", lambda: state.get("org"))
    monkeypatch.setattr(acb_auth, "resolve_access", _resolve)

    def _bind(member: str | None, org: str | None, verified: str = "1") -> None:
        state.update(member=member, org=org, verified=verified)
    return _bind


def test_the_decision_takes_no_argument_from_the_request() -> None:
    import orchestrator.executor as executor
    assert not inspect.signature(executor._agent_push_allowed).parameters


@pytest.mark.parametrize("member, org, verified, allowed", [
    (OP_ADMIN, OP_ORG, "1", True),
    (FP_ADMIN, FP_ORG, "1", True),    # the owner's case: first-party, unlisted
    (FP_EMPLOYEE, FP_ORG, "1", False),
    (OP_EMPLOYEE, OP_ORG, "1", False),
    (CUST_OWNER, CUST_ORG, "1", False),
    (OP_ADMIN, CUST_ORG, "1", False),
    (OP_ADMIN, OP_ORG, "0", False),   # an unverified claim is no member
    (None, OP_ORG, "1", False),
    (OP_ADMIN, None, "1", False),
])
def test_the_decision_reads_the_member_and_org_of_the_run(
    run_binding, member, org, verified, allowed,
) -> None:
    import orchestrator.executor as executor

    run_binding(member, org, verified)
    ok, why = asyncio.run(executor._agent_push_allowed())
    assert ok is allowed, why


def test_a_refused_host_shell_binds_false_without_a_read(monkeypatch) -> None:
    import orchestrator.executor as executor

    async def _never() -> tuple[bool, str]:
        raise AssertionError("no access read for a run with no shell")
    monkeypatch.setattr(executor, "_agent_push_allowed", _never)
    assert asyncio.run(executor._agent_push_binding(True)) is False


def test_every_run_boundary_binds_the_flag() -> None:
    """The stream, the batch and the sub-agent boundary each bind it."""
    import orchestrator.executor as executor

    src = inspect.getsource(executor)
    assert src.count("agent_push_allowed=await _agent_push_binding(") == 3


# ── 3. The Copilot permission guard ─────────────────────────────────────────

_INVOCATION = {"session_id": "s-push", "managed_settings_enabled": False}


def _with_run(*, push_allowed: bool | None):
    """A run whose host shell may run, with ``agent_push_allowed`` set or absent."""
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    @contextlib.contextmanager
    def _scope():
        with artifact_context_scope():
            values: dict[str, Any] = {
                "agent_name": "probe", "workspace_root": str(Path.cwd()),
                "host_shell_refused": False,
            }
            if push_allowed is not None:
                values["agent_push_allowed"] = push_allowed
            bind_artifact_context(**values)
            yield
    return _scope()


def _refused(result: Any) -> bool:
    from copilot.generated.rpc import PermissionDecisionReject
    return isinstance(result, PermissionDecisionReject)


_PUSH = "git push --no-verify origin HEAD"


@pytest.mark.parametrize("command", [
    _PUSH,
    "git add -A && git commit -m 'feat: x' && git push --no-verify origin HEAD",
    "git -C /srv/acb_agents/repos/agent-x push --no-verify",
    "git -c core.hooksPath=/dev/null push origin HEAD",
    "cd repo; git push",
    "GIT_TERMINAL_PROMPT=0 git push",
])
def test_a_member_who_is_not_a_reviewer_cannot_push(command: str) -> None:
    from acb_skills import permission_policy as pp

    ran: list[Any] = []
    guarded = pp.guard_shared_agent_shell(lambda r, i: ran.append(r) or "approved")
    for flag in (False, None):        # None: no key, so it fails closed
        with _with_run(push_allowed=flag):
            result = guarded({"kind": "shell", "full_command_text": command}, _INVOCATION)
        assert _refused(result), (flag, result)
        assert pp.AGENT_PUSH_WITHHELD_REASON in result.feedback
    assert not ran, "the push reached the shell"


def test_a_reviewer_pushes_as_before() -> None:
    from acb_skills import permission_policy as pp

    guarded = pp.guard_shared_agent_shell(lambda r, i: "approved")
    with _with_run(push_allowed=True):
        assert guarded({"kind": "shell", "full_command_text": _PUSH}, _INVOCATION) == "approved"


@pytest.mark.parametrize("command", [
    "git commit -m 'push the fix later'",
    "git log --grep=push",
    "git status",
    "echo push",
])
def test_other_git_commands_are_not_a_push(command: str) -> None:
    from acb_skills import permission_policy as pp

    guarded = pp.guard_shared_agent_shell(lambda r, i: "approved")
    with _with_run(push_allowed=False):
        assert guarded({"kind": "shell", "full_command_text": command}, _INVOCATION) == "approved"


def test_the_sdk_shell_request_shape_is_read() -> None:
    from acb_skills import permission_policy as pp
    from copilot.generated.session_events import (
        PermissionRequestShell,
        PermissionRequestShellCommand,
    )

    request = PermissionRequestShell(
        can_offer_session_approval=False,
        commands=[PermissionRequestShellCommand(identifier="git", read_only=False)],
        full_command_text=_PUSH,
        has_write_file_redirection=False,
        intention="push the commit",
        possible_paths=[],
        possible_urls=[],
    )
    guarded = pp.guard_shared_agent_shell(lambda r, i: "approved")
    with _with_run(push_allowed=False):
        assert _refused(guarded(request, _INVOCATION))


# ── 4. run_script reads the script text ─────────────────────────────────────

def _bound_workspace(tmp_path: Path, *, push_allowed: bool | None):
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    @contextlib.contextmanager
    def _scope():
        with artifact_context_scope():
            values: dict[str, Any] = {"agent_name": "probe", "workspace_root": str(tmp_path)}
            if push_allowed is not None:
                values["agent_push_allowed"] = push_allowed
            bind_artifact_context(**values)
            yield
    return _scope()


def _script(tmp_path: Path, body: str) -> str:
    scripts = tmp_path / "agent-data" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "ship.sh").write_text(body, encoding="utf-8")
    return "agent-data/scripts/ship.sh"


def _fake_run(calls: list[Any]):
    def _run(cmd, **_kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="done", stderr="")
    return _run


def test_run_script_refuses_a_push_script_of_a_non_reviewer(monkeypatch, tmp_path) -> None:
    from acb_skills import code_tools

    calls: list[Any] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))
    path = _script(tmp_path, "#!/bin/sh\ncd ../../repos/x && git push --no-verify\n")
    for flag in (False, None):
        with _bound_workspace(tmp_path, push_allowed=flag):
            out = asyncio.run(code_tools.run_script(path))
        assert out.startswith("run_script blocked:"), out
    assert not calls, "the push script ran"


def test_run_script_runs_a_push_script_of_a_reviewer(monkeypatch, tmp_path) -> None:
    from acb_skills import code_tools

    calls: list[Any] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))
    path = _script(tmp_path, "#!/bin/sh\ngit push --no-verify\n")
    with _bound_workspace(tmp_path, push_allowed=True):
        out = asyncio.run(code_tools.run_script(path))
    assert "blocked" not in out and calls


def test_run_script_runs_a_plain_script_of_anyone(monkeypatch, tmp_path) -> None:
    from acb_skills import code_tools

    calls: list[Any] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))
    path = _script(tmp_path, "#!/bin/sh\necho report\n")
    with _bound_workspace(tmp_path, push_allowed=False):
        out = asyncio.run(code_tools.run_script(path))
    assert "blocked" not in out and calls


# ── 5. The record: approved only for a reviewer ────────────────────────────

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture()
def repo(tmp_path) -> tuple[Path, str, str, str]:
    """A clone with a base commit (from before the run) and one run commit."""
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git is not installed")
    r = tmp_path / "agent-x"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "bot@example.com")
    _git(r, "config", "user.name", "bot")
    _git(r, "config", "commit.gpgsign", "false")
    (r / "a.txt").write_text("a\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "chore: base")
    base = _git(r, "rev-parse", "HEAD")
    (r / "b.txt").write_text("b\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "feat: the agent's change")
    head = _git(r, "rev-parse", "HEAD")
    return r, base, head, str(r)


def _detect(monkeypatch, repo_dir: str, since: str, *, gate: tuple[bool, str] | None):
    """``gate=None`` keeps the real ``_agent_push_allowed`` (the run binding)."""
    """Run the detector with every commit on the remote. Returns the rows."""
    import orchestrator.executor as executor
    import orchestrator.mutation as mutation

    rows: list[dict[str, Any]] = []
    audits: list[Any] = []
    asked: list[int] = []

    async def _on_remote(_d: str, _sha: str) -> bool:
        return True                     # the agent pushed every commit

    async def _register(**kw: Any) -> None:
        rows.append(kw)

    async def _diff(_d: str, _sha: str) -> str:
        return "diff"

    async def _gate() -> tuple[bool, str]:
        asked.append(1)
        return gate

    monkeypatch.setattr(executor, "_host_git_ok", lambda p: bool(p))
    monkeypatch.setattr(executor, "_commit_on_remote", _on_remote)
    monkeypatch.setattr(executor, "_graph_session_opener", lambda tid: None)
    if gate is not None:
        monkeypatch.setattr(executor, "_agent_push_allowed", _gate)
    monkeypatch.setattr(executor, "record", audits.append)
    monkeypatch.setattr(mutation, "_register_pending_commit", _register)
    monkeypatch.setattr(mutation, "_git_diff", _diff)
    asyncio.run(executor._detect_agent_commits(
        "agent-x", repo_dir, "run-1", since_sha=since, thread_id="t-1",
    ))
    return rows, audits, asked


def test_a_non_reviewer_push_is_pending_and_never_approved(monkeypatch, repo) -> None:
    _r, base, head, repo_dir = repo
    rows, audits, asked = _detect(
        monkeypatch, repo_dir, base, gate=(False, "not a reviewer"),
    )
    by_sha = {row["commit_sha"]: row for row in rows}
    assert by_sha[head]["status"] == "pending"
    assert by_sha[head]["reviewed_by"] is None
    assert asked == [1], "the gate is asked once per pass"
    actions = [getattr(a, "action", "") for a in audits]
    assert "agent_push_without_reviewer" in actions


def test_a_reviewer_push_is_approved_as_before(monkeypatch, repo) -> None:
    _r, base, head, repo_dir = repo
    rows, audits, _asked = _detect(monkeypatch, repo_dir, base, gate=(True, ""))
    by_sha = {row["commit_sha"]: row for row in rows}
    assert by_sha[head]["status"] == "approved"
    assert by_sha[head]["reviewed_by"] == "chat:autopush"
    actions = [getattr(a, "action", "") for a in audits]
    assert "agent_push_without_reviewer" not in actions


@pytest.mark.parametrize("member, org, approved", [
    (FP_ADMIN, FP_ORG, True),         # the owner: first-party, unlisted
    (FP_EMPLOYEE, FP_ORG, False),
    (CUST_OWNER, CUST_ORG, False),    # a customer admin, not listed
])
def test_the_real_rule_decides_the_record(
    monkeypatch, repo, run_binding, member, org, approved,
) -> None:
    """No stub on the decision: the run binding and the one rule decide."""
    import orchestrator.executor as executor

    _r, base, head, repo_dir = repo
    run_binding(member, org)
    assert asyncio.run(executor._agent_push_binding(False)) is approved
    rows, _audits, _asked = _detect(monkeypatch, repo_dir, base, gate=None)
    row = {r["commit_sha"]: r for r in rows}[head]
    assert row["status"] == ("approved" if approved else "pending")
    assert row["reviewed_by"] == ("chat:autopush" if approved else None)


def test_a_pushed_commit_from_before_the_run_keeps_its_record(monkeypatch, repo) -> None:
    """The catch-up scan: this run did not make it, so the member is not asked."""
    _r, base, _head, repo_dir = repo
    rows, _audits, _asked = _detect(
        monkeypatch, repo_dir, base, gate=(False, "not a reviewer"),
    )
    by_sha = {row["commit_sha"]: row for row in rows}
    assert by_sha[base]["status"] == "approved"


# ── 6. The addendum matches the control ────────────────────────────────────

def test_the_addendum_no_longer_promises_an_approval() -> None:
    from acb_skills import addendum

    src = inspect.getsource(addendum)
    assert "Metorite checks this, not you" in src
    assert "the user does not need to approve it again" not in src
