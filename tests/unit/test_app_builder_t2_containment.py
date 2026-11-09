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

Where each test runs (R7: the fence must run in CI, with no skip):

* A test that builds needs a T2 vendor cache, so it is ``sandbox_docker``. The
  "Sandbox Docker tests" job (``.github/workflows/sandbox-docker.yml``) runs
  it, and that job FAILS on any skip. The ``vendor`` fixture installs the
  cache with npm in the coding image (``tests/unit/_t2_vendor.py``, the same
  install as the ``t2_vendor`` fixture of ``test_app_builder_engine.py``).
  It uses production's versions (``PROD_T2_PACKAGES``, from
  ``scripts/vps_apply.sh``) and production's ``--ignore-scripts``, because the
  host route runs with them. ``--os`` and ``--cpu`` give esbuild's binary for
  the host, because the test runs node on the host, as the route does.
  ``T2_VENDOR_PROD_TEST_DIR`` names a ready cache and skips the install.
* A test that needs no cache (the install refusals, the route script, the
  missing script) stays unmarked, so the unit shards run it.

Mutations this suite catches (R7), each run red once by hand:

* ``plugins: []`` in place of the containment plugin: every leak test;
* the old ``importerInsideSrc`` rule (an app importer outside ``src/`` is
  not policed): the chain test (the error no longer names the import) and the
  vendor-by-path test (the build succeeds);
* onLoad names ``args.path`` again: the mapping test and the main and
  node_modules tests (a path leaks);
* the error printer prints a line placed outside the roots: the parse
  error test (the first word leaks);
* no glob scan in onLoad: the glob tests (the refusal text is gone);
* no dot-part rule: the dot-part tests (the refusal no longer names the
  import);
* no ``delivery_guard`` in ``build_app``: the dir lock test;
* ``build_app`` takes the script from the clone cache again
  (``_agent_workspace_dir("agent-app-builder")``): the two route tests.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.unit._t2_vendor import PROD_T2_PACKAGES, host_platform_args, install_t2_vendor
from tests.unit.test_coding_sandbox_image import coding_sandbox_image  # noqa: F401 — a fixture
from tests.unit.test_sandbox_exec_hygiene import docker_image  # noqa: F401 — a fixture

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


#: A test that runs a real build: it needs the vendor cache from Docker.
docker = pytest.mark.sandbox_docker


@pytest.fixture(scope="module")
def vendor(docker_image: str, tmp_path_factory: pytest.TempPathFactory) -> Path:  # noqa: F811
    """A T2 vendor cache of production's versions, for this host (see the docstring)."""
    given = os.environ.get("T2_VENDOR_PROD_TEST_DIR", "").strip()
    if given:
        return Path(given).resolve()
    cache = tmp_path_factory.mktemp("t2-vendor-prod").resolve()
    done = install_t2_vendor(
        docker_image, cache, PROD_T2_PACKAGES, "--ignore-scripts", *host_platform_args(),
    )
    if done.returncode != 0 or not (cache / "node_modules" / "esbuild").is_dir():
        why = f"no T2 vendor cache could be installed: {done.stderr[-400:]}"
        # In CI a missing cache is a failure: a fence that skips there proves nothing.
        if os.environ.get("CI", "").strip():
            pytest.fail(why)
        pytest.skip(why)
    return cache


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
    # Names and first words that a leak would show (task 2464 review P0, P3).
    _write(org_a / "src" / "payroll-secret-2026.json", "{}\n")
    _write(org_a / "src" / "zz-hidden-name.ts", "export {};\n")
    _write(org_a / ".env", "KEY=1\n")
    _write(org_a / "bad" / "package.json", "FIRSTWORD2464 is not JSON\n")
    _write(tmp_path / "host" / "host-secret-file.txt", "host\n")
    _write(org_b / "app.json", '{"name": "B", "entry": "dist/bundle.html", "tier": "T2"}\n')
    _write(org_b / "index.html", TEMPLATE)
    (org_b / "src").mkdir(parents=True)
    return {"root": root, "a": org_a, "b": org_b, "host": tmp_path / "host"}


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


#: Words that only org A's files and the host dir hold. No output may show one.
LEAK_TOKENS = (MARKER, "payroll-secret-2026", "zz-hidden-name", "FIRSTWORD2464", "host-secret-file")


def _path_forms(path: Path) -> set[str]:
    forms: set[str] = set()
    for p in {path, path.resolve()}:
        forms |= {str(p), p.as_posix(), json.dumps(str(p))[1:-1]}
    return forms


def _assert_no_leak(text: str, ws: Path, *, allowed: str = "") -> None:
    """*text* shows no word of org A's files, and no path of org A or the host.

    A path that org B wrote itself (*allowed*, the import spec) is no leak.
    """
    for token in LEAK_TOKENS:
        assert token not in text, f"{token!r} leaked:\n{text}"
    for leaked in (ws.parent / "org-a-app", ws.parent.parent / "host"):
        for form in _path_forms(leaked):
            if form not in allowed:
                assert form not in text, f"a path leaked ({form}):\n{text}"


def _assert_refused(done: subprocess.CompletedProcess[str], ws: Path, *, names: str | None = None) -> None:
    out = done.stdout + done.stderr
    assert done.returncode != 0, f"the build passed:\n{out}"
    assert REFUSAL in out, out
    if names is not None:
        assert f"Cannot import {json.dumps(names)}. {REFUSAL}" in out, (
            f"the refusal does not name {names!r}:\n{out}")
    _assert_no_leak(out, ws, allowed=names or "")
    _assert_no_leak(_bundle(ws), ws)


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


@docker
@pytest.mark.parametrize("mode", ["default", "custom_deps"])
@pytest.mark.parametrize("route", sorted(PATH_ROUTES))
def test_an_import_of_another_apps_file_is_refused(
    apps: dict[str, Path], vendor: Path, route: str, mode: str,
) -> None:
    if mode == "custom_deps":
        _custom_deps(apps["b"])
    spec = PATH_ROUTES[route](apps["a"], apps["b"])
    _assert_refused(_build(apps["b"], vendor), apps["b"], names=spec)


#: Glob imports (task 2464 review P0). esbuild sends each match straight to
#: onLoad, so before the fix each match printed one path of another org. The
#: first two are the reviewer's forms. ``host`` and ``etc`` glob a dir outside
#: the apps root.
GLOB_ROUTES = {
    "template": "const k = String(Math.random());\nconsole.log(require(`../../${k}/src/${k}`));\n",
    "concat": 'const k = String(Math.random());\nconsole.log(require("../../org-a-app/src/" + k));\n',
    "host": 'const k = String(Math.random());\nconsole.log(require("../../../host/" + k));\n',
    "etc": 'const k = String(Math.random());\nconsole.log(require("' + "../" * 14 + 'etc/" + k));\n',
    "cast": 'const k = String(Math.random());\nconsole.log(require(("../../org-a-app/" as string) + k));\n',
    "dynamic": "const k = String(Math.random());\nimport(`../../org-a-app/src/${k}.json`).then(console.log);\n",
}


@docker
@pytest.mark.parametrize("mode", ["default", "custom_deps"])
@pytest.mark.parametrize("route", sorted(GLOB_ROUTES))
def test_a_glob_import_is_refused_and_lists_no_file(
    apps: dict[str, Path], vendor: Path, route: str, mode: str,
) -> None:
    b = apps["b"]
    if mode == "custom_deps":
        _custom_deps(b)
    _write(b / "src" / "main.tsx", GLOB_ROUTES[route])
    done = _build(b, vendor)
    out = done.stdout + done.stderr
    assert done.returncode != 0, out
    assert "src/main.tsx builds an import path from parts" in out, out
    _assert_no_leak(out, b)
    _assert_no_leak(_bundle(b), b)
    for host_word in ("passwd", "hostname", "shadow"):
        assert host_word not in out, out


#: A bare name with a ``.`` or ``..`` part, or ``@cc/ui/<part>`` (review P3).
#: In custom-deps mode esbuild walked out of node_modules with it, and parsed
#: the package.json of the dir it reached. ``pkg_json`` reaches org A's
#: ``bad/package.json``, whose parse error showed its first word.
DOT_PART_SPECS = {
    "file": "x/../../../org-a-app/src/data.json",
    "pkg_json": "x/../../../org-a-app/bad",
    "missing": "x/../../../org-a-app/src/nope.json",
    "dot": "x/./../../../org-a-app/src/data.json",
    "cc_ui": "@cc/ui/../../../../org-a-app/src/data.json",
}


@docker
@pytest.mark.parametrize("name", sorted(DOT_PART_SPECS))
def test_a_bare_name_with_a_dot_part_is_refused(apps: dict[str, Path], vendor: Path, name: str) -> None:
    b = apps["b"]
    _custom_deps(b)
    spec = DOT_PART_SPECS[name]
    _write(b / "src" / "main.tsx", f"import d from {json.dumps(spec)};\nconsole.log(d);\n")
    _assert_refused(_build(b, vendor), b, names=spec)


@docker
def test_a_mapping_out_of_the_app_names_no_path_and_prints_once(
    apps: dict[str, Path], vendor: Path,
) -> None:
    """A package.json "browser" map leads two imports to org A's files. onLoad
    refuses each one with the fixed text, which names no path, and the two
    equal lines print once (review P0 a)."""
    b = apps["b"]
    _write(b / "package.json", json.dumps({"browser": {
        "./src/x.ts": "../org-a-app/src/payroll-secret-2026.json",
        "./src/y.ts": "../org-a-app/src/zz-hidden-name.ts",
    }}))
    _write(b / "src" / "x.ts", "export default 1;\n")
    _write(b / "src" / "y.ts", "export default 2;\n")
    _write(b / "src" / "main.tsx", 'import x from "./x"; import y from "./y"; console.log(x, y);\n')
    done = _build(b, vendor)
    out = done.stdout + done.stderr
    assert done.returncode != 0, out
    assert out.count(REFUSAL) == 1, out
    _assert_no_leak(out, b)


@docker
def test_a_parse_error_in_a_file_outside_the_app_shows_no_word_of_it(
    apps: dict[str, Path], vendor: Path,
) -> None:
    """esbuild reads the package.json of each dir above an app file. A parse
    error placed in a file outside the roots shows only the fixed text."""
    _write(apps["root"] / "package.json", "FIRSTWORD2464 is not JSON\n")
    _write(apps["b"] / "src" / "own.ts", "export const OWN = 1;\n")
    _write(apps["b"] / "src" / "main.tsx", 'import { OWN } from "./own";\nconsole.log(OWN);\n')
    done = _build(apps["b"], vendor)
    out = done.stdout + done.stderr
    assert done.returncode != 0, out
    assert REFUSAL in out, out
    assert "package.json" not in out, out
    _assert_no_leak(out, apps["b"])


@docker
@pytest.mark.parametrize("mode", ["default", "custom_deps"])
def test_a_backslash_relative_import_is_refused(apps: dict[str, Path], vendor: Path, mode: str) -> None:
    """Windows reads a backslash as a path separator, so the fence must refuse
    the import and name it. Linux reads it as part of one file name, so the
    build cannot resolve it. On both, no byte of org A reaches the bundle."""
    if mode == "custom_deps":
        _custom_deps(apps["b"])
    spec = "..\\..\\org-a-app\\src\\data.json"
    _write(apps["b"] / "src" / "main.tsx", f"import d from {json.dumps(spec)};\nconsole.log(d);\n")
    done = _build(apps["b"], vendor)
    if os.name == "nt":
        _assert_refused(done, apps["b"], names=spec)
    else:
        assert done.returncode != 0, done.stdout + done.stderr
        assert MARKER not in _bundle(apps["b"])


@docker
@pytest.mark.parametrize("mode", ["default", "custom_deps"])
def test_a_dir_link_that_points_out_is_refused(apps: dict[str, Path], vendor: Path, mode: str) -> None:
    if mode == "custom_deps":
        _custom_deps(apps["b"])
    _link(apps["b"] / "src" / "shared", apps["a"] / "src", is_dir=True)
    _write(apps["b"] / "src" / "main.tsx", 'import d from "./shared/data.json";\nconsole.log(d);\n')
    _assert_refused(_build(apps["b"], vendor), apps["b"], names="src/shared/data.json")


@docker
@pytest.mark.parametrize("mode", ["default", "custom_deps"])
def test_a_file_link_that_points_out_is_refused(apps: dict[str, Path], vendor: Path, mode: str) -> None:
    if mode == "custom_deps":
        _custom_deps(apps["b"])
    _link(apps["b"] / "src" / "data.json", apps["a"] / "src" / "data.json")
    _write(apps["b"] / "src" / "main.tsx", 'import d from "./data.json";\nconsole.log(d);\n')
    _assert_refused(_build(apps["b"], vendor), apps["b"], names="src/data.json")


@docker
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


@docker
def test_a_package_main_that_leads_out_is_refused(apps: dict[str, Path], vendor: Path) -> None:
    _custom_deps(apps["b"])
    _write(apps["b"] / "node_modules" / "x" / "package.json",
           json.dumps({"name": "x", "main": "../../../org-a-app/src/data.json"}))
    _write(apps["b"] / "src" / "main.tsx", 'import d from "x";\nconsole.log(d);\n')
    _assert_refused(_build(apps["b"], vendor), apps["b"])


@docker
def test_a_link_in_node_modules_is_refused(apps: dict[str, Path], vendor: Path) -> None:
    _custom_deps(apps["b"])
    _link(apps["b"] / "node_modules" / "x", apps["a"] / "src", is_dir=True)
    _write(apps["b"] / "src" / "main.tsx", 'import d from "x/data.json";\nconsole.log(d);\n')
    _assert_refused(_build(apps["b"], vendor), apps["b"], names="node_modules/x/data.json")


@docker
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


@docker
def test_a_linked_template_is_refused(apps: dict[str, Path], vendor: Path) -> None:
    (apps["b"] / "index.html").unlink()
    _link(apps["b"] / "index.html", apps["a"] / "index.html")
    _write(apps["b"] / "src" / "main.tsx", 'console.log("b");\n')
    _assert_refused(_build(apps["b"], vendor), apps["b"])
    assert not (apps["b"] / "dist").exists()


@docker
def test_a_linked_entry_point_is_refused(apps: dict[str, Path], vendor: Path) -> None:
    _link(apps["b"] / "src" / "main.tsx", apps["a"] / "src" / "main.tsx")
    _assert_refused(_build(apps["b"], vendor), apps["b"], names="src/main.tsx")


@docker
def test_a_linked_dist_dir_is_refused(apps: dict[str, Path], vendor: Path) -> None:
    (apps["a"] / "dist").mkdir()
    _link(apps["b"] / "dist", apps["a"] / "dist", is_dir=True)
    _write(apps["b"] / "src" / "main.tsx", 'console.log("b");\n')
    done = _build(apps["b"], vendor)
    assert done.returncode != 0, done.stdout + done.stderr
    assert "dist/ is not a dir of this app" in done.stderr, done.stderr
    assert list((apps["a"] / "dist").iterdir()) == []


@docker
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


@docker
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


@docker
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


@docker
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


@docker
def test_a_big_vendor_package_builds_inside_the_gateway_timeout(apps: dict[str, Path], vendor: Path) -> None:
    """lucide-react re-exports 3,924 icon files. A check that calls esbuild's
    resolve() for each import took over 170 s here, so the build timed out."""
    # PROD_T2_PACKAGES holds lucide-react, so a missing one is a broken cache.
    assert (vendor / "node_modules" / "lucide-react").is_dir(), "the vendor cache has no lucide-react"
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


def test_the_host_route_checks_and_builds_under_the_dir_lock(
    host_route: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review P2: the refusal check and the build run inside delivery_guard,
    so no container exec can put a link at dist/ between a check and a write."""
    events: list[str] = []

    @contextlib.asynccontextmanager
    async def guard(_workspace: Path) -> Any:
        events.append("lock")
        try:
            yield False
        finally:
            events.append("unlock")

    def refusal(_workspace: Path) -> None:
        events.append("check")

    async def run(_ws: Path, _script: Path) -> tuple[bool, str]:
        events.append("build")
        return True, "build_t2: ok"

    monkeypatch.setattr(host_route.broker, "delivery_guard", guard)
    monkeypatch.setattr(host_route.files, "host_build_refusal", refusal)
    monkeypatch.setattr(host_route.files, "_run_build_t2", run)
    out = asyncio.run(host_route.files.build_app("org-b-app", user=None))
    assert out["built"] is True, out
    assert events == ["lock", "check", "build", "unlock"], events


def test_a_build_past_the_timeout_is_killed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review P2: at the timeout the route kills node. Before, wait_for gave up
    on the wait only, and node ran on."""
    pytest.importorskip("fastapi")
    from gateway.routes.apps import files

    script = tmp_path / "slow.mjs"
    script.write_text("setInterval(() => {}, 1000);\n", encoding="utf-8")
    procs: list[Any] = []
    real_exec = asyncio.create_subprocess_exec

    async def spy(*args: Any, **kwargs: Any) -> Any:
        proc = await real_exec(*args, **kwargs)
        procs.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    monkeypatch.setattr(files, "BUILD_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(files, "t2_vendor_dir", lambda: tmp_path / "vendor")

    async def go() -> tuple[bool, str, Any]:
        ok, output = await files._run_build_t2(tmp_path, script)
        return ok, output, procs[0].returncode

    try:
        ok, output, code = asyncio.run(go())
    finally:
        import signal

        for proc in procs:  # no process outlives the test, even when it fails
            with contextlib.suppress(OSError):
                os.kill(proc.pid, signal.SIGTERM)
    assert ok is False and "was stopped" in output, output
    assert code is not None, "node still ran after the timeout"


@docker
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


#: Loaded before install_t2_deps.mjs (``node --import``). It puts a fake
#: ``spawnSync`` in place, which writes the call it got to NPM_CALL_LOG and
#: runs nothing. So the test sees the exact npm call, or that none was made.
NPM_STUB = """import cp from "node:child_process";
import { syncBuiltinESMExports } from "node:module";
import { writeFileSync } from "node:fs";
cp.spawnSync = (cmd, args, opts) => {
  writeFileSync(process.env.NPM_CALL_LOG, JSON.stringify({ cmd, args, cwd: opts && opts.cwd }));
  return { status: 0, stdout: "", stderr: "" };
};
syncBuiltinESMExports();
"""


def _install(ws: Path, tmp: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, Any] | None]:
    """Run install_t2_deps.mjs with npm stubbed. Returns the run and the npm call."""
    stub = tmp / "npm_stub.mjs"
    stub.write_text(NPM_STUB, encoding="utf-8")
    log = tmp / "npm_call.json"
    env = dict(os.environ, NPM_CALL_LOG=str(log))
    done = subprocess.run(
        [NODE, "--import", stub.as_uri(), str(INSTALL), ".", "react"], cwd=str(ws), env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
    )
    call = json.loads(log.read_text(encoding="utf-8")) if log.is_file() else None
    return done, call


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
    done, call = _install(b, tmp_path)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "An app can install only registry packages" in done.stderr, done.stderr
    assert call is None, f"npm ran: {call}"
    assert not (b / "node_modules").exists()


def test_install_lets_a_registry_version_through_to_npm(apps: dict[str, Path], tmp_path: Path) -> None:
    """A plain version passes the check, and npm gets exactly this call."""
    _write(apps["b"] / "package.json", json.dumps({"dependencies": {"react": "^18.3.1", "x": "1.x"}}))
    done, call = _install(apps["b"], tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert call is not None, done.stdout + done.stderr
    assert call["cmd"] == "npm"
    assert call["args"] == ["install", "--ignore-scripts", "--no-audit", "--no-fund", "--save-exact", "react"]
    assert Path(call["cwd"]).resolve() == apps["b"].resolve()
