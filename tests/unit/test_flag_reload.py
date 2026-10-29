"""A per-org flag flip with no restart (gateway/flag_reload.py, 2026-10-28).

R7 fences named here:

* ``flag-reload-applies``: a changed ``*_orgs`` flag reaches ``os.environ``,
  ``get_settings()`` and a real flag reader.
* ``flag-reload-allow-list``: only ``*_orgs`` and ``*_modes`` fields reload.
  A loop switch, a security gate, ``PGPASSWORD`` and every other key wait for
  a restart, and each reload names them (review of 2026-10-29).
* ``flag-reload-no-other-key``: a restart-only key that the file gains after
  the start stays out of the settings after a reload.
* ``flag-reload-bad-value``: settings that do not validate change nothing.
* ``flag-reload-signal`` (Linux): SIGHUP to the process runs the reload.
* ``flag-reload-per-call``: no module reads a reloadable flag at import, no
  start function of the lifespan reads one, no module keeps
  ``get_settings()`` in a module name, and no new cached function reads it.
"""
from __future__ import annotations

import ast
import asyncio
import os
import re
import signal
import sys
from pathlib import Path

import pytest
from acb_common import get_settings
from acb_common.settings import Settings
from gateway import flag_reload as fr
from gateway.routes.crm import flags as crm_flags

_ROOT = Path(__file__).resolve().parents[2]
_START = {"CRM_ORGS": "acme", "CRM_ENABLED": "true", "DATABASE_URL": "postgresql://a/b"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An env file and a process environment that agree, as at a start. The
    test works in the file's folder, as the unit does (``WorkingDirectory``),
    so ``Settings`` reads this file as its ``env_file``."""
    monkeypatch.chdir(tmp_path)
    path = tmp_path / ".env"

    def write(**values: str) -> None:
        path.write_text("".join(f"{k}={v}\n" for k, v in values.items()),
                        encoding="utf-8")

    write(**_START)
    for key in ("CRM_ORGS", "CRM_ENABLED", "DATABASE_URL", "DB_POOL_SIZE",
                "PGPASSWORD", "DECIDE_FEATURE_MODES"):
        monkeypatch.delenv(key, raising=False)
    for key, value in _START.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(fr, "_state", {"installed": False, "path": path,
                                       "loaded": fr._read(path), "at": None})
    get_settings.cache_clear()
    yield write
    get_settings.cache_clear()


def test_only_per_org_and_mode_flags_reload() -> None:
    assert len(fr.RELOADABLE) >= 20
    assert all(re.fullmatch(r"[A-Z0-9_]+_(ORGS|MODES)", k) for k in fr.RELOADABLE)
    for key in ("CRM_ORGS", "TOOL_PACKS_ORGS", "DECIDE_FEATURE_MODES", "crm_orgs"):
        assert fr.reloadable(key), key
    for key in ("DATABASE_URL", "PGPASSWORD", "PGSSLMODE", "CALENDAR_REMINDERS",
                "JOB_QUEUE", "V1_ALLOW_CALLER_ENDPOINT_OVERRIDE", "DB_POOL_SIZE",
                "EMAIL_SYNC_ENABLED", "TOOL_RESULT_SPILL", "GATEWAY_INTERNAL_TOKEN"):
        assert not fr.reloadable(key), key


def test_each_reloadable_field_is_named_by_its_env_key() -> None:
    """The reload maps ``CRM_ORGS`` to the field ``crm_orgs``. An alias would
    break that map, and ``str`` keeps every value valid."""
    for key in fr.RELOADABLE:
        info = Settings.model_fields[key.lower()]
        assert info.alias is None and info.validation_alias is None, key


def test_settings_rebuild_from_their_own_dump(env) -> None:
    current = get_settings()
    assert Settings(**current.model_dump()) == current


def test_a_changed_flag_reaches_the_settings_and_its_reader(env) -> None:
    assert crm_flags.crm_orgs() == frozenset({"acme"})
    env(**{**_START, "CRM_ORGS": "acme,globex"})

    res = fr.reload_flags()

    assert (res.applied, res.restart, res.error) == (("CRM_ORGS",), (), None)
    assert os.environ["CRM_ORGS"] == "acme,globex"
    assert crm_flags.crm_orgs() == frozenset({"acme", "globex"})
    assert fr.last_reload() is not None


def test_a_removed_flag_takes_its_default_again(env) -> None:
    env(CRM_ENABLED="true", DATABASE_URL="postgresql://a/b")
    assert fr.reload_flags().applied == ("CRM_ORGS",)
    assert "CRM_ORGS" not in os.environ
    assert get_settings().crm_orgs == Settings.model_fields["crm_orgs"].default


def test_any_other_key_waits_for_a_restart(env) -> None:
    env(**{**_START, "DATABASE_URL": "postgresql://new/db", "PGPASSWORD": "x"})
    first = fr.reload_flags()
    assert (first.applied, first.restart) == ((), ("DATABASE_URL", "PGPASSWORD"))
    assert os.environ["DATABASE_URL"] == "postgresql://a/b"
    assert "PGPASSWORD" not in os.environ
    assert fr.last_reload() is None, "a reload that applied nothing moved the time"
    # They still differ, so the next reload names them again.
    assert fr.reload_flags().restart == ("DATABASE_URL", "PGPASSWORD")


def test_a_key_the_file_gains_stays_out_of_the_settings(env) -> None:
    """Review finding P2. A rebuild from the environment would read the new
    key from the env file of ``Settings``. The reload rebuilds from the
    running settings, so only the applied flag moves."""
    running_pool = get_settings().db_pool_size
    env(**{**_START, "CRM_ORGS": "globex", "DB_POOL_SIZE": str(running_pool + 3)})

    res = fr.reload_flags()

    assert (res.applied, res.restart) == (("CRM_ORGS",), ("DB_POOL_SIZE",))
    assert get_settings().crm_orgs == "globex"
    assert get_settings().db_pool_size == running_pool
    assert "DB_POOL_SIZE" not in os.environ


def test_settings_that_do_not_validate_change_nothing(env, monkeypatch) -> None:
    class _Refuses(Settings):
        def __init__(self, **_kw):  # noqa: D107
            raise ValueError("no")

    before = get_settings()
    monkeypatch.setattr(fr, "Settings", _Refuses)
    env(**{**_START, "CRM_ORGS": "globex"})

    res = fr.reload_flags()

    assert res.error == "ValueError" and res.applied == ()
    assert os.environ["CRM_ORGS"] == "acme"
    assert get_settings() is before, "a refused reload replaced the settings"


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="no SIGHUP on this platform")
def test_sighup_runs_the_reload(env) -> None:
    env(**{**_START, "CRM_ORGS": "initech"})

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        assert fr.install(loop, path=fr._state["path"])
        # install() read the file afresh: put the start state back.
        fr._state["loaded"] = dict(_START)
        try:
            os.kill(os.getpid(), signal.SIGHUP)
            for _ in range(50):
                await asyncio.sleep(0.02)
                if os.environ.get("CRM_ORGS") == "initech":
                    break
        finally:
            loop.remove_signal_handler(signal.SIGHUP)

    asyncio.run(_run())
    assert fr.installed()
    assert crm_flags.crm_orgs() == frozenset({"initech"})


def test_install_on_a_platform_with_no_sighup(monkeypatch) -> None:
    monkeypatch.delattr(signal, "SIGHUP", raising=False)
    assert fr.install(loop=object()) is False  # type: ignore[arg-type]


# ── The fences of "read per call" ───────────────────────────────────────────

#: The code that runs in the gateway process.
_SCAN = [_ROOT / "apps/services", _ROOT / "packages"]
_MAIN = _ROOT / "apps/services/gateway/gateway/main.py"
_FIELDS = {k.lower() for k in fr.RELOADABLE}

#: The cached functions that may read ``get_settings()``: the accessor itself,
#: and the sync engine, which a restart builds (``DB_`` keys need a restart).
_CACHED_ALLOWED = {
    ("packages/acb_common/acb_common/settings.py", "get_settings"),
    ("packages/acb_graph/acb_graph/db.py", "get_engine"),
}

#: The module names that may keep a settings value. Each reads only a key
#: that a reload never applies.
_CAPTURED_ALLOWED = {
    # The docs routes exist only in dev, and FastAPI mounts them at import.
    ("apps/services/gateway/gateway/main.py", "_docs_enabled"),
}


def _trees() -> dict[str, ast.Module]:
    out: dict[str, ast.Module] = {}
    for root in _SCAN:
        for p in root.rglob("*.py"):
            if "tests" in p.parts or "node_modules" in p.parts:
                continue
            try:
                out[p.relative_to(_ROOT).as_posix()] = ast.parse(p.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
    return out


def _env_key(call: ast.Call) -> str | None:
    """The constant key of ``os.environ.get(K)``, ``os.getenv(K)``."""
    f = call.func
    if isinstance(f, ast.Attribute) and f.attr in {"get", "getenv"} and call.args \
            and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return None


def _flags_read(node: ast.AST) -> set[str]:
    """The reloadable fields that *node* reads, as an attribute or an env key.
    A bare string is a name, not a read, so a list of names is no hit."""
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and n.attr in _FIELDS:
            out.add(n.attr)
        elif isinstance(n, ast.Call) and (k := _env_key(n)) and k.lower() in _FIELDS:
            out.add(k.lower())
        elif isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) \
                and isinstance(n.slice.value, str) and n.slice.value.lower() in _FIELDS:
            out.add(n.slice.value.lower())
    return out


def _calls(node: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            out.add(getattr(n.func, "id", getattr(n.func, "attr", "")))
    return out


def test_no_module_reads_a_reloadable_flag_at_import() -> None:
    hits = []
    for rel, tree in _trees().items():
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for field in _flags_read(node):
                hits.append(f"{rel}:{node.lineno} {field}")
    assert hits == [], f"a module reads a reloadable flag at import: {hits}"


def test_no_start_function_of_the_lifespan_reads_a_reloadable_flag() -> None:
    """A start function runs once. A loop that it starts or skips on a
    reloadable flag would not follow a reload. One hop deep: a helper that a
    start function calls counts too."""
    trees = _trees()
    lifespan = next(n for n in ast.walk(trees[_MAIN.relative_to(_ROOT).as_posix()])
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")
    started = _calls(lifespan)
    defs: dict[str, list[ast.AST]] = {}
    for tree in trees.values():
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.setdefault(n.name, []).append(n)
    hits = []
    for name in sorted(started & set(defs)):
        for fn in defs[name]:
            found = _flags_read(fn)
            for helper in _calls(fn) & set(defs):
                for h in defs[helper]:
                    found |= _flags_read(h)
            hits += [f"{name} {f}" for f in sorted(found)]
    assert hits == [], f"a start function reads a reloadable flag: {hits}"


def test_no_module_keeps_a_copy_of_the_settings() -> None:
    captured, cached = [], []
    for rel, tree in _trees().items():
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None \
                    and "get_settings" in _calls(node.value):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if not all((rel, getattr(t, "id", "")) in _CAPTURED_ALLOWED for t in targets):
                    captured.append(f"{rel}:{node.lineno}")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and any(getattr(d.func if isinstance(d, ast.Call) else d, "id",
                                    getattr(d.func if isinstance(d, ast.Call) else d, "attr", ""))
                            in {"lru_cache", "cache", "cached_property"}
                            for d in node.decorator_list) \
                    and "get_settings" in _calls(node) and (rel, node.name) not in _CACHED_ALLOWED:
                cached.append(f"{rel}:{node.lineno} {node.name}")
    assert captured == [], f"a module keeps get_settings() from its import: {captured}"
    assert cached == [], f"a cached function keeps a settings value: {cached}"


def test_the_fences_can_fail(tmp_path, monkeypatch) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "m.py").write_text(
        "import os\n"
        "from acb_common import get_settings\n"
        "S = get_settings()\n"
        "ORGS = os.environ.get('CRM_ORGS', '')\n"
        "NAMES = frozenset({'CRM_ORGS'})\n", encoding="utf-8")
    me = sys.modules[__name__]
    monkeypatch.setattr(me, "_SCAN", [pkg])
    monkeypatch.setattr(me, "_ROOT", tmp_path)
    with pytest.raises(AssertionError) as got:
        test_no_module_reads_a_reloadable_flag_at_import()
    assert "pkg/m.py:4 crm_orgs" in str(got.value)
    assert "pkg/m.py:5" not in str(got.value), "a list of names is not a read"
    with pytest.raises(AssertionError) as got:
        test_no_module_keeps_a_copy_of_the_settings()
    assert "pkg/m.py:3" in str(got.value)
