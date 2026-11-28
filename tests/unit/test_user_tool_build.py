"""SBX-F12: the build of a user tool (SBX-4b1, Metorite task 3986).

Spec: ``project-docs/specs/agent_sandbox.md`` §12.4. This file holds
done-when 7 to 18 and 27 of SBX-4b1, and 14b and 18b of the audit. SBX-4b2
adds 31, 35 and 36.

Three halves:

* **Database-free (always runs).** The package check, the round cap, the
  build budget, the conditions, the clean round, the install limits and the
  thread lock, on a fake broker and fake stores.
* **R8** (``test_each_step_writes_one_run_row``): the rows on a real
  database, as the NOBYPASSRLS role. Gate ``TENANT_LADDER_DATABASE_URL``.
* **sandbox_docker** (done-when 8 to 10, 13, 14, 14b and 18b): a real broker,
  a real proxy, a stub package index and the door kit on a real port. The
  ``sandbox_docker`` job has no database, so ``mint``, ``revoke_step``,
  ``lookup`` and ``record_run`` are stand-ins there (8b).
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.unit.test_coding_sandbox_image import coding_sandbox_image  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_broker_agent_names_are_reserved_at_registration(monkeypatch) -> None:
    """Done-when 7 (decision 11). ``register_agent`` answers 409 for
    ``user-tool`` and ``compute`` and saves nothing. Both names pass the name
    rule, so only the reserved set stops them.

    The set is pinned to ``COMPUTE_AGENT`` and ``USER_TOOL_AGENT`` of the
    broker (the promise of part (c)).

    Mutation: drop ``BROKER_AGENT_NAMES`` from the reserved set -> red."""
    pytest.importorskip("gateway.routes.agent")
    from acb_skills.agent_paths import AGENT_NAME_RE, BROKER_AGENT_NAMES
    from fastapi import BackgroundTasks, HTTPException
    from gateway.routes import agent as routes
    from orchestrator.sandbox_broker import COMPUTE_AGENT, USER_TOOL_AGENT

    assert frozenset({USER_TOOL_AGENT, COMPUTE_AGENT}) == BROKER_AGENT_NAMES
    monkeypatch.setattr(routes, "_load_dynamic_agents", lambda: [])
    saved: list[Any] = []
    monkeypatch.setattr(routes, "_save_dynamic_agents", lambda a: saved.append(a))
    for name in sorted(BROKER_AGENT_NAMES):
        assert AGENT_NAME_RE.fullmatch(name), "the name rule alone lets it pass"
        req = routes.RegisterAgentRequest(name=name, description="x",
                                          local_path=str(REPO_ROOT))
        with pytest.raises(HTTPException) as exc:
            asyncio.run(routes.register_agent(
                req, BackgroundTasks(), user=SimpleNamespace(email="o@x.test")))
        assert exc.value.status_code == 409, name
        assert "reserved" in str(exc.value.detail), name
    assert saved == []


def test_one_module_runs_sql_on_user_tool_runs() -> None:
    """SBX-4b1 decision 10 (R7): `gateway/studio/user_tool_runs.py` is the
    one module that runs SQL on `user_tool_runs`. Mutation: add a query of the
    table to any other source file, and this test fails."""
    import re

    pattern = re.compile(
        r"(?i)\b(?:from|into|update|join|table)\s+user_tool_runs\b")
    owner = REPO_ROOT / "apps/services/gateway/gateway/studio/user_tool_runs.py"
    others = []
    for root in ("apps", "packages"):
        for path in (REPO_ROOT / root).rglob("*.py"):
            if path == owner or "/tests/" in str(path) or ".venv" in path.parts:
                continue
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                others.append(str(path.relative_to(REPO_ROOT)))
    assert others == [], others


def test_a_future_start_is_clamped_to_the_database_clock() -> None:
    """Review P2: a host clock ahead of the database must not break the CHECK
    `ended_at >= started_at`. Mutation: drop the LEAST, and this test fails."""
    from gateway.studio import user_tool_runs

    assert ("LEAST(COALESCE(CAST(:started AS timestamptz), now()), now())"
            in user_tool_runs._INSERT_SQL)


# ═══════════════════════════════════════════════════════════════════════════
# SBX-4b1, the step engine (§12.4 decisions 1 to 8)
# ═══════════════════════════════════════════════════════════════════════════

ENGINE_ORG = "aaaaaaaa-0000-4000-8000-0000000004b1"
ENGINE_MEMBER = "member@example.com"
DOOR_TOKEN = "msbx_" + "S" * 43
SKILL_MD = "---\nname: lead-count\ndescription: Counts the leads of the member.\n---\nCall it.\n"
GOOD_HASH = "a" * 64


def package_files(**over: Any) -> dict[str, bytes]:
    """A valid package of the five files of §6.3, with *over* in place."""
    import json

    tool = {
        "input_schema": {"type": "object", "required": ["n"],
                         "properties": {"n": {"type": "integer"}}},
        "output_schema": {"type": "object", "required": ["pages"],
                          "properties": {"pages": {"type": "integer"}}},
        "read_scopes": ["crm:read"],
        "write_scopes": [],
    }
    files = {
        "SKILL.md": SKILL_MD.encode(),
        "tool.json": json.dumps(tool).encode(),
        "main.py": b"print('ok')\n",
        "requirements.lock": f"mtrfixture==1.0.0 --hash=sha256:{GOOD_HASH}\n".encode(),
        "smoke.json": json.dumps({"input": {"n": 2}}).encode(),
    }
    for name, value in over.items():
        key = {"tool_json": "tool.json", "smoke_json": "smoke.json", "skill_md": "SKILL.md",
               "main_py": "main.py", "lock": "requirements.lock"}.get(name, name)
        if value is None:
            files.pop(key, None)
        else:
            files[key] = value if isinstance(value, bytes) else str(value).encode()
    return files


def write_package(pkg: Path, files: dict[str, bytes]) -> Path:
    pkg.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (pkg / name).write_bytes(data)
    return pkg


def _tool(**over: Any) -> bytes:
    import json

    tool: dict[str, Any] = json.loads(package_files()["tool.json"])
    tool.update(over)
    return json.dumps(tool).encode()


# ── decision 5: the package check ───────────────────────────────────────────


@pytest.mark.parametrize("missing", [
    "SKILL.md", "tool.json", "main.py", "requirements.lock", "smoke.json",
])
def test_a_package_without_each_file_is_refused(tmp_path: Path, missing: str) -> None:
    """Done-when 11. Mutation: drop the missing-file check -> red."""
    from acb_skills import user_tool_build as ute

    pkg = write_package(tmp_path / "pkg", package_files(**{missing: None}))
    with pytest.raises(ute.PackageRefused) as exc:
        ute.check_package(pkg, {"crm:read"})
    assert exc.value.code == ute.MISSING_FILE


@pytest.mark.parametrize("line", [
    "mtrfixture==1.0.0",
    "mtrfixture==1.0.0 --hash=md5:" + "a" * 32,
    "mtrfixture==1.0.0 --hash=sha256:" + "a" * 63,
    "mtrfixture>=1.0.0 --hash=sha256:" + "a" * 64,
    "--extra-index-url https://evil.example/simple",
    "-e git+https://evil.example/x.git",
    "mtrfixture==1.0.0 --hash=sha256:" + "a" * 64 + " ; python_version>'3'",
])
def test_a_lock_line_without_a_hash_is_refused(tmp_path: Path, line: str) -> None:
    """Done-when 11: each lock line is ``<name>==<version>`` with one
    ``--hash=sha256:``. No pip option passes. Mutation: loosen the line
    rule -> red."""
    from acb_skills import user_tool_build as ute

    pkg = write_package(tmp_path / "pkg", package_files(lock=line + "\n"))
    with pytest.raises(ute.PackageRefused) as exc:
        ute.check_package(pkg, {"crm:read"})
    assert exc.value.code == ute.LOCK_LINE


@pytest.mark.parametrize(("tool", "code"), [
    ({"write_scopes": ["crm:write"]}, "package_write_scope"),
    ({"read_scopes": ["projects:read"]}, "package_read_scope_not_approved"),
    ({"read_scopes": ["crm:read", "mail:read"]}, "package_read_scope_unknown"),
])
def test_a_scope_outside_the_approval_is_refused(
    tmp_path: Path, tool: dict[str, Any], code: str,
) -> None:
    """Done-when 15. A write scope, a read scope that the card did not
    approve, and a scope that the door does not know. Mutation: drop each
    check -> red."""
    from acb_skills import user_tool_build as ute

    pkg = write_package(tmp_path / "pkg", package_files(tool_json=_tool(**tool)))
    with pytest.raises(ute.PackageRefused) as exc:
        ute.check_package(pkg, {"crm:read"})
    assert exc.value.code == code


def test_each_other_rule_of_the_package_check(tmp_path: Path) -> None:
    """Decision 5, the other rules, each with its own fixed code."""
    import json
    import os

    from acb_skills import user_tool_build as ute

    def code_of(files: dict[str, bytes], tweak: Any = None) -> str | None:
        pkg = tmp_path / f"p{len(list(tmp_path.iterdir()))}"
        write_package(pkg, files)
        if tweak is not None:
            tweak(pkg)
        try:
            ute.check_package(pkg, {"crm:read"})
        except ute.PackageRefused as exc:
            return exc.code
        return None

    assert code_of(package_files()) is None
    assert code_of(package_files(lock="")) is None, "an empty lock is valid"
    assert code_of({**package_files(), "extra.py": b"x"}) == ute.EXTRA_NAME
    assert code_of(package_files(), lambda p: os.symlink("/etc/passwd", p / "x")) in (
        ute.NOT_REGULAR, ute.EXTRA_NAME)
    link_main = package_files(main_py=None)

    def swap(p: Path) -> None:
        os.symlink("/etc/hostname", p / "main.py")

    assert code_of(link_main, swap) == ute.NOT_REGULAR
    assert code_of(package_files(main_py=b"#" * (ute.PACKAGE_MAX_BYTES + 1))) == ute.TOO_LARGE
    half = b"#" * (ute.PACKAGE_MAX_BYTES // 2 + 1)
    assert code_of(package_files(main_py=half, SKILL_md=None, **{"SKILL.md": SKILL_MD.encode()
                                 + half})) == ute.TOO_LARGE
    assert code_of(package_files(tool_json=b"[1]")) == ute.TOOL_JSON
    assert code_of(package_files(tool_json=b"{")) == ute.TOOL_JSON
    no_out = json.loads(package_files()["tool.json"])
    no_out.pop("output_schema")
    assert code_of(package_files(tool_json=json.dumps(no_out))) == ute.TOOL_JSON
    assert code_of(package_files(tool_json=_tool(input_schema={"type": 5}))) == ute.TOOL_JSON
    assert code_of(package_files(smoke_json=b'{"input": {"n": "two"}}')) == ute.SMOKE_INPUT
    assert code_of(package_files(smoke_json=b'{"sample": {}}')) == ute.SMOKE_INPUT
    assert code_of(package_files(skill_md=b"no front matter")) == ute.SKILL_MD
    assert code_of(package_files(skill_md=b"---\nname: x\n---\n")) == ute.SKILL_MD
    many = "".join(f"p{i}==1.0 --hash=sha256:{GOOD_HASH}\n" for i in range(21))
    assert code_of(package_files(lock=many)) == ute.LOCK_TOO_LONG
    assert code_of(package_files(main_py=b"\xff\xfe")) == ute.NOT_TEXT
    for code in (ute.MISSING_FILE, ute.LOCK_LINE, ute.SCOPE_REFUSAL, ute.INSTALL_TOO_LARGE):
        assert re.fullmatch(r"[a-z0-9_]{1,64}", code), "an error_class is a fixed code"


# ── a fake broker and fake stores for the step engine ──────────────────────


class FakeBroker:
    """The calls of the step engine, recorded. The network of the handle is
    the host list of its live grant, or ``None``."""

    def __init__(self, tool_data: Path) -> None:
        from types import SimpleNamespace

        self.tool_data = tool_data
        self.handle = SimpleNamespace(
            binding=SimpleNamespace(tool_data=tool_data), org=ENGINE_ORG,
        )
        self.calls: list[tuple[Any, ...]] = []
        self.hosts: tuple[str, ...] | None = None
        self.over_quota = False
        self.quota_bytes = 1 << 40
        self.on_exec: Any = None

    async def acquire_tool(self) -> Any:
        self.calls.append(("acquire",))
        self.tool_data.mkdir(parents=True, exist_ok=True)
        (self.tool_data / "outputs").mkdir(exist_ok=True)
        return self.handle

    async def release_tool(self, handle: Any) -> None:
        self.calls.append(("release",))

    async def grant_egress(self, handle: Any, reason: str, hosts: Any) -> Any:
        self.calls.append(("grant", tuple(hosts)))
        self.hosts = tuple(hosts)

    async def revoke_egress(self, handle: Any) -> bool:
        self.calls.append(("revoke", self.hosts))
        had = self.hosts is not None
        self.hosts = None
        return had

    async def exec(self, handle: Any, command: str, timeout_s: float, *,
                   env_file: Any = None) -> Any:
        from orchestrator import sandbox_broker as sb

        self.calls.append(("exec", command, dict(env_file or {}), self.hosts, timeout_s))
        result = sb.ExecResult(0, "ok", 2, False, 0.1)
        if self.on_exec is not None:
            got = self.on_exec(command, env_file)
            if asyncio.iscoroutine(got):
                got = await got
            result = got or result
        await self.measure_workspace(handle)
        return result

    @contextlib.asynccontextmanager
    async def host_files(self, handle: Any) -> Any:
        yield handle

    async def measure_workspace(self, handle: Any) -> int:
        used = sum(p.stat().st_size for p in self.tool_data.rglob("*") if p.is_file())
        self.over_quota = used > self.quota_bytes
        return used

    def quota_exceeded(self, handle: Any) -> bool:
        return self.over_quota

    def execs(self) -> list[tuple[Any, ...]]:
        return [c for c in self.calls if c[0] == "exec"]


class Stores:
    """Stand-ins for ``mint``, ``revoke_step``, ``door_counts`` and ``record_run``."""

    def __init__(self) -> None:
        self.mints: list[dict[str, Any]] = []
        self.revokes: list[dict[str, Any]] = []
        self.rows: list[dict[str, Any]] = []
        self.counts = (2, 0)
        self.counts_error = False
        self.live: set[str] = set()


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """The step engine on a fake broker and fake stores, in a bound run."""
    from types import SimpleNamespace

    from acb_auth import sandbox_capabilities
    from acb_common import db as common_db
    from acb_skills import user_tool_build as ute
    from gateway.studio import user_tool_runs
    from orchestrator import sandbox_broker as sb

    from tests.unit._sandbox_broker_fakes import configure_env

    env = configure_env(monkeypatch, tmp_path)
    s = env["settings"]
    monkeypatch.setattr(s, "maf_coding_scope", "tools:*")
    monkeypatch.setattr(s, "sandbox_egress_enabled", True)
    monkeypatch.setattr(s, "sandbox_egress_door_upstream", "10.0.0.5:8080")
    stores = Stores()

    async def mint(**kw: Any) -> Any:
        stores.mints.append(kw)
        stores.live.add(DOOR_TOKEN)
        return sandbox_capabilities.Minted(token=DOOR_TOKEN, expires_at=None)  # type: ignore[arg-type]

    async def revoke_step(**kw: Any) -> int:
        stores.revokes.append(kw)
        gone = len(stores.live)
        stores.live.clear()
        return gone

    async def door_counts(org: str, token_sha256: str) -> Any:
        if stores.counts_error:
            raise ConnectionError("redis is down")
        return sandbox_capabilities.DoorCounts(calls=stores.counts[0], refusals=stores.counts[1])

    async def record_run(db: Any, **kw: Any) -> str:
        stores.rows.append(kw)
        return "row"

    @contextlib.asynccontextmanager
    async def tenant_session(org: Any = None) -> Any:
        yield SimpleNamespace(org=org)

    monkeypatch.setattr(sandbox_capabilities, "mint", mint)
    monkeypatch.setattr(sandbox_capabilities, "revoke_step", revoke_step)
    monkeypatch.setattr(sandbox_capabilities, "door_counts", door_counts)
    monkeypatch.setattr(user_tool_runs, "record_run", record_run)
    monkeypatch.setattr(common_db, "tenant_session", tenant_session)
    thread = str(uuid.uuid4())
    with bound_run(ENGINE_ORG, member=ENGINE_MEMBER, thread=thread):
        tool_data = sb.read_tool_binding().tool_data
        broker = FakeBroker(tool_data)
        monkeypatch.setattr(ute, "_broker", lambda: broker)
        yield SimpleNamespace(ute=ute, broker=broker, stores=stores, settings=s,
                              tool_data=tool_data, thread=thread)


def bound_run(*args: Any, **kw: Any) -> Any:
    from tests.unit._sandbox_broker_fakes import bound_run as _bound

    return _bound(*args, **kw)


def passing(broker: FakeBroker, pages: int = 2) -> None:
    """An exec that writes a valid ``result.json`` for the smoke command."""
    from acb_skills import user_tool_build as ute

    def run(command: str, env_file: Any) -> Any:
        if command == ute.SMOKE_COMMAND:
            (broker.tool_data / "outputs" / "result.json").write_text(
                f'{{"pages": {pages}}}', encoding="utf-8")
        elif "pip install" in command:
            (broker.tool_data / "site").mkdir(exist_ok=True)
            (broker.tool_data / "site" / "mtrfixture.py").write_text("x = 1\n", encoding="utf-8")
        return None

    broker.on_exec = run


async def _round(e: Any, files: dict[str, bytes] | None = None) -> Any:
    async with e.ute.start_build({"crm:read"}) as build:
        write_package(e.tool_data / "pkg", files or package_files())
        return await build.run_smoke()


# ── decisions 4, 6, 7 and 8 on the fake broker ─────────────────────────────


async def test_a_good_round_passes_with_a_smoke_capability(engine: Any) -> None:
    """Done-when 14b (the mint) and the happy path. ``mint`` gets
    ``phase="smoke"``, the read scopes of ``tool.json``, no write scope and
    ``ttl_s=180``. Mutation: mint in another phase, with a write scope, or a
    longer ttl -> red."""
    passing(engine.broker)
    result = await _round(engine)
    assert result.passed and result.outcome == "succeeded" and result.api_reads == 2
    (mint,) = engine.stores.mints
    assert mint["phase"] == "smoke" and mint["write_scopes"] == () and mint["ttl_s"] == 180
    assert tuple(mint["read_scopes"]) == ("crm:read",)
    assert mint["org"] == ENGINE_ORG and mint["member"] == ENGINE_MEMBER
    assert mint["thread_id"] == engine.thread
    install, smoke = engine.broker.execs()
    assert install[2] == {} and smoke[2] == {"METORITE_API_TOKEN": DOOR_TOKEN}
    assert engine.stores.live == set(), "the round revoked its capability"


async def test_a_fourth_smoke_round_is_refused(engine: Any) -> None:
    """Done-when 12 (decision 8). The fourth call raises before any command.
    Mutation: raise the cap, or count after the command -> red."""
    passing(engine.broker)
    async with engine.ute.start_build({"crm:read"}) as build:
        write_package(engine.tool_data / "pkg", package_files())
        for _ in range(3):
            await build.run_smoke()
        before = list(engine.broker.calls)
        mints = len(engine.stores.mints)
        with pytest.raises(engine.ute.SmokeRoundsExhausted):
            await build.run_smoke()
        assert engine.broker.calls == before, "no command and no grant ran"
        assert len(engine.stores.mints) == mints
    assert build.rounds == 3


async def test_the_build_budget_stops_a_round(engine: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Decision 8: the whole build has ``user_tool_step_seconds_build``. A
    round with no time left raises before any command, and the build row
    reads ``timeout``. Mutation: drop the budget check -> red."""
    assert engine.settings.user_tool_step_seconds_build == 600
    monkeypatch.setattr(engine.settings, "user_tool_step_seconds_build", 0)
    passing(engine.broker)
    with pytest.raises(engine.ute.BuildBudgetExceeded):
        async with engine.ute.start_build({"crm:read"}) as build:
            write_package(engine.tool_data / "pkg", package_files())
            await build.run_smoke()
    assert engine.broker.execs() == [] and engine.stores.mints == []
    (row,) = engine.stores.rows
    assert row["phase"] == "build" and row["outcome"] == "timeout"


async def test_the_package_check_runs_again_each_round(engine: Any) -> None:
    """Decision 7, step 1: each round checks the package again, so a change
    of the session between rounds cannot skip the check. Mutation: check
    once for the build -> red."""
    passing(engine.broker)
    async with engine.ute.start_build({"crm:read"}) as build:
        write_package(engine.tool_data / "pkg", package_files())
        first = await build.run_smoke()
        write_package(engine.tool_data / "pkg",
                      package_files(tool_json=_tool(write_scopes=["crm:write"])))
        second = await build.run_smoke()
    assert first.passed
    assert second.outcome == "refused" and second.error_class == "package_write_scope"
    assert len(engine.stores.mints) == 1, "a refused package gets no capability"


@pytest.mark.parametrize("case", ["exit", "schema", "timeout", "refusal", "counts"])
async def test_each_smoke_condition_fails_alone(engine: Any, case: str) -> None:
    """Done-when 16. Exit 1, a ``result.json`` off the schema, a timeout, one
    refusal, and a count that cannot be read: each fails the round alone.
    Mutation: drop any one condition -> red."""
    from orchestrator import sandbox_broker as sb

    ute = engine.ute
    passing(engine.broker, pages=2)
    plain = engine.broker.on_exec

    def run(command: str, env_file: Any) -> Any:
        plain(command, env_file)
        if command != ute.SMOKE_COMMAND:
            return None
        if case == "exit":
            return sb.ExecResult(1, "boom", 4, False, 0.1)
        if case == "schema":
            (engine.tool_data / "outputs" / "result.json").write_text(
                '{"pages": "two"}', encoding="utf-8")
        if case == "timeout":
            return sb.ExecResult(137, "", 0, False, 120.0, timed_out=True)
        return None

    engine.broker.on_exec = run
    if case == "refusal":
        engine.stores.counts = (2, 1)
    if case == "counts":
        engine.stores.counts_error = True
    result = await _round(engine)
    expected = {"exit": ("failed", ute.EXIT_CODE), "schema": ("failed", ute.OUTPUT_SCHEMA),
                "timeout": ("timeout", ute.SMOKE_TIMEOUT),
                "refusal": ("failed", ute.SCOPE_REFUSAL),
                "counts": ("failed", ute.COUNTS_UNREADABLE)}[case]
    assert not result.passed
    assert (result.outcome, result.error_class) == expected
    assert engine.stores.live == set(), "the round revoked its capability on a failure"


async def test_a_call_count_over_the_cap_fails_condition_3(engine: Any) -> None:
    passing(engine.broker)
    engine.stores.counts = (601, 0)
    result = await _round(engine)
    assert result.error_class == engine.ute.CALL_CAP


async def test_a_full_tool_data_fails_condition_3(engine: Any) -> None:
    """Done-when 18b on the fake broker: a smoke command that fills
    ``<tool-data>`` past the quota fails condition 3. The docker half runs
    the real broker."""
    ute = engine.ute
    passing(engine.broker)
    plain = engine.broker.on_exec
    engine.broker.quota_bytes = 64 * 1024

    def run(command: str, env_file: Any) -> Any:
        plain(command, env_file)
        if command == ute.SMOKE_COMMAND:
            (engine.tool_data / "outputs" / "fill.bin").write_bytes(b"x" * 128 * 1024)
        return None

    engine.broker.on_exec = run
    result = await _round(engine)
    assert (result.outcome, result.error_class) == ("failed", ute.QUOTA)


async def test_each_round_starts_clean(engine: Any) -> None:
    """Done-when 17. A round deletes ``site/``, ``outputs/`` and
    ``input.json`` first, and a build starts with an empty ``pkg/``.
    Mutation: skip either clean -> red."""
    ute = engine.ute
    stale = engine.tool_data / "pkg"
    write_package(stale, {"old.py": b"x"})
    seen: dict[str, Any] = {}
    passing(engine.broker)
    plain = engine.broker.on_exec

    def run(command: str, env_file: Any) -> Any:
        if "pip install" in command and "site" not in seen:
            seen["site"] = (engine.tool_data / "site").exists()
            seen["outputs"] = sorted(p.name for p in (engine.tool_data / "outputs").iterdir())
            seen["input"] = (engine.tool_data / "input.json").exists()
            seen["extra"] = sorted(
                p.name for p in engine.tool_data.iterdir()
                if p.name not in ("pkg", "outputs", "site"))
        return plain(command, env_file)

    engine.broker.on_exec = run
    async with ute.start_build({"crm:read"}) as build:
        assert list((engine.tool_data / "pkg").iterdir()) == [], "pkg/ is empty at the start"
        write_package(engine.tool_data / "pkg", package_files())
        (engine.tool_data / "site").mkdir()
        (engine.tool_data / "site" / "planted.py").write_text("x", encoding="utf-8")
        (engine.tool_data / "outputs" / "result.json").write_text('{"pages": 2}',
                                                                  encoding="utf-8")
        (engine.tool_data / "outputs" / "deep").mkdir()
        (engine.tool_data / "input.json").write_text("{}", encoding="utf-8")
        (engine.tool_data / "leaked-token.txt").write_text("msbx_old", encoding="utf-8")
        await build.run_smoke()
    assert seen == {"site": False, "outputs": [], "input": False, "extra": []}
    assert (engine.tool_data / "outputs").is_dir(), "outputs/ stays: the container mounts it"


@pytest.mark.parametrize("case", ["timeout", "large", "failed"])
async def test_the_install_and_the_thread_limits(
    engine: Any, case: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Done-when 18, the install half. An install past 180 s, a ``site/``
    over 300 MiB, and a failed pip each fail the round before the smoke
    step. The thread half is
    ``test_a_second_build_in_the_thread_gets_busy``. Mutation: drop the
    size check or the install timeout -> red."""
    from orchestrator import sandbox_broker as sb

    ute = engine.ute
    assert ute.INSTALL_TIMEOUT_S == 180 and ute.SITE_MAX_BYTES == 300 * 1024 * 1024
    passing(engine.broker)
    plain = engine.broker.on_exec
    if case == "large":
        monkeypatch.setattr(ute, "SITE_MAX_BYTES", 3)

    def run(command: str, env_file: Any) -> Any:
        plain(command, env_file)
        if "pip install" in command and case == "timeout":
            return sb.ExecResult(137, "", 0, False, 180.0, timed_out=True)
        if "pip install" in command and case == "failed":
            return sb.ExecResult(1, "no", 2, False, 1.0)
        return None

    engine.broker.on_exec = run
    result = await _round(engine)
    expected = {"timeout": ("timeout", ute.INSTALL_TIMEOUT),
                "large": ("failed", ute.INSTALL_TOO_LARGE),
                "failed": ("failed", ute.INSTALL_FAILED)}[case]
    assert (result.outcome, result.error_class) == expected
    (install,) = engine.broker.execs()
    assert install[4] <= ute.INSTALL_TIMEOUT_S
    assert engine.stores.mints == [], "no capability after a failed install"
    assert engine.broker.hosts is None, "the failed install was revoked"


@pytest.mark.parametrize("fault", ["mint", "unsafe_path"])
async def test_a_fault_in_a_round_still_writes_its_row(
    engine: Any, fault: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review P3-1 (done-when 27). A database error of ``mint``, or an unsafe
    path in the clean step, fails the round with one fixed code. The round
    still writes its ``smoke`` row, the caller gets no raw error, and no
    grant or capability stays. Mutation: let the fault escape ``run_smoke``
    -> red."""
    from acb_auth import sandbox_capabilities
    from acb_skills import safe_open
    from sqlalchemy.exc import OperationalError

    ute = engine.ute
    passing(engine.broker)
    if fault == "mint":
        async def broken_mint(**kw: Any) -> Any:
            raise OperationalError("INSERT", {}, Exception("the database is gone"))

        monkeypatch.setattr(sandbox_capabilities, "mint", broken_mint)
    async with ute.start_build({"crm:read"}) as build:
        write_package(engine.tool_data / "pkg", package_files())
        if fault == "unsafe_path":
            (engine.tool_data / "site").mkdir()

            def unsafe(*_a: Any, **_k: Any) -> bool:
                raise safe_open.UnsafePath("a link in the way")

            monkeypatch.setattr(safe_open, "remove_tree", unsafe)
        result = await build.run_smoke()
        monkeypatch.undo()
    assert (result.outcome, result.error_class) == ("failed", ute.ROUND_FAULT)
    smoke = [r for r in engine.stores.rows if r["phase"] == "smoke"]
    assert [(r["outcome"], r["error_class"]) for r in smoke] == [("failed", ute.ROUND_FAULT)]
    assert engine.broker.hosts is None and engine.stores.live == set()


@pytest.mark.parametrize("slow", ["install", "check"])
async def test_a_round_ends_by_the_build_deadline(
    engine: Any, slow: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review P3-2 (decision 8). With 0.3 s of the build left, a slow install
    or a slow package check stops at the deadline. The round reads
    ``timeout`` and no grant stays. Mutation: give the install its own 30 s
    past the budget, or leave the clean and the check with no deadline ->
    red."""
    import time as _time

    ute = engine.ute
    passing(engine.broker)
    plain = engine.broker.on_exec

    async def run(command: str, env_file: Any) -> Any:
        if "pip install" in command and slow == "install":
            await asyncio.sleep(3)
        return plain(command, env_file)

    engine.broker.on_exec = run
    if slow == "check":
        real_check = ute.check_package

        def slow_check(*a: Any, **k: Any) -> Any:
            _time.sleep(2)
            return real_check(*a, **k)

        monkeypatch.setattr(ute, "check_package", slow_check)
    async with ute.start_build({"crm:read"}) as build:
        write_package(engine.tool_data / "pkg", package_files())
        build.deadline = _time.monotonic() + 0.3
        began = _time.monotonic()
        result = await build.run_smoke()
        took = _time.monotonic() - began
    assert (result.outcome, result.error_class) == ("timeout", ute.BUILD_BUDGET)
    assert took < 1.5, f"the round ran {took:.1f} s past a budget of 0.3 s"
    assert engine.broker.hosts is None and engine.stores.mints == []
    assert engine.stores.rows[-1]["phase"] == "build"
    assert engine.stores.rows[-1]["outcome"] == "timeout"


async def test_the_install_alone_ends_by_the_build_deadline(engine: Any) -> None:
    """Review P3-2, the install step on its own: its timeout is the smaller
    of 180 s plus the grace and the time left, so it ends by the deadline
    even with no outer bound. Mutation: give it 30 s past the budget -> red."""
    import time as _time

    ute = engine.ute
    passing(engine.broker)
    plain = engine.broker.on_exec

    async def run(command: str, env_file: Any) -> Any:
        if "pip install" in command:
            await asyncio.sleep(3)
        return plain(command, env_file)

    engine.broker.on_exec = run
    async with ute.start_build({"crm:read"}) as build:
        write_package(engine.tool_data / "pkg", package_files())
        package = ute.check_package(engine.tool_data / "pkg", {"crm:read"})
        build.deadline = _time.monotonic() + 0.3
        began = _time.monotonic()
        failed = await build._install(package)
        took = _time.monotonic() - began
        await build._revoke(False)
    assert failed is not None and (failed.outcome, failed.error_class) == (
        "timeout", ute.INSTALL_TIMEOUT)
    assert took < 1.5, f"the install ran {took:.1f} s past a budget of 0.3 s"


async def test_a_cancel_mid_round_revokes_and_writes_cancelled_rows(engine: Any) -> None:
    """Decisions 3 and 4, the cancel path. A cancel while the smoke command
    runs propagates as ``CancelledError``. The rows read ``smoke/cancelled``
    and ``build/cancelled``, no capability is live, no grant is left, and
    ``release_tool`` ran. Mutation: swallow the cancel in ``run_smoke`` ->
    red."""
    ute = engine.ute
    passing(engine.broker)
    plain = engine.broker.on_exec
    running = asyncio.Event()

    async def run(command: str, env_file: Any) -> Any:
        if command == ute.SMOKE_COMMAND:
            running.set()
            await asyncio.Event().wait()  # held until the cancel
        return plain(command, env_file)

    engine.broker.on_exec = run

    async def build() -> None:
        async with ute.start_build({"crm:read"}) as b:
            write_package(engine.tool_data / "pkg", package_files())
            await b.run_smoke()

    task = asyncio.create_task(build())
    await asyncio.wait_for(running.wait(), 10)
    assert engine.stores.live == {DOOR_TOKEN}, "the capability is live during the command"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    rows = [(r["phase"], r["outcome"]) for r in engine.stores.rows]
    assert rows == [("smoke", "cancelled"), ("build", "cancelled")]
    assert engine.stores.live == set(), "a capability outlived the cancel"
    assert engine.broker.hosts is None, "a grant outlived the cancel"
    assert engine.broker.calls[-1] == ("release",)


async def test_a_second_build_in_the_thread_gets_busy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Done-when 18, the thread half, on the real broker and a fake Docker.
    Mutation: drop the lock of (org, thread) -> red."""
    from acb_auth import sandbox_capabilities
    from acb_common import db as common_db
    from acb_skills import user_tool_build as ute
    from gateway.studio import user_tool_runs
    from orchestrator import sandbox_broker as sb

    from tests.unit._sandbox_broker_fakes import FakeDocker, configure_env

    env = configure_env(monkeypatch, tmp_path)
    s = env["settings"]
    monkeypatch.setattr(s, "maf_coding_scope", "tools:*")
    monkeypatch.setattr(s, "sandbox_egress_enabled", True)
    monkeypatch.setattr(s, "sandbox_egress_door_upstream", "10.0.0.5:8080")
    broker = sb.SandboxBroker(docker=FakeDocker())  # type: ignore[arg-type]
    monkeypatch.setattr(ute, "_broker", lambda: broker)

    async def nothing(*_a: Any, **_k: Any) -> int:
        return 0

    @contextlib.asynccontextmanager
    async def session(org: Any = None) -> Any:
        yield None

    monkeypatch.setattr(sandbox_capabilities, "revoke_step", nothing)
    monkeypatch.setattr(user_tool_runs, "record_run", nothing)
    monkeypatch.setattr(common_db, "tenant_session", session)
    with bound_run(ENGINE_ORG, member=ENGINE_MEMBER, thread=str(uuid.uuid4())):
        async with ute.start_build({"crm:read"}):
            with pytest.raises(sb.SandboxBusy, match="already runs"):
                async with ute.start_build({"crm:read"}):
                    pass
        async with ute.start_build({"crm:read"}):
            pass  # the thread is free again
    await broker.stop()


async def test_each_step_writes_one_run_row_on_the_fake_store(engine: Any) -> None:
    """Decision 10 on the fake store. The R8 half is
    ``test_each_step_writes_one_run_row``."""
    passing(engine.broker)
    async with engine.ute.start_build({"crm:read"}, gap_id=str(uuid.uuid4())) as build:
        write_package(engine.tool_data / "pkg", package_files(lock=b"bad line\n"))
        await build.run_smoke()
        write_package(engine.tool_data / "pkg", package_files())
        await build.run_smoke()
    phases = [(r["phase"], r["outcome"], r["error_class"]) for r in engine.stores.rows]
    assert phases == [
        ("smoke", "refused", engine.ute.LOCK_LINE),
        ("smoke", "succeeded", None),
        ("build", "succeeded", None),
    ]
    for row in engine.stores.rows:
        assert row["member"] == ENGINE_MEMBER and row["thread_id"] == engine.thread
        assert row["backend"] == "docker-runc"
    assert engine.stores.rows[-1]["api_reads"] == 2


async def test_start_build_refuses_before_any_start(engine: Any,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Decision 1 and done-when 25, the engine half: a scope off the door,
    egress off, and an empty door upstream refuse before ``acquire_tool``."""
    from orchestrator import sandbox_broker as sb

    ute = engine.ute
    with pytest.raises(ValueError):
        async with ute.start_build({"crm:write"}):
            pass
    monkeypatch.setattr(engine.settings, "sandbox_egress_door_upstream", "")
    with pytest.raises(sb.SandboxRefused, match="upstream"):
        async with ute.start_build({"crm:read"}):
            pass
    monkeypatch.setattr(engine.settings, "sandbox_egress_enabled", False)
    with pytest.raises(sb.SandboxRefused):
        async with ute.start_build({"crm:read"}):
            pass
    assert engine.broker.calls == []


# ── R8: the rows of decision 10 on a real database ─────────────────────────

from tests.unit.test_h3_rls_promotion_rehearsal import _DB_GATE, promoted  # noqa: E402,F401
from tests.unit.test_user_tool_registry import (  # noqa: E402,F401
    ANA,
    _chat,
    _run,
    _run_rows,
    runs_world,
)


@_DB_GATE
def test_each_step_writes_one_run_row(runs_world: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
                                      tmp_path: Path) -> None:
    """Done-when 27 (decision 10), R8 as the NOBYPASSRLS role. One ``build``
    row for the build, one ``smoke`` row for each round, and ``refused`` with
    the fixed code for a failed package check. Each row is in the run's org,
    on the run's own chat, with the member of the run binding. Mutation:
    skip the row of a refused round, or of the build -> red."""
    from acb_auth import sandbox_capabilities
    from acb_skills import user_tool_build as ute
    from orchestrator import sandbox_broker as sb

    from tests.unit._sandbox_broker_fakes import configure_env

    env = configure_env(monkeypatch, tmp_path)
    s = env["settings"]
    monkeypatch.setattr(s, "maf_coding_scope", "tools:*")
    monkeypatch.setattr(s, "sandbox_egress_enabled", True)
    monkeypatch.setattr(s, "sandbox_egress_door_upstream", "10.0.0.5:8080")

    async def mint(**kw: Any) -> Any:
        return sandbox_capabilities.Minted(token=DOOR_TOKEN, expires_at=None)  # type: ignore[arg-type]

    async def nothing(**kw: Any) -> int:
        return 0

    async def counts(org: str, token_sha256: str) -> Any:
        return sandbox_capabilities.DoorCounts(calls=3, refusals=0)

    monkeypatch.setattr(sandbox_capabilities, "mint", mint)
    monkeypatch.setattr(sandbox_capabilities, "revoke_step", nothing)
    monkeypatch.setattr(sandbox_capabilities, "door_counts", counts)
    world = runs_world
    chat = _chat(world, world.a, ANA)
    gap = str(uuid.uuid4())

    async def go() -> list[Any]:
        with bound_run(world.a, member=ANA, thread=chat):
            broker = FakeBroker(sb.read_tool_binding().tool_data)
            monkeypatch.setattr(ute, "_broker", lambda: broker)
            passing(broker)
            results = []
            async with ute.start_build({"crm:read"}, gap_id=gap) as build:
                write_package(broker.tool_data / "pkg", package_files(lock=b"no hash\n"))
                results.append(await build.run_smoke())
                write_package(broker.tool_data / "pkg", package_files())
                results.append(await build.run_smoke())
            return results

    results = _run(world, go)
    assert [r.outcome for r in results] == ["refused", "succeeded"]
    rows = [r for r in _run_rows(world, ANA) if r.thread_id == chat]
    got = [(r.phase, r.outcome, r.error_class, r.api_reads) for r in rows]
    # The rows come in the order of ``started_at``: the build starts first.
    assert got == [
        ("build", "succeeded", None, 3),
        ("smoke", "refused", ute.LOCK_LINE, 0),
        ("smoke", "succeeded", None, 3),
    ], got
    for row in rows:
        assert row.organization_id == world.a and row.member_email == ANA
        assert row.backend == "docker-runc" and row.credits is None
        assert row.writes_held == row.writes_approved == row.writes_refused == 0


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 case that CI does not list skips in silence. The R8 guard names
    this file."""
    from tests.unit import _r8_lists as r8_lists

    assert "tests/unit/test_user_tool_build.py" in r8_lists.must_run()


# ── the source scan: the step engine is dark until SBX-4b2 ──────────────────


def _uses(tree: Any, names: frozenset[str]) -> bool:
    """True when the module imports the engine, or names one of *names* in
    code (a call, an attribute or a name), under any alias."""
    import ast

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").endswith("user_tool_build"):
                return True
            if any(a.name == "user_tool_build" or a.name in names for a in node.names):
                return True
        if isinstance(node, ast.Import) and any(
                a.name.endswith("user_tool_build") for a in node.names):
            return True
        if isinstance(node, ast.Attribute) and node.attr in names | {"acquire_tool"}:
            return True
        if isinstance(node, ast.Name) and node.id in names:
            return True
    return False


def _callers_of(names: frozenset[str], owners: frozenset[str],
                root: Path = REPO_ROOT) -> list[str]:
    import ast

    found = []
    for top in ("apps", "packages", "scripts", "evals", "deploy"):
        base = root / top
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            parts = set(path.parts)
            if "node_modules" in parts or ".venv" in parts or "__pycache__" in parts:
                continue
            rel = path.relative_to(root).as_posix()
            if rel in owners:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if not any(n in text for n in (*names, "user_tool_build", "acquire_tool")):
                continue
            if _uses(ast.parse(text), names):
                found.append(rel)
    return sorted(found)


ENGINE = "packages/acb_skills/acb_skills/user_tool_build.py"
_ENTRY = frozenset({"start_build"})


def test_no_production_module_calls_the_step_engine() -> None:
    """The residual of the audit (2026-11-27). In SBX-4b1 the step engine
    grants egress with no card, so no production module may import it or
    call ``start_build``, and only the engine calls ``acquire_tool``.
    SBX-4b2 lifts this scan together with its card. Mutation: import the
    engine, or call ``acquire_tool``, from any other module -> red."""
    assert _callers_of(_ENTRY, frozenset({ENGINE})) == []


def test_the_engine_scan_turns_red(tmp_path: Path) -> None:
    fake = tmp_path / "apps" / "x"
    fake.mkdir(parents=True)
    (fake / "a.py").write_text(
        "from acb_skills.user_tool_build import start_build\n", encoding="utf-8")
    (fake / "b.py").write_text(
        "from acb_skills import user_tool_build as u\n", encoding="utf-8")
    (fake / "c.py").write_text(
        "async def go(b):\n    await b.acquire_tool()\n", encoding="utf-8")
    (fake / "d.py").write_text("x = 'start_build in a string'\n", encoding="utf-8")
    owners = frozenset({ENGINE})
    assert _callers_of(_ENTRY, owners, tmp_path) == ["apps/x/a.py", "apps/x/b.py", "apps/x/c.py"]


def test_the_install_command_is_fixed() -> None:
    """Decision 6: the engine runs pip with a fixed command and index."""
    from acb_skills import user_tool_build as ute

    assert ute.install_command() == (
        "pip install --require-hashes --only-binary :all: --no-deps "
        "--index-url https://pypi.org/simple --target /workspace/.run/site "
        "-r /workspace/.run/pkg/requirements.lock"
    )
    assert ute.INSTALL_HOSTS == ("pypi.org", "files.pythonhosted.org")
    assert ute.SMOKE_COMMAND == (
        "PYTHONPATH=/workspace/.run/site PYTHONNOUSERSITE=1 exec python3 "
        "/workspace/.run/pkg/main.py"
    )


# ═══════════════════════════════════════════════════════════════════════════
# sandbox_docker: a real broker, a real proxy, a stub index and the door kit
# ═══════════════════════════════════════════════════════════════════════════

STUB_HOST = "pypi-stub.test"
WHEEL_NAME = "mtrfixture"
WHEEL_VERSION = "1.0.0"


def _docker(*args: str, timeout: float = 180) -> Any:
    import subprocess

    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace", check=False,
    )


def _wheel(folder: Path) -> tuple[str, str]:
    """A small pure wheel that the image lacks. Its file name and SHA-256."""
    import base64
    import hashlib
    import zipfile

    dist = f"{WHEEL_NAME}-{WHEEL_VERSION}.dist-info"
    members = {
        f"{WHEEL_NAME}/__init__.py": b'VALUE = "fixture"\n',
        f"{dist}/METADATA": (f"Metadata-Version: 2.1\nName: {WHEEL_NAME}\n"
                             f"Version: {WHEEL_VERSION}\n").encode(),
        f"{dist}/WHEEL": b"Wheel-Version: 1.0\nGenerator: sbx4b1\nRoot-Is-Purelib: true\n"
                         b"Tag: py3-none-any\n",
    }
    record = []
    for name, data in members.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append(f"{name},sha256={digest},{len(data)}")
    record.append(f"{dist}/RECORD,,")
    members[f"{dist}/RECORD"] = ("\n".join(record) + "\n").encode()
    name = f"{WHEEL_NAME}-{WHEEL_VERSION}-py3-none-any.whl"
    path = folder / name
    with zipfile.ZipFile(path, "w") as zf:
        for member, data in members.items():
            zf.writestr(zipfile.ZipInfo(member, (2026, 1, 1, 0, 0, 0)), data)
    return name, hashlib.sha256(path.read_bytes()).hexdigest()


def _stub_index(root: Path) -> str:
    """The simple index of one wheel in *root*. The SHA-256 of the wheel."""
    import os

    files = root / "files"
    simple = root / "simple" / WHEEL_NAME
    files.mkdir(parents=True)
    simple.mkdir(parents=True)
    name, sha = _wheel(files)
    (simple / "index.html").write_text(
        f'<html><body><a href="/files/{name}#sha256={sha}">{name}</a></body></html>\n',
        encoding="utf-8")
    for here, _dirs, names in os.walk(root):
        os.chmod(here, 0o755)
        for n in names:
            os.chmod(Path(here) / n, 0o644)
    return sha


_PROXY_OVERRIDE = '''

# ── test only (SBX-4b1 done-when 9): the stub index of the test ────────────
_STUB_NAME = "__HOST__"
_STUB_ADDRESS = "__ADDRESS__"
_real_resolve = system_resolve
_real_refusal = address_refusal


def system_resolve(host, port):
    return [_STUB_ADDRESS] if host == _STUB_NAME else _real_resolve(host, port)


def address_refusal(address):
    return None if address == _STUB_ADDRESS else _real_refusal(address)


Proxy.__init__.__kwdefaults__ = {**(Proxy.__init__.__kwdefaults__ or {}),
                                 "resolve": system_resolve}

'''


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        return int(s.getsockname()[1])


@contextlib.contextmanager
def _door_server(app: Any, port: int) -> Any:
    """The door kit on a real port of the host, in a thread."""
    import threading
    import time

    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port,
                                           log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "the door kit did not start"
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=15)


@pytest.fixture
def real_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, coding_sandbox_image: str) -> Any:  # noqa: F811
    """A real broker on the local daemon, a real proxy, a stub package index
    in a container, and the door kit of ``_sandbox_door_kit`` on a real port
    of the host. The ``sandbox_docker`` job has no database, so ``mint``,
    ``lookup``, ``revoke_step`` and ``record_run`` are stand-ins (8b)."""
    from types import SimpleNamespace

    from acb_auth import agent_tokens, sandbox_capabilities
    from acb_common import db as common_db
    from acb_skills import user_tool_build as ute
    from gateway import sandbox_batch
    from gateway.studio import user_tool_runs
    from orchestrator import sandbox_broker as sb

    from tests.unit import _sandbox_door_kit as kit
    from tests.unit._sandbox_broker_fakes import configure_env

    tag = uuid.uuid4().hex[:10]
    stub = f"sbx4b1-index-{tag}"
    monkeypatch.setattr(sb, "CONTAINER_PREFIX", f"sbx4b1-sbx-{tag}-")
    monkeypatch.setattr(sb, "EGRESS_PROXY_NAME", f"sbx4b1-proxy-{tag}")
    real_ids = sb._process_ids
    env = configure_env(monkeypatch, tmp_path)
    s = env["settings"]
    monkeypatch.setattr(sb, "_process_ids", real_ids)
    monkeypatch.setattr(s, "sandbox_image", coding_sandbox_image)
    monkeypatch.setattr(s, "maf_coding_scope", "tools:*")
    monkeypatch.setattr(s, "sandbox_egress_enabled", True)
    monkeypatch.setattr(s, "sandbox_egress_allow_hosts", STUB_HOST)
    monkeypatch.setattr(ute, "INSTALL_HOSTS", (STUB_HOST,))
    monkeypatch.setattr(ute, "INDEX_URL", f"http://{STUB_HOST}/simple")
    real_install = ute.install_command
    monkeypatch.setattr(ute, "install_command",
                        lambda: real_install() + f" --trusted-host {STUB_HOST}")
    broker = sb.SandboxBroker()
    monkeypatch.setattr(ute, "_broker", lambda: broker)

    # The door kit, with the batch route of SBX-3a, on a real port.
    door = kit.make_app(monkeypatch)
    door.app.include_router(sandbox_batch.router)
    state = kit.arm_door(monkeypatch)
    caps: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    mints: list[dict[str, Any]] = []

    async def mint(**kw: Any) -> Any:
        import secrets

        mints.append(kw)
        raw = "msbx_" + secrets.token_urlsafe(32)
        caps[raw] = kit.capability(
            token_sha256=agent_tokens.hash_token(raw), organization_id=kw["org"],
            member_email=kw["member"], run_id=kw["run_id"], thread_id=kw["thread_id"],
            phase=kw["phase"], read_scopes=tuple(kw["read_scopes"]),
            write_scopes=tuple(kw.get("write_scopes", ())))
        return sandbox_capabilities.Minted(token=raw, expires_at=None)  # type: ignore[arg-type]

    async def lookup(raw: str) -> Any:
        found = caps.get(raw)
        if found is not None:
            sandbox_capabilities._note_lookup(found)
        return found

    async def revoke_step(**kw: Any) -> int:
        gone = [k for k, c in caps.items() if c.run_id == kw["run_id"] and c.phase == kw["phase"]]
        for k in gone:
            caps.pop(k)
        return len(gone)

    async def record_run(db: Any, **kw: Any) -> str:
        rows.append(kw)
        return "row"

    @contextlib.asynccontextmanager
    async def session(org: Any = None) -> Any:
        yield None

    monkeypatch.setattr(sandbox_capabilities, "mint", mint)
    monkeypatch.setattr(sandbox_capabilities, "lookup", lookup)
    monkeypatch.setattr(sandbox_capabilities, "revoke_step", revoke_step)
    monkeypatch.setattr(user_tool_runs, "record_run", record_run)
    monkeypatch.setattr(common_db, "tenant_session", session)

    index = tmp_path / "index"
    sha = _stub_index(index)
    port = _free_port()
    try:
        started = _docker(
            "run", "-d", "--name", stub, "--network", "bridge", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "65534:65534",
            "--mount", f"type=bind,source={index},target=/srv,readonly",
            coding_sandbox_image, "python3", "-I", "-m", "http.server", "80",
            "--directory", "/srv",
        )  # fmt: skip
        assert started.returncode == 0, started.stderr
        address = _docker(
            "inspect", "-f", "{{.NetworkSettings.Networks.bridge.IPAddress}}", stub,
        ).stdout.strip()
        gateway_ip = _docker(
            "network", "inspect", "-f", "{{(index .IPAM.Config 0).Gateway}}", "bridge",
        ).stdout.strip()
        assert address and gateway_ip, "the bridge network gave no address"
        proxy = tmp_path / "proxy" / "egress_proxy.py"
        proxy.parent.mkdir()
        source = sb.egress_proxy_source().read_text(encoding="utf-8")
        main_guard = '\nif __name__ == "__main__":'
        assert source.count(main_guard) == 1
        override = _PROXY_OVERRIDE.replace("__HOST__", STUB_HOST).replace("__ADDRESS__", address)
        proxy.write_text(source.replace(main_guard, override + main_guard), encoding="utf-8")
        monkeypatch.setattr(sb, "egress_proxy_source", lambda: proxy)
        monkeypatch.setattr(s, "sandbox_egress_door_upstream", f"{gateway_ip}:{port}")
        with _door_server(door.app, port):
            yield SimpleNamespace(broker=broker, ute=ute, sha=sha, rows=rows, mints=mints,
                                  caps=caps, door=door, redis=state.redis, settings=s,
                                  proxy=sb.EGRESS_PROXY_NAME, kit=kit)
    finally:
        import asyncio as _asyncio

        with contextlib.suppress(Exception):
            _asyncio.get_event_loop().run_until_complete(broker.settle())
        _docker("rm", "-f", stub)


async def _stop(broker: Any) -> None:
    from orchestrator import sandbox_broker as sb

    await broker.settle()
    await broker.stop()
    left = _docker("ps", "-aq", "--filter", f"label={sb.LABEL_SANDBOX}=1",
                   "--filter", f"name={sb.CONTAINER_PREFIX}").stdout.split()
    _docker("rm", "-f", sb.EGRESS_PROXY_NAME, *left)
    nets = _docker("network", "ls", "-q", "--filter", f"label={sb.LABEL_EGRESS}=1",
                   "--filter", f"label={sb.LABEL_ORG}=" + "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                   ).stdout.split()
    if nets:
        _docker("network", "rm", *nets)


def _fixture_package(main: str, *, reads: list[str], lock: str = "") -> dict[str, bytes]:
    import json

    tool = {
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object", "required": ["pages", "value"],
                          "properties": {"pages": {"const": 2},
                                         "value": {"const": "fixture"}}},
        "read_scopes": reads,
        "write_scopes": [],
    }
    return {
        "SKILL.md": SKILL_MD.encode(),
        "tool.json": json.dumps(tool).encode(),
        "main.py": main.encode(),
        "requirements.lock": lock.encode(),
        "smoke.json": json.dumps({"input": {"n": 2}}).encode(),
    }


_READ_TWO_PAGES = """\
import json
import metorite_door as md
import mtrfixture

door = md.Door(timeout_s=30)
with open("/workspace/.run/input.json", encoding="utf-8") as fh:
    json.load(fh)
leads = door.get("/crm/leads")
tasks = door.get("/projects/tasks")
out = {"pages": 2, "value": mtrfixture.VALUE, "email": leads["email"], "t": tasks["email"]}
with open("/workspace/outputs/result.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh)
"""


def _proxy_lines(proxy: str) -> list[dict[str, Any]]:
    import json

    logs = _docker("logs", proxy).stdout.splitlines()
    return [json.loads(x) for x in logs if x.startswith("{") and '"decision"' in x]


async def _build(real: Any, files: dict[str, bytes], approved: set[str],
                 rounds: int = 1, between: Any = None) -> list[Any]:
    from acb_skills.write_artifact import derive_artifact_context

    from tests.unit._sandbox_broker_fakes import bound_run as _bound

    results = []
    with _bound(real.kit.ORG_A, member=real.kit.MEMBER, thread=str(uuid.uuid4())):
        derive_artifact_context(run_id=f"run-{uuid.uuid4().hex[:8]}")
        try:
            async with real.ute.start_build(approved) as build:
                for n in range(rounds):
                    current = between(n) if between is not None else files
                    write_package(build.tool_data / "pkg", current)
                    results.append(await build.run_smoke())
        finally:
            await real.broker.end_run()
    return results


@pytest.mark.sandbox_docker
async def test_a_fixture_tool_builds_and_passes_its_smoke_test(real_build: Any) -> None:
    """Done-when 8, 9 and 10. A fixed package builds through the step engine.
    Its lock pins one small wheel that the image lacks, with its hash, and
    pip installs it through the proxy from a stub index on a real port (the
    real PyPI path is WS-43g3's). The tool reads two pages through
    ``metorite_door``, the proxy and the door kit. Conditions 1 to 4 pass.
    This is the real-door proof of §12.3."""
    real = real_build
    lock = f"{WHEEL_NAME}=={WHEEL_VERSION} --hash=sha256:{real.sha}\n"
    files = _fixture_package(_READ_TWO_PAGES, reads=["crm:read", "projects:read"], lock=lock)
    try:
        (result,) = await _build(real, files, {"crm:read", "projects:read"})
        lines = _proxy_lines(real.proxy)
    finally:
        await _stop(real.broker)
    assert result.passed, (result.outcome, result.error_class, result.output[-1500:])
    assert result.api_reads == 2 and result.refusals == 0
    (mint,) = real.mints
    assert mint["phase"] == "smoke" and mint["write_scopes"] == () and mint["ttl_s"] == 180
    assert real.caps == {}, "the round revoked its capability"
    allowed = {(x["host"], x["decision"]) for x in lines}
    assert (STUB_HOST, "allow") in allowed, "pip did not reach the index through the proxy"
    assert ("metorite-api.sandbox", "allow") in allowed
    assert [r["phase"] for r in real.rows] == ["smoke", "build"]
    assert real.rows[-1]["outcome"] == "succeeded" and real.rows[0]["api_reads"] == 2


_OUTSIDE_SCOPE = """\
import json
import metorite_door as md

try:
    md.Door(timeout_s=30).get("/crm/leads")
    code = "read"
except md.DoorError as exc:
    code = exc.code
with open("/workspace/outputs/result.json", "w", encoding="utf-8") as fh:
    json.dump({"pages": 2, "value": "fixture", "code": code}, fh)
"""


@pytest.mark.sandbox_docker
async def test_a_call_outside_the_declared_scopes_fails_the_smoke_test(real_build: Any) -> None:
    """Done-when 13. A tool with ``projects:read`` only calls ``GET
    /crm/leads``, catches the ``DoorError`` and exits 0. The round fails on
    condition 4."""
    real = real_build
    files = _fixture_package(_OUTSIDE_SCOPE, reads=["projects:read"])
    try:
        (result,) = await _build(real, files, {"projects:read"})
    finally:
        await _stop(real.broker)
    assert result.exit_code == 0 and not result.passed
    assert result.error_class == real.ute.SCOPE_REFUSAL and result.refusals == 1


_WRITES = """\
import json
import os
import metorite_door as md
import requests

mode = "__MODE__"
if mode == "post":
    answer = requests.post(md.DOOR_URL + "/crm/leads", json={"lead_name": "x"}, timeout=30,
                           headers={"X-Sandbox-Capability": os.environ["METORITE_API_TOKEN"]})
    seen = [answer.status_code, answer.text[:300]]
else:
    try:
        md.Door(timeout_s=30).batch([{"method": "POST", "path": "/crm/leads",
                                      "body": {"lead_name": "x"}}])
        seen = ["ran"]
    except md.DoorError as exc:
        seen = [exc.status, exc.code]
print("SEEN " + json.dumps(seen))
with open("/workspace/outputs/result.json", "w", encoding="utf-8") as fh:
    json.dump({"pages": 2, "value": "fixture"}, fh)
"""


@pytest.mark.sandbox_docker
async def test_a_write_in_the_smoke_test_fails_it(real_build: Any) -> None:
    """Done-when 14 and 14b. A plain POST, and a POST to the batch pair
    (``SANDBOX_BATCH_PAIR``), each get 403 ``sandbox_read_only``. Each counts
    under condition 4, so the round fails. The batch pair refuses because the
    capability is of the ``smoke`` phase (``sandbox_batch._check_batch``), as
    the mint of the step engine sets it."""
    real = real_build

    def package(n: int) -> dict[str, bytes]:
        return _fixture_package(_WRITES.replace("__MODE__", ("post", "batch")[n]),
                                reads=["crm:read"])

    try:
        post, batch = await _build(real, package(0), {"crm:read"}, rounds=2, between=package)
    finally:
        await _stop(real.broker)
    for result in (post, batch):
        assert result.exit_code == 0 and not result.passed, result
        assert result.error_class == real.ute.SCOPE_REFUSAL and result.refusals == 1
    assert "SEEN [403," in post.output and "sandbox_read_only" in post.output, post.output
    assert 'SEEN [403, "sandbox_read_only"]' in batch.output, batch.output
    assert [m["phase"] for m in real.mints] == ["smoke", "smoke"]
    assert not any(r.startswith("POST") for r in real.door.ran), "no write route ran"


@pytest.mark.sandbox_docker
async def test_a_full_tool_data_fails_the_smoke_test(real_build: Any,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Done-when 18b. A smoke command that fills ``<tool-data>`` past
    ``sandbox_workspace_quota_mb`` fails condition 3, on the real broker."""
    real = real_build
    monkeypatch.setattr(real.settings, "sandbox_workspace_quota_mb", 1)
    main = (
        "import json\n"
        "open('/workspace/outputs/fill.bin', 'wb').write(b'x' * (3 * 1024 * 1024))\n"
        "json.dump({'pages': 2, 'value': 'fixture'},"
        " open('/workspace/outputs/result.json', 'w'))\n"
    )
    try:
        (result,) = await _build(real, _fixture_package(main, reads=[]), set())
    finally:
        await _stop(real.broker)
    assert result.exit_code == 0 and not result.passed
    assert (result.outcome, result.error_class) == ("failed", real.ute.QUOTA)
