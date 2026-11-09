"""Task 2464: a T2 build imports only the app's own files.

``POST /apps/{slug}/build`` runs ``build_t2.mjs`` on the HOST, and every app
dir is a sibling under one ``apps_root()``, across orgs. Before the fix,
esbuild followed a relative import anywhere on disk, so org B could write
``import d from "../../<org-A-app>/src/data.json"`` and get org A's text in
its own ``dist/bundle.html``. Each test here runs the REAL ``build_t2.mjs``
with node and a real T2 vendor cache, against two app dirs under one
temporary apps root.

What it proves (each route once in default mode, and the path routes again
in custom-deps mode):

* a direct ``../`` import, a chain through a file outside ``src/``, a CSS
  ``@import``, a CSS ``url()``, an absolute path, a ``file:`` URL, a
  ``require()`` and a dynamic ``import()`` are refused, and the error names
  the import as written;
* a dir link and a file link inside the app dir that point out are refused
  (``onLoad`` checks the real path);
* an app file outside ``src/`` cannot reach the vendor cache by path, only by
  package name (the importer rule);
* in custom-deps mode, a package ``main`` that leads out, a link in
  ``node_modules`` and a ``node_modules`` dir above the app are refused;
* a linked ``index.html``, ``src/main.tsx`` or ``dist/`` is refused, and a
  link planted at ``dist/.bundle.html.tmp`` or ``dist/bundle.html`` never
  writes into org A's file;
* a ``tsconfig.json`` ``extends`` never reads org A's file;
* ``install_t2_deps.mjs`` refuses a ``file:``, ``link:``, path, git or
  ``workspaces`` dependency and a lock entry that is a link, before npm runs;
* a legal app (own ``./`` and ``../`` files, React, ``@cc/ui``) still builds,
  in both modes, and lucide-react (3,924 files) builds inside the gateway's
  60 s build timeout;
* the host route (``files.build_app``) runs the ``build_t2.mjs`` of the
  deployed source tree, the one that the sandbox broker copies. It never
  runs the agent clone-cache copy, because the clone refreshes only when the
  agent loads. A planted clone script with other content never runs.

The vendor cache: ``T2_VENDOR_TEST_DIR`` (the name that
``test_app_builder_engine.py`` reads), then ``CUSTOM_APPS_T2_VENDOR_DIR``,
then the default dirs of the gateway and the box. With none, the build tests
SKIP and say so. ``scripts/vps_apply.sh`` holds the install command.

Mutations this suite catches (R7), each run red once by hand:

* ``plugins: []`` in place of the containment plugin: every leak test;
* the old ``importerInsideSrc`` rule (an app importer outside ``src/`` is
  not policed): the chain test (the error no longer names the import) and the
  vendor-by-path test (the build succeeds);
* ``build_app`` takes the script from the clone cache again
  (``_agent_workspace_dir("agent-app-builder")``): the two route tests.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
BUILD_DIR = REPO / "apps" / "agents" / "agent-app-builder" / "build"
BUILD = BUILD_DIR / "build_t2.mjs"
INSTALL = BUILD_DIR / "install_t2_deps.mjs"

#: Every file of org A holds this text. It must never reach org B.
MARKER = "ORG-A-PRIVATE-2464"
REFUSAL = "An app can import only its own files."
TEMPLATE = '<!doctype html><html><body><div id="root"></div><!-- CC_T2_BUNDLE --></body></html>\n'

#: The gateway's own cap on a host build (files.BUILD_TIMEOUT_SECONDS).
BUILD_TIMEOUT = 60

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs node on PATH")


def _vendor_candidates() -> list[Path]:
    named = [os.environ.get("T2_VENDOR_TEST_DIR", ""), os.environ.get("CUSTOM_APPS_T2_VENDOR_DIR", "")]
    paths = [Path(n.strip()) for n in named if n.strip()]
    paths.append(Path.home() / ".acb" / "agents" / "vendor" / "t2-react")
    paths.append(Path("/opt/acb/t2-vendor"))
    return paths


def _find_vendor() -> Path | None:
    for path in _vendor_candidates():
        modules = path / "node_modules"
        if all((modules / name).is_dir() for name in ("esbuild", "react", "react-dom")):
            return path.resolve()
    return None


@pytest.fixture(scope="module")
def vendor() -> Path:
    found = _find_vendor()
    if found is None:
        pytest.skip(
            "no T2 vendor cache: set T2_VENDOR_TEST_DIR to a dir whose node_modules holds "
            "esbuild, react and react-dom (scripts/vps_apply.sh installs one at /opt/acb/t2-vendor)"
        )
    return found


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _link(link: Path, target: Path, *, is_dir: bool = False) -> None:
    """A symlink, or a junction for a dir where Windows refuses a symlink."""
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=is_dir)
        return
    except (OSError, NotImplementedError):
        pass
    if is_dir and os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return
    pytest.skip("this box cannot make a link")


@pytest.fixture
def apps(tmp_path: Path) -> dict[str, Path]:
    """Two sibling app dirs under one apps root: org A's and org B's."""
    root = tmp_path / "custom_apps"
    org_a, org_b = root / "org-a-app", root / "org-b-app"
    _write(org_a / "app.json", '{"name": "A", "entry": "dist/bundle.html", "tier": "T2"}\n')
    _write(org_a / "index.html", TEMPLATE.replace("<body>", f"<body>{MARKER}-template"))
    _write(org_a / "src" / "main.tsx", f'console.log("{MARKER}-main");\n')
    _write(org_a / "src" / "data.json", json.dumps({"secret": f"{MARKER}-json"}) + "\n")
    _write(org_a / "src" / "secret.ts", f'export const SECRET = "{MARKER}-ts";\n')
    _write(org_a / "src" / "a.css", f'.a {{ content: "{MARKER}-css"; }}\n')
    _write(org_a / "src" / "note.txt", f"{MARKER}-txt and more words\n")
    _write(org_b / "app.json", '{"name": "B", "entry": "dist/bundle.html", "tier": "T2"}\n')
    _write(org_b / "index.html", TEMPLATE)
    (org_b / "src").mkdir(parents=True)
    return {"root": root, "a": org_a, "b": org_b}


def _build(ws: Path, vendor: Path) -> subprocess.CompletedProcess[str]:
    """Run the real build the way the gateway does: cwd is the app dir."""
    env = dict(os.environ)
    env["CUSTOM_APPS_T2_VENDOR_DIR"] = str(vendor)
    env.pop("CUSTOM_APPS_T2_UI_SOURCE", None)
    return subprocess.run(
        [NODE, str(BUILD)], cwd=str(ws), env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=BUILD_TIMEOUT * 2, check=False,
    )


def _bundle(ws: Path) -> str:
    path = ws / "dist" / "bundle.html"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _assert_refused(done: subprocess.CompletedProcess[str], ws: Path, *, names: str | None = None) -> None:
    out = done.stdout + done.stderr
    assert done.returncode != 0, f"the build passed:\n{out}"
    assert REFUSAL in out, out
    if names is not None:
        assert json.dumps(names) in out, f"the refusal does not name {names!r}:\n{out}"
    assert MARKER not in _bundle(ws)


def _custom_deps(ws: Path) -> None:
    """An app-local node_modules switches build_t2.mjs to custom-deps mode."""
    (ws / "node_modules").mkdir(exist_ok=True)


# ── the import routes, in both modes ────────────────────────────────────────


def _route_direct(a: Path, b: Path) -> str:
    spec = "../../org-a-app/src/data.json"
    _write(b / "src" / "main.tsx", f'import d from "{spec}";\nconsole.log(d);\n')
    return spec


def _route_chain(a: Path, b: Path) -> str:
    """src/ imports ../lib/a.ts, and lib/a.ts reaches out (the old src/-only rule)."""
    spec = "../../org-a-app/src/data.json"
    _write(b / "src" / "main.tsx", 'import a from "../lib/a";\nconsole.log(a);\n')
    _write(b / "lib" / "a.ts", f'import d from "{spec}";\nexport default d;\n')
    return spec


def _route_css_import(a: Path, b: Path) -> str:
    spec = "../../org-a-app/src/a.css"
    _write(b / "src" / "main.tsx", 'import "./styles.css";\nconsole.log(1);\n')
    _write(b / "src" / "styles.css", f'@import "{spec}";\n')
    return spec


def _route_css_url(a: Path, b: Path) -> str:
    spec = "../../org-a-app/src/note.txt"
    _write(b / "src" / "main.tsx", 'import "./styles.css";\nconsole.log(1);\n')
    _write(b / "src" / "styles.css", f".b {{ background: url({spec}); }}\n")
    return spec


def _route_absolute(a: Path, b: Path) -> str:
    spec = (a / "src" / "data.json").as_posix()
    _write(b / "src" / "main.tsx", f'import d from "{spec}";\nconsole.log(d);\n')
    return spec


def _route_file_url(a: Path, b: Path) -> str:
    spec = (a / "src" / "data.json").as_uri()
    _write(b / "src" / "main.tsx", f'import d from "{spec}";\nconsole.log(d);\n')
    return spec


def _route_require(a: Path, b: Path) -> str:
    spec = "../../org-a-app/src/data.json"
    _write(b / "src" / "main.tsx", f'const d = require("{spec}");\nconsole.log(d);\n')
    return spec


def _route_dynamic_import(a: Path, b: Path) -> str:
    spec = "../../org-a-app/src/secret"
    _write(b / "src" / "main.tsx", f'import("{spec}").then((m) => console.log(m));\n')
    return spec


PATH_ROUTES = {
    "direct": _route_direct,
    "chain": _route_chain,
    "css_import": _route_css_import,
    "css_url": _route_css_url,
    "absolute": _route_absolute,
    "file_url": _route_file_url,
    "require": _route_require,
    "dynamic_import": _route_dynamic_import,
}


@pytest.mark.parametrize("mode", ["default", "custom_deps"])
@pytest.mark.parametrize("route", sorted(PATH_ROUTES))
def test_an_import_of_another_apps_file_is_refused(
    apps: dict[str, Path], vendor: Path, route: str, mode: str,
) -> None:
    if mode == "custom_deps":
        _custom_deps(apps["b"])
    spec = PATH_ROUTES[route](apps["a"], apps["b"])
    _assert_refused(_build(apps["b"], vendor), apps["b"], names=spec)


@pytest.mark.skipif(os.name != "nt", reason="only Windows reads a backslash as a path separator")
@pytest.mark.parametrize("mode", ["default", "custom_deps"])
def test_a_backslash_relative_import_is_refused(apps: dict[str, Path], vendor: Path, mode: str) -> None:
    if mode == "custom_deps":
        _custom_deps(apps["b"])
    spec = "..\\..\\org-a-app\\src\\data.json"
    _write(apps["b"] / "src" / "main.tsx", f"import d from {json.dumps(spec)};\nconsole.log(d);\n")
    _assert_refused(_build(apps["b"], vendor), apps["b"], names=spec)


@pytest.mark.parametrize("mode", ["default", "custom_deps"])
def test_a_dir_link_that_points_out_is_refused(apps: dict[str, Path], vendor: Path, mode: str) -> None:
    if mode == "custom_deps":
        _custom_deps(apps["b"])
    _link(apps["b"] / "src" / "shared", apps["a"] / "src", is_dir=True)
    _write(apps["b"] / "src" / "main.tsx", 'import d from "./shared/data.json";\nconsole.log(d);\n')
    _assert_refused(_build(apps["b"], vendor), apps["b"], names="src/shared/data.json")


@pytest.mark.parametrize("mode", ["default", "custom_deps"])
def test_a_file_link_that_points_out_is_refused(apps: dict[str, Path], vendor: Path, mode: str) -> None:
    if mode == "custom_deps":
        _custom_deps(apps["b"])
    _link(apps["b"] / "src" / "data.json", apps["a"] / "src" / "data.json")
    _write(apps["b"] / "src" / "main.tsx", 'import d from "./data.json";\nconsole.log(d);\n')
    _assert_refused(_build(apps["b"], vendor), apps["b"], names="src/data.json")


def test_an_app_file_outside_src_reaches_the_vendor_cache_only_by_package_name(
    apps: dict[str, Path], vendor: Path,
) -> None:
    """The importer rule: a path import of an app file stays in the app dir.

    onLoad alone allows a vendor file. So only the importer rule refuses a
    path into the vendor cache, and it must police lib/, not only src/.
    """
    spec = (vendor / "node_modules" / "react" / "index.js").as_posix()
    _write(apps["b"] / "src" / "main.tsx", 'import r from "../lib/r";\nconsole.log(r);\n')
    _write(apps["b"] / "lib" / "r.ts", f'import r from "{spec}";\nexport default r;\n')
    _assert_refused(_build(apps["b"], vendor), apps["b"], names=spec)


# ── custom-deps mode: node_modules ──────────────────────────────────────────


def test_a_package_main_that_leads_out_is_refused(apps: dict[str, Path], vendor: Path) -> None:
    _custom_deps(apps["b"])
    _write(apps["b"] / "node_modules" / "x" / "package.json",
           json.dumps({"name": "x", "main": "../../../org-a-app/src/data.json"}))
    _write(apps["b"] / "src" / "main.tsx", 'import d from "x";\nconsole.log(d);\n')
    _assert_refused(_build(apps["b"], vendor), apps["b"])


def test_a_link_in_node_modules_is_refused(apps: dict[str, Path], vendor: Path) -> None:
    _custom_deps(apps["b"])
    _link(apps["b"] / "node_modules" / "x", apps["a"] / "src", is_dir=True)
    _write(apps["b"] / "src" / "main.tsx", 'import d from "x/data.json";\nconsole.log(d);\n')
    _assert_refused(_build(apps["b"], vendor), apps["b"], names="node_modules/x/data.json")


@pytest.mark.parametrize("mode", ["default", "custom_deps"])
def test_a_node_modules_above_the_app_dir_is_refused(
    apps: dict[str, Path], vendor: Path, mode: str,
) -> None:
    """esbuild walks node_modules up past the app dir, into the apps root."""
    if mode == "custom_deps":
        _custom_deps(apps["b"])
    # "react" is on the default allowlist, so the walk is what is tested.
    _write(apps["root"] / "node_modules" / "react" / "index.js",
           f'export const useState = "{MARKER}-walk";\n')
    _write(apps["b"] / "src" / "main.tsx", 'import { useState } from "react";\nconsole.log(useState);\n')
    _assert_refused(_build(apps["b"], vendor), apps["b"])


# ── the template, the entry and the output ──────────────────────────────────


def test_a_linked_template_is_refused(apps: dict[str, Path], vendor: Path) -> None:
    (apps["b"] / "index.html").unlink()
    _link(apps["b"] / "index.html", apps["a"] / "index.html")
    _write(apps["b"] / "src" / "main.tsx", 'console.log("b");\n')
    _assert_refused(_build(apps["b"], vendor), apps["b"])
    assert not (apps["b"] / "dist").exists()


def test_a_linked_entry_point_is_refused(apps: dict[str, Path], vendor: Path) -> None:
    _link(apps["b"] / "src" / "main.tsx", apps["a"] / "src" / "main.tsx")
    _assert_refused(_build(apps["b"], vendor), apps["b"], names="src/main.tsx")


def test_a_linked_dist_dir_is_refused(apps: dict[str, Path], vendor: Path) -> None:
    (apps["a"] / "dist").mkdir()
    _link(apps["b"] / "dist", apps["a"] / "dist", is_dir=True)
    _write(apps["b"] / "src" / "main.tsx", 'console.log("b");\n')
    done = _build(apps["b"], vendor)
    assert done.returncode != 0, done.stdout + done.stderr
    assert "dist/ is not a dir of this app" in done.stderr, done.stderr
    assert list((apps["a"] / "dist").iterdir()) == []


@pytest.mark.parametrize("planted", [".bundle.html.tmp", "bundle.html"])
def test_a_link_planted_in_dist_never_writes_into_another_app(
    apps: dict[str, Path], vendor: Path, planted: str,
) -> None:
    victim = apps["a"] / "victim.html"
    victim.write_text("ORG-A-ORIGINAL\n", encoding="utf-8")
    _link(apps["b"] / "dist" / planted, victim)
    _write(apps["b"] / "src" / "main.tsx", 'console.log("org-b-own");\n')
    done = _build(apps["b"], vendor)
    assert done.returncode == 0, done.stdout + done.stderr
    assert victim.read_text(encoding="utf-8") == "ORG-A-ORIGINAL\n"
    bundle = apps["b"] / "dist" / "bundle.html"
    assert bundle.is_file() and not bundle.is_symlink()
    assert "org-b-own" in bundle.read_text(encoding="utf-8")


@pytest.mark.parametrize("config", ["tsconfig.json", "jsconfig.json", "src/tsconfig.json"])
def test_a_tsconfig_extends_never_reads_another_apps_file(
    apps: dict[str, Path], vendor: Path, config: str,
) -> None:
    """esbuild read the "extends" file, and its parse error showed its first word."""
    target = apps["a"] / "src" / "note.txt"
    rel = os.path.relpath(target, (apps["b"] / config).parent).replace(os.sep, "/")
    _write(apps["b"] / config, json.dumps({"extends": rel}))
    _write(apps["b"] / "src" / "main.tsx", 'console.log("b");\n')
    done = _build(apps["b"], vendor)
    assert done.returncode == 0, done.stdout + done.stderr
    assert MARKER not in done.stdout + done.stderr + _bundle(apps["b"])


# ── a legal app still builds ────────────────────────────────────────────────


def test_a_legal_app_builds_with_its_own_files_react_and_cc_ui(
    apps: dict[str, Path], vendor: Path,
) -> None:
    b = apps["b"]
    _write(b / "lib" / "x.ts", 'export const LIB = "ORG-B-LIB-TEXT";\n')
    _write(b / "src" / "own.ts", 'export const OWN = "ORG-B-OWN-TEXT";\n')
    _write(b / "src" / "parts" / "label.ts", 'import { OWN } from "../own";\nexport const LABEL = OWN;\n')
    _write(b / "src" / "main.tsx", (
        'import { createRoot } from "react-dom/client";\n'
        'import { useState } from "react";\n'
        'import { Stat } from "@cc/ui";\n'
        'import { LABEL } from "./parts/label";\n'
        'import { LIB } from "../lib/x";\n'
        "function App() {\n"
        "  const [n] = useState(3);\n"
        "  return <Stat label={LABEL + LIB} value={n} />;\n"
        "}\n"
        'createRoot(document.getElementById("root")!).render(<App />);\n'
    ))
    done = _build(b, vendor)
    assert done.returncode == 0, done.stdout + done.stderr
    bundle = _bundle(b)
    assert "ORG-B-OWN-TEXT" in bundle and "ORG-B-LIB-TEXT" in bundle
    assert "CC_T2_BUNDLE" not in bundle


def test_a_custom_deps_app_builds_a_package_of_its_own_node_modules(
    apps: dict[str, Path], vendor: Path,
) -> None:
    b = apps["b"]
    _write(b / "node_modules" / "own-pkg" / "package.json", json.dumps({"name": "own-pkg", "main": "main.js"}))
    _write(b / "node_modules" / "own-pkg" / "main.js",
           'const { HELPER } = require("./helper");\nmodule.exports = HELPER;\n')
    _write(b / "node_modules" / "own-pkg" / "helper.js", 'exports.HELPER = "ORG-B-PKG-TEXT";\n')
    _write(b / "lib" / "x.ts", 'export const LIB = "ORG-B-LIB-TEXT";\n')
    _write(b / "src" / "main.tsx", (
        'import pkg from "own-pkg";\nimport { LIB } from "../lib/x";\nconsole.log(pkg, LIB);\n'
    ))
    done = _build(b, vendor)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "custom deps" in done.stdout
    bundle = _bundle(b)
    assert "ORG-B-PKG-TEXT" in bundle and "ORG-B-LIB-TEXT" in bundle


def test_a_big_vendor_package_builds_inside_the_gateway_timeout(apps: dict[str, Path], vendor: Path) -> None:
    """lucide-react re-exports 3,924 icon files. A check that calls esbuild's
    resolve() for each import took over 170 s here, so the build timed out."""
    if not (vendor / "node_modules" / "lucide-react").is_dir():
        pytest.skip("the vendor cache has no lucide-react")
    _write(apps["b"] / "src" / "main.tsx", 'import { Save } from "lucide-react";\nconsole.log(Save);\n')
    env = dict(os.environ, CUSTOM_APPS_T2_VENDOR_DIR=str(vendor))
    env.pop("CUSTOM_APPS_T2_UI_SOURCE", None)
    done = subprocess.run(
        [NODE, str(BUILD)], cwd=str(apps["b"]), env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=BUILD_TIMEOUT, check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


# ── the host route runs the source-tree script, never the clone ─────────────


@pytest.fixture
def host_route(apps: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """``files.build_app`` on org B's app, with a planted clone-cache script.

    The clone refreshes only when the agent loads, so the old lookup
    (``_agent_clone_dir``) could run a copy without the fix. The planted
    script writes a marker file if it ever runs.
    """
    pytest.importorskip("fastapi")
    from gateway.routes import workspace
    from gateway.routes.apps import files
    from orchestrator import sandbox_broker

    clone = tmp_path / "clone" / "repos" / "agent-app-builder"
    _write(clone / "build" / "build_t2.mjs", (
        'import { writeFileSync } from "node:fs";\n'
        f'writeFileSync("CLONE-SCRIPT-RAN.txt", "{MARKER}");\n'
        'process.stdout.write("build_t2: ok, the planted clone script ran\\n");\n'
    ))
    monkeypatch.setattr(workspace, "_agent_clone_dir", lambda _name: clone)
    monkeypatch.setattr(sandbox_broker, "is_sandbox_dir", lambda _path: False)

    async def edit(_slug: str, _user: Any) -> tuple[Any, Path]:
        return SimpleNamespace(id="app-b", slug="org-b-app", workspace_path=str(apps["b"])), apps["b"]

    monkeypatch.setattr(files, "_edit_workspace", edit)
    return SimpleNamespace(files=files, clone=clone, broker=sandbox_broker)


def test_the_host_route_runs_the_source_tree_script_not_the_clone(
    host_route: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Path] = []

    async def run(_ws: Path, script: Path) -> tuple[bool, str]:
        seen.append(Path(script))
        return True, "build_t2: ok"

    monkeypatch.setattr(host_route.files, "_run_build_t2", run)
    out = asyncio.run(host_route.files.build_app("org-b-app", user=None))
    assert out["built"] is True, out
    broker = host_route.broker
    source = broker.app_builder_build_source() / broker.APP_BUILDER_BUILD_SOURCES["build_t2.mjs"]
    assert seen == [source], seen
    assert seen[0].as_posix().endswith("apps/agents/agent-app-builder/build/build_t2.mjs")
    assert host_route.clone not in seen[0].parents
    assert seen[0].read_bytes() != (host_route.clone / "build" / "build_t2.mjs").read_bytes()


@pytest.mark.parametrize("where", ["no_source", "no_script"])
def test_the_host_route_refuses_when_the_source_script_is_missing(
    host_route: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str,
) -> None:
    missing = None if where == "no_source" else tmp_path / "gone" / "build_t2.mjs"
    monkeypatch.setattr(host_route.files, "_t2_build_script", lambda: missing)
    out = asyncio.run(host_route.files.build_app("org-b-app", user=None))
    assert out["built"] is False
    expected = "T2 build tooling unavailable" if where == "no_source" else "build script missing"
    assert expected in out["error"], out


def test_the_host_route_builds_with_the_source_script_and_refuses_a_leak(
    host_route: Any, apps: dict[str, Path], vendor: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real route build, with node and the vendor cache: the planted clone
    script never runs, a legal app builds, and an import of org A is refused."""
    monkeypatch.setattr(host_route.files, "t2_vendor_dir", lambda: vendor)
    b = apps["b"]
    _write(b / "src" / "main.tsx", 'console.log("org-b-own");\n')
    out = asyncio.run(host_route.files.build_app("org-b-app", user=None))
    assert out["built"] is True, out
    assert not (b / "CLONE-SCRIPT-RAN.txt").exists()
    assert "org-b-own" in _bundle(b)

    _route_direct(apps["a"], b)
    out = asyncio.run(host_route.files.build_app("org-b-app", user=None))
    assert out["built"] is False, out
    assert REFUSAL in out["error"], out
    assert not (b / "CLONE-SCRIPT-RAN.txt").exists()
    assert MARKER not in _bundle(b)


# ── install_t2_deps.mjs: no dependency from disk ────────────────────────────


def _install(ws: Path, tmp: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, npm_config_offline="true", npm_config_cache=str(tmp / "npm-cache"))
    return subprocess.run(
        [NODE, str(INSTALL), ".", "react"], cwd=str(ws), env=env, capture_output=True,
        text=True, encoding="utf-8", timeout=120, check=False,
    )


LOCAL_DEPS = {
    "file": {"package.json": {"dependencies": {"x": "file:../org-a-app/src"}}},
    "link": {"package.json": {"dependencies": {"x": "link:../org-a-app"}}},
    "path": {"package.json": {"devDependencies": {"x": "../org-a-app"}}},
    "git": {"package.json": {"optionalDependencies": {"x": "github:user/repo"}}},
    "override": {"package.json": {"overrides": {"x": "file:../org-a-app"}}},
    "workspaces": {"package.json": {"workspaces": ["../org-a-app"]}},
    "lock_link": {
        "package.json": {"dependencies": {}},
        "package-lock.json": {"lockfileVersion": 3, "packages": {
            "": {}, "node_modules/x": {"resolved": "../org-a-app", "link": True}}},
    },
}


@pytest.mark.parametrize("case", sorted(LOCAL_DEPS))
def test_install_refuses_a_dependency_from_disk(apps: dict[str, Path], tmp_path: Path, case: str) -> None:
    b = apps["b"]
    for name, body in LOCAL_DEPS[case].items():
        _write(b / name, json.dumps(body))
    done = _install(b, tmp_path)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "An app can install only registry packages" in done.stderr, done.stderr
    assert not (b / "node_modules").exists()


def test_install_lets_a_registry_version_through_to_npm(apps: dict[str, Path], tmp_path: Path) -> None:
    """A plain version passes the check. npm then runs offline and may fail,
    but not with the refusal."""
    _write(apps["b"] / "package.json", json.dumps({"dependencies": {"react": "^18.3.1", "x": "1.x"}}))
    done = _install(apps["b"], tmp_path)
    assert "An app can install only registry packages" not in done.stderr, done.stderr
