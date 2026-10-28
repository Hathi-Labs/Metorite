"""A flag flip with no restart (gateway/flag_reload.py, 2026-10-28).

R7 fences named here:

* ``flag-reload-applies``: a changed flag in the env file reaches
  ``os.environ``, ``get_settings()`` and a real flag reader.
* ``flag-reload-keeps-secrets``: a key with a secret or start-up name is not
  applied, and each reload names it as one that needs a restart.
* ``flag-reload-bad-value``: a value that ``Settings`` refuses changes
  nothing, and the running settings stay.
* ``flag-reload-signal`` (Linux): SIGHUP to the process runs the reload.
* ``flag-reload-no-import-capture``: no module of the gateway or the shared
  packages keeps ``get_settings()`` in a module name, and no cached function
  but the two known ones reads it. A reload could not reach such a copy.
"""
from __future__ import annotations

import ast
import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest
from acb_common import get_settings
from gateway import flag_reload as fr
from gateway.routes.crm import flags as crm_flags

_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An env file and a process environment that agree, as at a start."""
    path = tmp_path / ".env"

    def write(**values: str) -> None:
        path.write_text("".join(f"{k}={v}\n" for k, v in values.items()),
                        encoding="utf-8")

    write(CRM_ORGS="acme", CRM_ENABLED="true", DATABASE_URL="postgresql://a/b")
    for key in ("CRM_ORGS", "CRM_ENABLED", "DATABASE_URL", "TOOL_RESULT_SPILL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CRM_ORGS", "acme")
    monkeypatch.setenv("CRM_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://a/b")
    monkeypatch.setattr(fr, "_state", {"installed": False, "path": path,
                                       "loaded": fr._read(path), "at": None})
    get_settings.cache_clear()
    yield write
    get_settings.cache_clear()


def test_which_keys_a_reload_may_apply() -> None:
    for key in ("CRM_ORGS", "TOOL_RESULT_SPILL", "AGENT_PERMISSION_MODE",
                "WEB_PUSH_ENABLED", "DECIDE_FEATURE_MODES", "TOOL_PACKS"):
        assert fr.reloadable(key), key
    for key in ("DATABASE_URL", "GATEWAY_INTERNAL_TOKEN", "LITELLM_MASTER_KEY",
                "OPENAI_API_KEY", "DB_POOL_SIZE", "REDIS_URL", "GATEWAY_PORT",
                "EMAIL_SYNC_ENABLED", "WORKFLOW_SCHEDULER_ENABLED", "LOG_LEVEL",
                "NEXT_PUBLIC_API_URL", "VAPID_PRIVATE", "GOOGLE_CLIENT_ID"):
        assert not fr.reloadable(key), key


def test_a_changed_flag_reaches_the_settings_and_its_reader(env) -> None:
    assert crm_flags.crm_orgs() == frozenset({"acme"})
    env(CRM_ORGS="acme,globex", CRM_ENABLED="true", DATABASE_URL="postgresql://a/b")

    res = fr.reload_flags()

    assert (res.applied, res.restart, res.error) == (("CRM_ORGS",), (), None)
    assert os.environ["CRM_ORGS"] == "acme,globex"
    assert crm_flags.crm_orgs() == frozenset({"acme", "globex"})
    assert fr.last_reload() is not None


def test_a_removed_flag_takes_its_default_again(env) -> None:
    env(CRM_ENABLED="true", DATABASE_URL="postgresql://a/b")
    assert fr.reload_flags().applied == ("CRM_ORGS",)
    assert "CRM_ORGS" not in os.environ
    assert get_settings().crm_orgs == type(get_settings()).model_fields["crm_orgs"].default


def test_a_secret_or_startup_key_waits_for_a_restart(env) -> None:
    env(CRM_ORGS="acme", CRM_ENABLED="true", DATABASE_URL="postgresql://new/db")
    first = fr.reload_flags()
    assert (first.applied, first.restart) == ((), ("DATABASE_URL",))
    assert os.environ["DATABASE_URL"] == "postgresql://a/b"
    # It still differs, so the next reload names it again.
    assert fr.reload_flags().restart == ("DATABASE_URL",)


def test_a_bad_value_changes_nothing(env) -> None:
    before = get_settings()
    env(CRM_ORGS="globex", CRM_ENABLED="maybe", DATABASE_URL="postgresql://a/b")

    res = fr.reload_flags()

    assert res.error and res.applied == ()
    assert os.environ["CRM_ORGS"] == "acme" and os.environ["CRM_ENABLED"] == "true"
    assert get_settings() is before, "a refused reload cleared the settings"
    # Once the value is fixed, the reload applies both keys.
    env(CRM_ORGS="globex", CRM_ENABLED="true", DATABASE_URL="postgresql://a/b")
    assert fr.reload_flags().applied == ("CRM_ORGS",)


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="no SIGHUP on this platform")
def test_sighup_runs_the_reload(env) -> None:
    env(CRM_ORGS="initech", CRM_ENABLED="true", DATABASE_URL="postgresql://a/b")

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        assert fr.install(loop, path=fr._state["path"])
        # install() read the file afresh: put the start state back.
        fr._state["loaded"] = {"CRM_ORGS": "acme", "CRM_ENABLED": "true",
                               "DATABASE_URL": "postgresql://a/b"}
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


# ── The fence of "why this is enough" ───────────────────────────────────────

_SCAN = [_ROOT / "apps/services/gateway/gateway", _ROOT / "packages"]

#: The cached functions that may read ``get_settings()``: the accessor itself,
#: and the sync engine, which a restart builds (``DB_`` keys need a restart).
_CACHED_ALLOWED = {
    ("packages/acb_common/acb_common/settings.py", "get_settings"),
    ("packages/acb_graph/acb_graph/db.py", "get_engine"),
}

#: The module names that may keep a settings value. Each reads only a key of
#: ``flag_reload.RESTART_ONLY``.
_CAPTURED_ALLOWED = {
    # The docs routes exist only in dev, and FastAPI mounts them at import.
    # ACB_ENV is restart-only.
    ("apps/services/gateway/gateway/main.py", "_docs_enabled"),
}


def _py_files() -> list[Path]:
    out: list[Path] = []
    for root in _SCAN:
        out += [p for p in root.rglob("*.py")
                if "tests" not in p.parts and "node_modules" not in p.parts]
    return out


def _calls_get_settings(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", ""))
               == "get_settings" for n in ast.walk(node))


def _cached(fn: ast.AST) -> bool:
    for dec in getattr(fn, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = getattr(target, "id", getattr(target, "attr", ""))
        if name in {"lru_cache", "cache", "cached_property"}:
            return True
    return False


def test_no_module_keeps_a_copy_of_the_settings() -> None:
    captured, cached = [], []
    for path in _py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(_ROOT).as_posix()
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None \
                    and _calls_get_settings(node.value):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = {getattr(t, "id", "") for t in targets}
                if not all((rel, n) in _CAPTURED_ALLOWED for n in names):
                    captured.append(f"{rel}:{node.lineno}")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _cached(node) \
                    and _calls_get_settings(node) and (rel, node.name) not in _CACHED_ALLOWED:
                cached.append(f"{rel}:{node.lineno} {node.name}")
    assert captured == [], f"a module keeps get_settings() from its import: {captured}"
    assert cached == [], f"a cached function keeps a settings value: {cached}"


def test_the_fence_can_fail(tmp_path, monkeypatch) -> None:
    bad = tmp_path / "pkg"
    bad.mkdir()
    (bad / "m.py").write_text(
        "from acb_common import get_settings\n"
        "S = get_settings()\n"
        "from functools import lru_cache\n"
        "@lru_cache\n"
        "def f():\n    return get_settings().crm_orgs\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_SCAN", [bad])
    monkeypatch.setattr(sys.modules[__name__], "_ROOT", tmp_path)
    with pytest.raises(AssertionError) as got:
        test_no_module_keeps_a_copy_of_the_settings()
    assert "pkg/m.py:2" in str(got.value)
