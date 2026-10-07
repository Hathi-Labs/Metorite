"""No new direct call to an AI vendor. WS-45 S4, D90 clause 3.

Spec: ``project-docs/specs/ai_tier_routing.md`` §2.10, §10 (the last fence
row) and §11 S5.

D90 clause 3: every AI call goes through OUR Router tiers. No agent and no
tool calls an outside AI vendor directly. This test is the ratchet. It does
not move a call. S5 moves the calls, one task at a time.

What the test reads
-------------------
Every ``.py`` file under ``apps/`` and ``packages/``, and the root
``agents.py``. It reads the syntax tree, not the text, so a comment or a
docstring never trips it. ``apps/services/customer_console/`` is exempt,
because it IS the Router, and the Router is the one place that calls a vendor.
A ``tests`` directory is exempt too, because a test calls no vendor.

What counts as a direct vendor call
-----------------------------------
* each use of a ``litellm`` verb, such as ``litellm.acompletion`` or a bare
  ``aembedding`` that ``from litellm import`` binds, also through an alias.
  The import alone does not count, so a second call raises the count,
* the construction of an ``openai`` or ``anthropic`` SDK client, such as
  ``OpenAI(...)`` or ``AsyncAnthropic(...)``, also through an alias,
* an MAF chat client with no ``async_client`` and no ``base_url``, because
  its default address is the vendor,
* an import of a vendor AI SDK, such as ``openai``, ``litellm``,
  ``anthropic`` or ``deepgram``. Both forms count, also through an alias or
  a submodule: ``import openai.types``, ``from litellm import completion``
  and ``from google import genai``. The import counts BESIDE the verb or the
  client it brings, so a helper import such as ``model_cost`` counts too,
* the host of a vendor AI API in a string, such as ``api.openai.com``.

A USE of a litellm helper that makes no request (``model_cost``,
``token_counter``, ``ModelResponse``) does not count. Its import does.

The ratchet
-----------
``_BASELINE`` names each file that held a direct call at build time
(2026-10-06), the count of each finding, and a reason. A file off the list
with a finding fails. A count above the baseline fails. A count BELOW the
baseline fails too, so a PR that removes a call lowers the baseline in the
same PR. So the list only goes down.

``_ROUTER_SEAMS`` is not part of the ratchet. It names the one file that
builds an OpenAI SDK client on purpose, to point it at OUR gateway. Its
entry must match exactly, so a second client in that file fails too.

A file that does not parse FAILS the test. It never skips. A skipped file is
a file the fence does not read.

What the test cannot see (advisory)
-----------------------------------
A call through ``getattr(litellm, name)``, a host built from parts at run
time, and a third-party library that builds its own vendor client (mem0,
graphiti-core). ``acb_memory._gateway_env`` holds the two libraries on the
gateway, and ``test_background_ai_member.py`` holds their attribution.
"""

from __future__ import annotations

import ast
import inspect
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

#: The dirs that the fence reads, and the one root file.
_SCAN_DIRS = ("apps", "packages")
_ROOT_FILES = ("agents.py",)

#: Dirs that hold no source of ours. A local checkout can carry them.
_SKIP_PARTS = frozenset({"__pycache__", ".venv", "node_modules", ".git", "tests"})

#: The Router. It is the one place that calls a vendor (§10).
_EXEMPT_PREFIXES = ("apps/services/customer_console/",)

# ── What counts ──────────────────────────────────────────────────────────────

#: The litellm functions that send a request to a vendor.
_LITELLM_VERBS = frozenset({
    "completion", "acompletion", "text_completion", "atext_completion",
    "embedding", "aembedding", "transcription", "atranscription",
    "speech", "aspeech", "image_generation", "aimage_generation",
    "image_edit", "aimage_edit", "image_variation", "aimage_variation",
    "rerank", "arerank", "responses", "aresponses",
    "moderation", "amoderation", "batch_completion", "Router",
})

#: The SDK clients whose construction opens a vendor connection.
_SDK_CLIENTS = frozenset({
    "OpenAI", "AsyncOpenAI", "AzureOpenAI", "AsyncAzureOpenAI",
    "Anthropic", "AsyncAnthropic", "AnthropicBedrock", "AsyncAnthropicBedrock",
})

#: The MAF chat clients. With no ``async_client`` and no ``base_url``, the
#: client goes to the vendor's own address.
_MAF_CLIENTS = frozenset({
    "OpenAIChatCompletionClient", "OpenAIChatClient", "OpenAIResponsesClient",
    "OpenAIAssistantsClient",
})
_MAF_ADDRESS_ARGS = frozenset({"async_client", "base_url"})

#: The vendor AI SDKs. An import of one is a direct call in waiting, so each
#: import is a finding of its own, beside the verb or the client it brings.
#: ``openai`` and ``litellm`` are here too. Until 2026-10-07 they were not,
#: so a new file with a bare ``import openai`` passed the fence (found by
#: WS-48 N1, PR #711). The Router seam imports ``openai`` on purpose, and
#: ``_ROUTER_SEAMS`` holds that import.
_VENDOR_SDKS = (
    "openai", "litellm",
    "anthropic", "agent_framework_anthropic", "agent_framework.anthropic",
    "deepgram", "assemblyai", "mistralai", "cohere", "groq", "elevenlabs",
    "google.generativeai", "google.genai", "replicate", "together", "fireworks",
)

#: The hosts of vendor AI APIs. A substring match, so a subdomain counts.
_VENDOR_HOSTS = (
    "api.openai.com", "openai.azure.com", "api.anthropic.com",
    "api.deepgram.com", "assemblyai.com", "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com", "api.mistral.ai", "api.groq.com",
    "api.cohere.ai", "api.cohere.com", "api.elevenlabs.io", "api.deepseek.com",
    "openrouter.ai", "aimlapi.com", "api.together.xyz", "api.together.ai",
    "api.fireworks.ai", "api.x.ai", "api.perplexity.ai",
    "dashscope.aliyuncs.com", "api.moonshot.ai", "api.moonshot.cn",
    "api.replicate.com", "api.stability.ai", "bedrock-runtime.",
)

# ── The baseline (2026-10-06) ───────────────────────────────────────────────


@dataclass(frozen=True)
class _Entry:
    """The findings a file held at build time, and why each one stays."""

    findings: dict[str, int]
    reason: str


#: Each file that held a direct vendor call on 2026-10-06. Lower a count, or
#: delete the entry, in the PR that removes a call. NEVER add an entry or
#: raise a count. Build the call on the Router instead (``acb_llm.routed``).
_BASELINE: dict[str, _Entry] = {
    # ── Embeddings (§2.10 row 1 and 2). S5 step 2 moves them. The Router
    # serves no embeddings door yet, so each needs one first (D61.1).
    "apps/services/gateway/gateway/main.py": _Entry(
        {
            "client OpenAI": 1, "litellm verb acompletion": 1,
            "sdk openai": 1, "sdk litellm": 1,
        },
        "The /v1/embeddings door calls OpenAI with OPENAI_API_KEY (S5 step 2). "
        "The prompt-cache warm-up calls litellm, behind PROMPT_CACHE_PREWARM",
    ),
    "apps/services/email_ingestion/email_ingestion/email_embeddings.py": _Entry(
        {"litellm verb aembedding": 1, "sdk litellm": 1},
        "Email embeddings go to the gateway /v1/embeddings door, which skips "
        "the Router (S5 step 2)",
    ),
    "apps/services/whatsapp_ingestion/whatsapp_ingestion/wa_embeddings.py": _Entry(
        {"litellm verb aembedding": 1, "sdk litellm": 1},
        "WhatsApp embeddings go to the gateway /v1/embeddings door, which "
        "skips the Router (S5 step 2)",
    ),
    "apps/services/gateway/gateway/routes/tasks/capability.py": _Entry(
        {"litellm verb aembedding": 1, "sdk litellm": 1},
        "Capability embeddings go to the gateway /v1/embeddings door, which "
        "skips the Router (S5 step 2)",
    ),
    # ── Transcription (§2.10 rows 3 and 4). S5 steps 1 and 4.
    "packages/acb_stt/acb_stt/litellm_provider.py": _Entry(
        {"litellm verb atranscription": 1, "sdk litellm": 1},
        "Transcription through litellm. S5 step 1 moves it to the Router's "
        "/v1/audio/transcriptions on tier-stt",
    ),
    "packages/acb_stt/acb_stt/assemblyai_provider.py": _Entry(
        {"host assemblyai.com": 1},
        "Transcription through AssemblyAI. S5 step 1",
    ),
    "apps/services/gateway/gateway/routes/notes/live.py": _Entry(
        {"host api.deepgram.com": 1, "host assemblyai.com": 1},
        "Live notes mint Deepgram and AssemblyAI stream keys. S5 step 4 needs "
        "its own design. §2.10 names Deepgram only. The AssemblyAI key was "
        "found 2026-10-06",
    ),
    "apps/services/meeting_bot/app/live.py": _Entry(
        {"host assemblyai.com": 1},
        "The meeting bot streams call audio to AssemblyAI. S5 step 4. Not in "
        "§2.10, found 2026-10-06",
    ),
    # ── Chat that skips the Router (§2.10 rows 5 to 7).
    "apps/services/gateway/gateway/routes/integrations.py": _Entry(
        {"litellm verb acompletion": 1, "sdk litellm": 1},
        "POST /integrations/discover calls litellm for an API schema. A later "
        "slice moves it to acb_llm.routed",
    ),
    "packages/acb_llm/acb_llm/context.py": _Entry(
        {"litellm verb acompletion": 2, "sdk litellm": 5},
        "acompletion_with_fallback calls litellm when ROUTER_SERVING_ENABLED "
        "is off (H-171). acompletion_stream_text has no routed branch, so it "
        "calls litellm whatever the flag says. Each of the two calls imports "
        "litellm and the verb, and token_counter is the fifth import",
    ),
    "packages/acb_llm/acb_llm/client.py": _Entry(
        {"litellm verb acompletion": 2, "sdk litellm": 8},
        "complete and complete_with_tools call litellm when "
        "ROUTER_SERVING_ENABLED is off (H-171). Not in §2.10, found 2026-10-06. "
        "The eight litellm imports serve those calls, the prompt cache and the "
        "cost lookups (model_cost, completion_cost, cost_per_token)",
    ),
    "apps/services/gateway/gateway/routes/v1_compat.py": _Entry(
        {"litellm verb acompletion": 2, "sdk litellm": 3},
        "The gateway /v1 door calls litellm when its Router hop is off "
        "(ROUTER_SERVING_ENABLED). Not in §2.10, found 2026-10-06. The third "
        "litellm import is stream_chunk_builder, for that same stream",
    ),
    # ── Not AI work (§2.10, the last paragraph). They stay.
    "apps/services/gateway/gateway/routes/settings.py": _Entry(
        {
            "host api.openai.com": 1, "host api.deepseek.com": 1,
            "host api.groq.com": 1, "host api.mistral.ai": 1,
            "host api.together.xyz": 1, "host openrouter.ai": 1,
            "host generativelanguage.googleapis.com": 1,
            "host api.anthropic.com": 1, "host assemblyai.com": 1,
        },
        "The provider key tests and model lists. They move no tenant content, "
        "so they are not AI work (§2.10)",
    ),
    # ── Imports of a litellm helper that makes no request. The import rule
    # took in ``litellm`` on 2026-10-07 and found them. They were in the
    # tree before that date, so they enter the baseline once. Each import
    # stays a finding, because the next line in the file can be a call.
    "packages/acb_llm/acb_llm/routed.py": _Entry(
        {"sdk litellm": 1},
        "routed_acompletion rebuilds the Router's reply as litellm's "
        "ModelResponse, the type the call sites already hold. It sends no "
        "request",
    ),
    "packages/acb_llm/acb_llm/model_limits.py": _Entry(
        {"sdk litellm": 1},
        "_litellm_info reads litellm's model_cost registry for token limits. "
        "It sends no request",
    ),
    "packages/acb_llm/acb_llm/key_store.py": _Entry(
        {"sdk litellm": 1},
        "configure_litellm loads the stored provider keys into litellm's "
        "module config, for the litellm calls that ROUTER_SERVING_ENABLED "
        "turns off (H-171). It sends no request",
    ),
}

#: The files that build an OpenAI SDK client ON PURPOSE, to reach OUR
#: gateway. Not a ratchet: each entry must match exactly.
_ROUTER_SEAMS: dict[str, _Entry] = {
    "packages/acb_llm/acb_llm/attribution.py": _Entry(
        {"client AsyncOpenAI": 1, "sdk openai": 1},
        "attributed_openai builds the AsyncOpenAI client that every MAF agent "
        "hands to OpenAIChatCompletionClient. Its base_url is a required "
        "argument, and every caller passes the gateway /v1. The one "
        "import openai serves that client",
    ),
}

# ── The finder ───────────────────────────────────────────────────────────────


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_module(dotted: str, roots: tuple[str, ...]) -> str | None:
    for root in roots:
        if dotted == root or dotted.startswith(root + "."):
            return root
    return None


def _docstring_ids(tree: ast.AST) -> set[int]:
    """The ids of the docstring nodes, which hold text and no call."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        ) and node.body:
            head = node.body[0]
            if isinstance(head, ast.Expr) and isinstance(head.value, ast.Constant):
                out.add(id(head.value))
    return out


def _root_name(node: ast.expr) -> str | None:
    """The name at the root of ``a.b.c``, or None."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


@dataclass
class _Bindings:
    """The names that one file binds to a vendor call, read before the visit.

    ``litellm_modules`` holds each name bound to ``litellm`` or a module under
    it. ``verbs`` maps a name to the litellm verb it is bound to. ``clients``
    maps a name to the SDK or MAF client class it is bound to. So an alias
    counts as the name it stands for.
    """

    litellm_modules: set[str] = field(default_factory=lambda: {"litellm"})
    verbs: dict[str, str] = field(default_factory=dict)
    clients: dict[str, str] = field(default_factory=dict)


def _bindings(tree: ast.AST) -> _Bindings:
    out = _Bindings()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_module(alias.name, ("litellm",)):
                    out.litellm_modules.add(alias.asname or "litellm")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            from_litellm = _is_module(node.module, ("litellm",))
            for alias in node.names:
                bound = alias.asname or alias.name
                if from_litellm and alias.name in _LITELLM_VERBS:
                    out.verbs[bound] = alias.name
                elif from_litellm and alias.name == "main":
                    out.litellm_modules.add(bound)
                if alias.name in _SDK_CLIENTS | _MAF_CLIENTS:
                    out.clients[bound] = alias.name
    return out


def _names_an_address(kw: ast.keyword) -> bool:
    """True when *kw* gives an MAF client an address that is not None."""
    if kw.arg not in _MAF_ADDRESS_ARGS:
        return False
    return not (isinstance(kw.value, ast.Constant) and kw.value.value is None)


@dataclass
class _VendorFinder(ast.NodeVisitor):
    """Collects each direct vendor call in one syntax tree, as a label.

    A litellm verb counts at each USE (``litellm.acompletion`` or a bare
    ``acompletion`` bound by ``from litellm import``), never at the import.
    So the two forms count in one unit, and a second call raises the count.
    """

    docstrings: set[int]
    bound: _Bindings
    hits: list[str] = field(default_factory=list)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            sdk = _is_module(alias.name, _VENDOR_SDKS)
            if sdk:
                self.hits.append(f"sdk {sdk}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """One finding for each vendor SDK that the statement imports.

        ``from google import genai`` names the SDK in the module AND the
        name, so the rule reads ``google.genai`` too. A relative import is
        our own module, never a vendor.
        """
        module = node.module or ""
        if node.level == 0 and module:
            sdks = {_is_module(module, _VENDOR_SDKS)}
            sdks |= {_is_module(f"{module}.{a.name}", _VENDOR_SDKS) for a in node.names}
            self.hits.extend(f"sdk {sdk}" for sdk in sorted(s for s in sdks if s))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id in self.bound.verbs:
            self.hits.append(f"litellm verb {self.bound.verbs[node.id]}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            node.attr in _LITELLM_VERBS
            and _root_name(node.value) in self.bound.litellm_modules
        ):
            self.hits.append(f"litellm verb {node.attr}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        if isinstance(node.func, ast.Name):
            name = self.bound.clients.get(node.func.id, name)
        if name in _SDK_CLIENTS:
            self.hits.append(f"client {name}")
        elif name in _MAF_CLIENTS and not any(
            _names_an_address(kw) for kw in node.keywords
        ):
            self.hits.append(f"maf client {name} with no address")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and id(node) not in self.docstrings:
            for host in _VENDOR_HOSTS:
                if host in node.value:
                    self.hits.append(f"host {host}")


def _vendor_calls(source: str, filename: str = "<source>") -> Counter[str]:
    """Each direct vendor call in *source*, counted by label.

    A syntax error RAISES. It never skips. The bindings are read first, so a
    later ``_litellm.acompletion`` or an aliased client counts.
    """
    tree = ast.parse(source, filename=filename)
    finder = _VendorFinder(docstrings=_docstring_ids(tree), bound=_bindings(tree))
    finder.visit(tree)
    return Counter(finder.hits)


# ── The scan ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Scan:
    calls: dict[str, Counter[str]]
    parse_errors: dict[str, str]
    files: frozenset[str]


def _rel(path: Path) -> str:
    return path.relative_to(_REPO).as_posix()


def _python_files() -> list[Path]:
    out = [_REPO / name for name in _ROOT_FILES]
    for top in _SCAN_DIRS:
        out.extend(
            p for p in (_REPO / top).rglob("*.py")
            if not _SKIP_PARTS.intersection(p.relative_to(_REPO).parts)
            and not _rel(p).startswith(_EXEMPT_PREFIXES)
        )
    return sorted(out)


def _scan_files(paths: list[Path], rel: Callable[[Path], str] = _rel) -> _Scan:
    """Read and parse each file. Record each call and each parse error."""
    calls: dict[str, Counter[str]] = {}
    errors: dict[str, str] = {}
    for path in paths:
        key = rel(path)
        try:
            # utf-8-sig: some files under apps/ and packages/ start with a
            # byte order mark, and ast.parse refuses a leading U+FEFF.
            found = _vendor_calls(path.read_text(encoding="utf-8-sig"), str(path))
        # A UnicodeDecodeError is a ValueError, and so is a NUL byte.
        except (SyntaxError, ValueError) as exc:
            errors[key] = f"{type(exc).__name__}: {exc}"
            continue
        if found:
            calls[key] = found
    return _Scan(calls=calls, parse_errors=errors, files=frozenset(rel(p) for p in paths))


@cache
def _tree_scan() -> _Scan:
    return _scan_files(_python_files())


def _calls_or_fail() -> dict[str, Counter[str]]:
    """The calls in the tree. A parse error fails the caller. It never skips."""
    scan = _tree_scan()
    assert not scan.parse_errors, (
        "The vendor-call fence could not parse these files, so it cannot say "
        "they hold no direct call:\n  "
        + "\n  ".join(f"{p}: {e}" for p, e in sorted(scan.parse_errors.items()))
    )
    return scan.calls


def _allowed() -> dict[str, _Entry]:
    return {**_BASELINE, **_ROUTER_SEAMS}


# ── The fence on the tree ───────────────────────────────────────────────────


def test_every_scanned_file_parses() -> None:
    _calls_or_fail()


def test_the_scan_reads_the_whole_tree() -> None:
    files = _tree_scan().files
    assert "agents.py" in files, "the root agents.py is not scanned"
    for top in _SCAN_DIRS:
        assert any(f.startswith(f"{top}/") for f in files), f"nothing under {top}/ is scanned"
    for path in _allowed():
        assert path in files, f"{path} is on a list, but the scan does not read it"


def test_the_router_is_the_one_exempt_tree() -> None:
    files = _tree_scan().files
    assert not any(f.startswith(_EXEMPT_PREFIXES) for f in files)
    assert (_REPO / _EXEMPT_PREFIXES[0]).is_dir(), "the Router moved, so the exemption is stale"


def _off_the_baseline(calls: dict[str, Counter[str]]) -> dict[str, Counter[str]]:
    """The files with a finding that no list names."""
    return {p: c for p, c in sorted(calls.items()) if p not in _allowed()}


def test_no_direct_vendor_call_off_the_baseline() -> None:
    new = _off_the_baseline(_calls_or_fail())
    assert not new, (
        "D90 clause 3: every AI call goes through OUR Router (ai_tier_routing.md "
        "§3.1). These files call an AI vendor directly:\n  "
        + "\n  ".join(f"{p}: {dict(c)}" for p, c in new.items())
        + "\n\nCall acb_llm.routed (or acb_llm.context.acompletion_with_fallback) "
        "instead. Do not add the file to _BASELINE."
    )


def test_no_count_rises_above_the_baseline() -> None:
    calls = _calls_or_fail()
    over: list[str] = []
    for path, entry in sorted(_allowed().items()):
        for label, count in sorted(calls.get(path, Counter()).items()):
            allowed = entry.findings.get(label, 0)
            if count > allowed:
                over.append(f"{path}: {label} = {count}, baseline {allowed}")
    assert not over, (
        "A grandfathered file gained a direct vendor call. Route the new call "
        "through acb_llm.routed:\n  " + "\n  ".join(over)
    )


def test_the_baseline_only_goes_down() -> None:
    """A count below the baseline fails, so the PR that removes a call lowers it."""
    calls = _calls_or_fail()
    stale: list[str] = []
    for path, entry in sorted(_BASELINE.items()):
        for label, allowed in sorted(entry.findings.items()):
            count = calls.get(path, Counter()).get(label, 0)
            if count < allowed:
                stale.append(f"{path}: {label} = {count}, baseline {allowed}")
    assert not stale, (
        "These baseline counts are higher than the tree. Lower them, or delete "
        "the entry, in this PR, so the baseline only goes down:\n  "
        + "\n  ".join(stale)
    )


def test_each_router_seam_matches_exactly() -> None:
    calls = _calls_or_fail()
    for path, entry in _ROUTER_SEAMS.items():
        assert dict(calls.get(path, Counter())) == entry.findings, (
            f"{path} is a Router seam. It must hold exactly {entry.findings}"
        )


def test_every_entry_says_why() -> None:
    for path, entry in _allowed().items():
        assert entry.reason.strip(), f"{path} has no reason"
        assert entry.findings and all(n > 0 for n in entry.findings.values()), path


def test_the_seam_cannot_reach_a_vendor_by_default() -> None:
    """``attributed_openai`` has no default address, so a caller must name one.

    An ``AsyncOpenAI`` with no ``base_url`` goes to ``api.openai.com``. A
    default on this parameter would let a caller reach the vendor silently.
    """
    from acb_llm.attribution import attributed_openai

    base_url = inspect.signature(attributed_openai).parameters["base_url"]
    assert base_url.default is inspect.Parameter.empty
    assert base_url.kind is inspect.Parameter.KEYWORD_ONLY


# ── The finder itself ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "label"),
    [
        ("import litellm\nlitellm.acompletion(model='x')\n", "litellm verb acompletion"),
        ("import litellm as _l\nawait _l.aembedding(model='x')\n", "litellm verb aembedding"),
        ("def f():\n    import litellm as ll\n    ll.atranscription()\n", "litellm verb atranscription"),
        ("from litellm import acompletion\nawait acompletion(m)\n", "litellm verb acompletion"),
        ("from litellm import aspeech as s\nawait s(x)\n", "litellm verb aspeech"),
        ("from litellm.main import completion\ncompletion(m)\n", "litellm verb completion"),
        ("import litellm.main as m\nm.acompletion(x)\n", "litellm verb acompletion"),
        ("import litellm\nlitellm.main.acompletion(x)\n", "litellm verb acompletion"),
        ("from litellm import main as lm\nlm.aembedding(x)\n", "litellm verb aembedding"),
        ("from openai import AsyncOpenAI as C\nc = C()\n", "client AsyncOpenAI"),
        ("from agent_framework.openai import OpenAIChatCompletionClient as K\nK(model='m')\n",
         "maf client OpenAIChatCompletionClient with no address"),
        ("c = OpenAIChatCompletionClient(model='m', base_url=None)\n",
         "maf client OpenAIChatCompletionClient with no address"),
        ("import litellm\nr = litellm.Router(model_list=[])\n", "litellm verb Router"),
        ("from openai import OpenAI\nc = OpenAI(api_key=k)\n", "client OpenAI"),
        ("import openai\nc = openai.AsyncOpenAI()\n", "client AsyncOpenAI"),
        ("import anthropic\n", "sdk anthropic"),
        ("from anthropic import AsyncAnthropic\n", "sdk anthropic"),
        ("c = AsyncAnthropic()\n", "client AsyncAnthropic"),
        ("from agent_framework_anthropic import AnthropicClient\n", "sdk agent_framework_anthropic"),
        ("from deepgram import DeepgramClient\n", "sdk deepgram"),
        ("import google.generativeai as genai\n", "sdk google.generativeai"),
        ("URL = 'https://api.openai.com/v1'\n", "host api.openai.com"),
        ("u = f'https://api.anthropic.com/{path}'\n", "host api.anthropic.com"),
        ("WS = 'wss://streaming.assemblyai.com/v3'\n", "host assemblyai.com"),
        ("c = OpenAIChatCompletionClient(model='tier-balanced')\n",
         "maf client OpenAIChatCompletionClient with no address"),
    ],
)
def test_the_finder_catches_a_direct_call(source: str, label: str) -> None:
    assert label in _vendor_calls(source), f"the fence missed {label!r} in: {source!r}"


@pytest.mark.parametrize(
    "source",
    [
        "# litellm.acompletion and https://api.openai.com in a comment\n",
        '"""Call litellm.acompletion at https://api.openai.com."""\n',
        "def f():\n    'from litellm import acompletion, api.openai.com'\n",
        "c = OpenAIChatCompletionClient(model='tier-fast', async_client=ac)\n",
        "c = OpenAIChatCompletionClient(model='m', base_url=gateway)\n",
        "other.acompletion(model='x')\n",
        "URL = 'https://gmail.googleapis.com/gmail/v1'\n",
        "from .anthropic import helper\n",
        "from .openai import helper\nfrom . import litellm\n",
        "label = 'anthropic'\nslug = 'openai/gpt-4o'\n",
        "from google import auth\nfrom google.cloud import storage\n",
        "from agent_framework import ChatAgent\n",
        "import openai_compat_shim\nimport litellm_helpers\n",
    ],
)
def test_the_finder_ignores_text_and_routed_calls(source: str) -> None:
    assert not _vendor_calls(source), f"the fence flagged a non-call: {source!r}"


# ── The import rule (2026-10-07). WS-48 N1 (PR #711) found that a bare
# ``import openai`` passed. ``openai`` and ``litellm`` were off the module
# list, and ``from google import genai`` read only the module ``google``.

#: Each import form, and the one finding it must give. A helper import such
#: as ``model_cost`` is here too: its import counts, and its use does not.
_IMPORT_FORMS = [
    ("import openai\n", "sdk openai"),
    ("import openai as oa\n", "sdk openai"),
    ("import openai.types\n", "sdk openai"),
    ("from openai import OpenAI\n", "sdk openai"),
    ("from openai.types.chat import ChatCompletion as C\n", "sdk openai"),
    ("import openai\nh = openai.DefaultAsyncHttpxClient()\n", "sdk openai"),
    ("import litellm\n", "sdk litellm"),
    ("import litellm as _l\n", "sdk litellm"),
    ("def f():\n    import litellm.caching\n", "sdk litellm"),
    ("from litellm import completion  # imported and never used\n", "sdk litellm"),
    ("from litellm.caching.caching import Cache\n", "sdk litellm"),
    ("from litellm import ModelResponse, token_counter, model_cost\n", "sdk litellm"),
    ("import litellm\nlitellm.drop_params = True\ncost = litellm.model_cost\n", "sdk litellm"),
    ("from google import genai\n", "sdk google.genai"),
    ("from google import generativeai as g\n", "sdk google.generativeai"),
    ("from agent_framework import anthropic\n", "sdk agent_framework.anthropic"),
]


@pytest.mark.parametrize(("source", "label"), _IMPORT_FORMS)
def test_each_vendor_import_form_is_one_finding(source: str, label: str) -> None:
    assert _vendor_calls(source) == Counter({label: 1}), (
        f"the fence must count {label!r} once, and nothing else, in: {source!r}"
    )


@pytest.mark.parametrize(("source", "label"), _IMPORT_FORMS)
def test_a_planted_vendor_import_fails_the_fence(
    tmp_path: Path, source: str, label: str,
) -> None:
    """R7: a NEW product file that only imports a vendor SDK is off the baseline."""
    rel = "apps/services/probe/probe/new_feature.py"
    path = tmp_path / rel
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    scan = _scan_files([path], rel=lambda p: p.relative_to(tmp_path).as_posix())
    assert not scan.parse_errors
    assert _off_the_baseline(scan.calls) == {rel: Counter({label: 1})}


def test_one_statement_counts_each_sdk_it_imports() -> None:
    assert _vendor_calls("import openai, litellm\n") == Counter(
        {"sdk openai": 1, "sdk litellm": 1},
    )
    assert _vendor_calls("from google import genai, generativeai\n") == Counter(
        {"sdk google.genai": 1, "sdk google.generativeai": 1},
    )
    assert _vendor_calls("from openai import OpenAI, AsyncOpenAI\n") == Counter(
        {"sdk openai": 1},
    )


def test_each_use_of_an_imported_verb_counts() -> None:
    """Review P1 of PR #681. The import counted once, so a second call was free."""
    one = "from litellm import acompletion\nawait acompletion(a)\n"
    two = one + "await acompletion(b)\n"
    assert _vendor_calls(one)["litellm verb acompletion"] == 1
    assert _vendor_calls(two)["litellm verb acompletion"] == 2
    attr = "import litellm\nlitellm.acompletion(a)\nlitellm.acompletion(b)\n"
    assert _vendor_calls(attr)["litellm verb acompletion"] == 2


def test_a_file_that_does_not_parse_fails(tmp_path: Path) -> None:
    bad = tmp_path / "broken.py"
    bad.write_text("def broken(:\n    pass\n", encoding="utf-8")
    scan = _scan_files([bad], rel=lambda p: p.name)
    assert "broken.py" in scan.parse_errors
    assert not scan.calls


def test_a_byte_order_mark_does_not_hide_a_call(tmp_path: Path) -> None:
    bom = tmp_path / "bom.py"
    bom.write_bytes(b"\xef\xbb\xbfimport litellm\nlitellm.acompletion()\n")
    scan = _scan_files([bom], rel=lambda p: p.name)
    assert not scan.parse_errors
    assert scan.calls["bom.py"]["litellm verb acompletion"] == 1
