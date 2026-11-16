"""No new direct call to an AI vendor from the web app, and the memory flags off.

2449 S1 (task 3552), D90 clause 3. Owner rule, 2026-11-08, restated
2026-11-16: every AI call goes through the operator console.

``test_no_direct_ai_vendor_calls.py`` reads only Python under ``apps/`` and
``packages/``. This file closes the two gaps that a sweep on 2026-11-16 found.

* The TypeScript of ``workbench/``. A browser can open a socket to a vendor
  with a key that the app minted. ``notes/lib/live.ts`` does this for live
  captions (Deepgram and AssemblyAI).
* ``deploy/hostinger/deploy.sh``. Mem0 and Graphiti call an embeddings vendor
  with their own key, so the deploy forces both flags to false.

What the TypeScript scan reads
------------------------------
Each ``.ts``, ``.tsx``, ``.js`` and ``.mjs`` file under ``workbench/``, with
no ``node_modules``, build output, test or ``e2e`` file, and no file of the
operator console UI (``workbench/operator_console/``, the staff app of the operator plane).
It strips comments first, so a comment never counts. It counts:

* an import of a vendor AI package, through ``import``, ``require`` or a
  dynamic ``import()``, with the packages of ``_VENDOR_PACKAGES``,
* a string that holds the host of a vendor AI API. The hosts are the list of
  the Python fence (``_VENDOR_HOSTS``), so there is one list.

The ratchet
-----------
``_BASELINE`` names each file that held a finding on 2026-11-16, the count of
each, and the 2449 slice that removes it. A file off the list fails. A count
above the baseline fails. A count below the baseline fails too, so the PR that
removes a call lowers the baseline in the same PR.

What the scan cannot see (advisory)
-----------------------------------
A host built from parts at run time, and a vendor that a package reaches inside
its own code.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest

from tests.unit.test_no_direct_ai_vendor_calls import _VENDOR_HOSTS

_REPO = Path(__file__).resolve().parents[2]
_WEB = _REPO / "workbench"
_DEPLOY = _REPO / "deploy" / "hostinger" / "deploy.sh"

_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".mjs"})
_SKIP_PARTS = frozenset({
    "node_modules", ".next", "dist", "build", "out", "coverage", "e2e",
    "__pycache__", ".git",
})
#: The operator console UI. It is the staff-only app of the operator plane (CP-8
#: README), and that plane holds the vendor keys. Its only vendor hosts are the
#: key-guide text of ``src/lib/providerGuides.ts``. The Python fence exempts
#: ``apps/services/customer_console/``, the Router, for the same plane. The
#: exemption also hides a real call added there, so keep that app free of one.
_EXEMPT_PREFIXES = ("workbench/operator_console/",)
_TEST_NAME = re.compile(r"\.(test|spec)\.[cm]?[jt]sx?$|\.d\.ts$")

#: The npm packages of AI vendors. A name with a trailing slash is a scope.
_VENDOR_PACKAGES = (
    "openai", "@anthropic-ai/sdk", "@ai-sdk/", "ai", "@google/genai",
    "@google/generative-ai", "@deepgram/sdk", "deepgram", "assemblyai",
    "groq-sdk", "@mistralai/mistralai", "cohere-ai", "elevenlabs",
    "@elevenlabs/", "replicate", "together-ai", "@github/copilot",
    "@github/copilot-sdk", "litellm",
)

_IMPORT = re.compile(
    r"""(?:import\s+(?:[^'";]*?\s+from\s+)?|require\s*\(\s*|import\s*\(\s*)"""
    r"""['"]([^'"]+)['"]"""
)
_STRING = re.compile(r"""(['"`])((?:\\.|(?!\1).)*)\1""", re.DOTALL)


@dataclass(frozen=True)
class _Entry:
    findings: dict[str, int]
    reason: str


#: Each file with a finding on 2026-11-16. Lower a count, or delete the entry,
#: in the PR that removes a call. NEVER add an entry or raise a count.
_BASELINE: dict[str, _Entry] = {
    "workbench/control_plane/src/app/notes/lib/live.ts": _Entry(
        {"host api.deepgram.com": 1, "host assemblyai.com": 1},
        "Live captions: the browser opens a socket to Deepgram or AssemblyAI "
        "with a token that the gateway minted from its own key. "
        "A console streaming-token door replaces it [2449 S6]",
    ),
    "workbench/control_plane/src/lib/providers.ts": _Entry(
        {"host openrouter.ai": 3, "host api.together.ai": 2},
        "The setup and docs links of the tenant Models page. They are links in "
        "text, not calls. The page and its key store go in [2449 S7]",
    ),
}


def _strip_comments(src: str) -> str:
    """Remove ``//`` and ``/* */`` comments. A string keeps its text, so the
    ``//`` of ``https://`` stays. A template literal counts as one string."""
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        two = src[i:i + 2]
        if c in "'\"`":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
        elif two == "//":
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif two == "/*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _is_vendor_package(spec: str) -> str | None:
    if spec.startswith((".", "/", "node:")):
        return None
    for name in _VENDOR_PACKAGES:
        if name.endswith("/"):
            if spec.startswith(name):
                return name.rstrip("/")
        elif spec == name or spec.startswith(name + "/"):
            return name
    return None


def _web_findings(source: str) -> Counter[str]:
    """Each vendor package import and each vendor host in *source*."""
    text = _strip_comments(source)
    hits: list[str] = []
    for m in _IMPORT.finditer(text):
        name = _is_vendor_package(m.group(1))
        if name:
            hits.append(f"sdk {name}")
    for m in _STRING.finditer(text):
        body = m.group(2)
        for host in _VENDOR_HOSTS:
            if host in body:
                hits.append(f"host {host}")
    return Counter(hits)


def _web_files() -> list[Path]:
    out: list[Path] = []
    for path in _WEB.rglob("*"):
        if path.suffix not in _SUFFIXES or not path.is_file():
            continue
        rel = path.relative_to(_REPO).parts
        if _SKIP_PARTS.intersection(rel) or _TEST_NAME.search(path.name):
            continue
        if path.relative_to(_REPO).as_posix().startswith(_EXEMPT_PREFIXES):
            continue
        out.append(path)
    return sorted(out)


@cache
def _scan() -> dict[str, Counter[str]]:
    found: dict[str, Counter[str]] = {}
    for path in _web_files():
        counts = _web_findings(path.read_text(encoding="utf-8-sig", errors="replace"))
        if counts:
            found[path.relative_to(_REPO).as_posix()] = counts
    return found


# ── The fence on the tree ─────────────────────────────────────────────────


def test_the_scan_reads_the_web_app() -> None:
    files = {p.relative_to(_REPO).as_posix() for p in _web_files()}
    assert len(files) > 500, "the scan reads far fewer files than the web app holds"
    assert any(f.startswith("workbench/control_plane/src/app/") for f in files)
    for path in _BASELINE:
        assert path in files, f"{path} is on the baseline, but the scan does not read it"
    assert not any("node_modules" in f for f in files)
    assert not any(f.startswith(_EXEMPT_PREFIXES) for f in files)
    assert (_REPO / _EXEMPT_PREFIXES[0]).is_dir(), "the operator console moved, so the exemption is stale"


def test_no_direct_vendor_call_off_the_baseline() -> None:
    new = {p: c for p, c in _scan().items() if p not in _BASELINE}
    assert not new, (
        "D90 clause 3: every AI call goes through the operator console. These web "
        "files import an AI vendor package or name a vendor host:\n  "
        + "\n  ".join(f"{p}: {dict(c)}" for p, c in sorted(new.items()))
        + "\n\nCall the gateway, which calls the console. Do not add the file to "
        "_BASELINE."
    )


def test_no_count_rises_above_the_baseline() -> None:
    over: list[str] = []
    for path, entry in sorted(_BASELINE.items()):
        for label, count in sorted(_scan().get(path, Counter()).items()):
            allowed = entry.findings.get(label, 0)
            if count > allowed:
                over.append(f"{path}: {label} = {count}, baseline {allowed}")
    assert not over, "A grandfathered web file gained a vendor call:\n  " + "\n  ".join(over)


def test_the_baseline_only_goes_down() -> None:
    stale: list[str] = []
    for path, entry in sorted(_BASELINE.items()):
        for label, allowed in sorted(entry.findings.items()):
            count = _scan().get(path, Counter()).get(label, 0)
            if count < allowed:
                stale.append(f"{path}: {label} = {count}, baseline {allowed}")
    assert not stale, (
        "These baseline counts are higher than the tree. Lower them, or delete the "
        "entry, in this PR:\n  " + "\n  ".join(stale)
    )


def test_every_entry_names_its_slice() -> None:
    for path, entry in _BASELINE.items():
        assert re.search(r"\[2449 S\d+\]", entry.reason), f"{path}: no 2449 slice in the reason"


# ── The finder ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "label"),
    [
        ("import OpenAI from 'openai';\n", "sdk openai"),
        ("import { Anthropic } from \"@anthropic-ai/sdk\";\n", "sdk @anthropic-ai/sdk"),
        ("import { streamText } from 'ai';\n", "sdk ai"),
        ("import { openai } from '@ai-sdk/openai';\n", "sdk @ai-sdk"),
        ("import { GoogleGenAI } from '@google/genai';\n", "sdk @google/genai"),
        ("const d = require('deepgram');\n", "sdk deepgram"),
        ("const m = await import('assemblyai');\n", "sdk assemblyai"),
        ("import 'openai/shims/web';\n", "sdk openai"),
        ("import { createClient } from '@deepgram/sdk';\n", "sdk @deepgram/sdk"),
        ("const WS = \"wss://api.deepgram.com/v1/listen\";\n", "host api.deepgram.com"),
        ("const u = `https://api.openai.com/v1/${path}`;\n", "host api.openai.com"),
        ("fetch('https://api.anthropic.com/v1/messages');\n", "host api.anthropic.com"),
        ("const R = 'https://api.githubcopilot.com/x';\n", "host githubcopilot.com"),
        ("const J = 'https://r.jina.ai/' + url;\n", "host r.jina.ai"),
    ],
)
def test_the_finder_catches_a_vendor_use(source: str, label: str) -> None:
    """Mutation: take the name out of ``_VENDOR_PACKAGES`` or of the host list
    of the Python fence, and its row goes red."""
    assert label in _web_findings(source), f"missed {label!r} in {source!r}"


@pytest.mark.parametrize(
    "source",
    [
        "// import OpenAI from 'openai';\n",
        "/* const WS = 'wss://api.deepgram.com/v1/listen'; */\n",
        "const x = 1; // https://api.openai.com in a comment\n",
        "import { api } from './openai';\nimport x from '../ai/client';\n",
        "import React from 'react';\nimport { z } from 'zod';\n",
        "import { airtable } from 'airtable-sdk';\nimport a from 'aiohttp-shim';\n",
        "const u = 'https://gmail.googleapis.com/gmail/v1';\n",
        "import fs from 'node:fs';\n",
        "const label = 'openai';\nconst slug = 'ai';\n",
        "import { x } from '@ai-sdk';\n",
    ],
)
def test_the_finder_ignores_text_and_own_modules(source: str) -> None:
    assert not _web_findings(source), f"flagged a non-call: {source!r}"


def test_a_url_in_a_string_keeps_its_double_slash() -> None:
    src = "const a = 'https://api.openai.com/v1'; // trailing comment\n"
    assert _web_findings(src) == Counter({"host api.openai.com": 1})


def test_a_new_web_file_is_off_the_baseline() -> None:
    """A planted web file with a vendor import has a finding, and no baseline
    row. Mutation: let ``_web_findings`` skip an import -> no finding -> red."""
    assert _web_findings("import OpenAI from 'openai';\n") == Counter({"sdk openai": 1})
    assert "workbench/control_plane/src/x.ts" not in _BASELINE


# ── The memory flags of the deploy ────────────────────────────────────────


def _memory_block() -> str:
    text = _DEPLOY.read_text(encoding="utf-8")
    start = text.index("Memory-layer")
    end = text.index('say "Ensuring public-URL env vars"')
    return text[start:end]


def test_the_deploy_forces_the_memory_flags_off() -> None:
    """The block names both flags and rewrites a line that exists.
    Mutation: restore the old ``if ! grep`` guard -> no ``sed`` line -> red."""
    block = _memory_block()
    assert "for _var in MEM0_ENABLED GRAPHITI_ENABLED; do" in block
    assert 'sed -i "s/^${_var}=.*/${_var}=false/" "$ENV_FILE"' in block
    assert 'echo "${_var}=false" >> "$ENV_FILE"' in block


def test_the_deploy_does_not_wait_for_a_missing_line() -> None:
    """The old guard ran for a missing line only, so a line with another value
    stayed. No guard of that kind may come back, and no value but false may be
    written for either flag."""
    block = _memory_block()
    assert 'if ! grep -qE "^${_var}="' not in block
    assert not re.search(r"_ENABLED=(?!false)", block.replace("${_var}=", ""))


def test_the_deploy_script_parses_in_bash() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("needs bash")
    done = subprocess.run([bash, "-n", str(_DEPLOY)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_forced_block_overwrites_any_value(tmp_path: Path) -> None:
    """Run the loop on a copy of an env file. A line with any other value is
    rewritten, a missing line is added, and another key stays.
    Mutation: put the old guard back -> the value stays -> red."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("needs bash")
    env_file = tmp_path / ".env"
    env_file.write_text("MEM0_ENABLED=enabled\nOTHER=kept\n", encoding="utf-8")
    block = _memory_block()
    loop = block[block.index("for _var in"):]
    script = f'ENV_FILE="{env_file.as_posix()}"\n' + loop
    done = subprocess.run([bash, "-c", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    lines = set(env_file.read_text(encoding="utf-8").split())
    assert {"MEM0_ENABLED=false", "GRAPHITI_ENABLED=false", "OTHER=kept"} <= lines
    assert "MEM0_ENABLED=enabled" not in lines
