"""WS-29 MT-1j slice 5 — the credential/config tenant-threading ratchet.

Spec: ``project-docs/specs/saas_multitenancy.md`` §11 MT-1j slice 5 · MT-0d §6.3
· D15 · R5 · ``user_management_contract.md`` R11.

**The defect this ratchets down.** ``key_store._resolve_org(None)`` and
``model_config._resolve_org(cur, None)`` answer *the sole organization*, and
only while ``count(*) FROM organization = 1``. The moment org #2 exists every
untenanted read returns nothing and every untenanted write raises. That is
**correct and must NOT be repaired** (``work_plan.md`` §3, D33 finding 3): a
"default org" fallback would keep answering after tenant #2 and serve the
operator's keys to a customer. The work is passing ``organization_id`` at the
CALL SITES, from the tenant the request already bound.

**Why a ratchet.** Slice 5 lands across several PRs, so the completion clause
("a second organization's provider key and model config read correctly") is
otherwise satisfiable by threading three sites. This is
``test_db_engine_seam.py:310``'s H2 bank-your-progress mechanism, one seam
along: pin the counts, assert they only go DOWN, pin a converted file at zero,
and pin each deliberate leftover at its exact remaining count so it cannot be
quietly "converted" with a guessed tenant either.

**Two measurements, and the difference is the finding.**

* The **surface** — the spec's own two greps, 38 ``get_key_store()`` lines and
  10 ``load_blob``/``save_blob`` lines. A converted site KEEPS both of those
  strings, so these are ceilings ("no new credential call site"), not ratchets.
* The **untenanted call count** — parsed, not grepped: a call to a
  tenant-scoped store method, or to ``load_blob``/``save_blob``, that passes no
  ``organization_id``. This is the number that falls as slice 5 lands, and it
  is what "38 sites fail closed" was actually claiming.

⚠️ **Re-measured 2026-08-19 and the spec's 38 over-states the blast radius by
~3.5x.** Of the 48 store-method calls reachable from those 38 lines, **33 are
``encrypt``/``decrypt``** — pure Fernet over the deployment's single
``ACB_MASTER_KEY``, used to seal ``email_accounts.credentials_encrypted`` and
the WhatsApp bridge's credentials. They never call ``_resolve_org`` and a
second organization does not perturb them at all. One more is ``_execute`` (a
raw-SQL escape hatch whose statement carries its own scoping) and three are
``configure_litellm``/``configure_integrations`` (the process-global startup
path). **Only 11 calls actually resolved a tenant**, and those plus the 10
``model_config`` calls are the real 21-site surface. Both numbers are pinned
below so the correction is auditable in either direction.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

#: Scanned trees. ``tests/`` is excluded for the reason slice 6's ratchet
#: records: a suite that deliberately drives the untenanted arm (the mt0d pair)
#: would otherwise be counted as a violation of the thing it is pinning.
_SCANNED_TREES = ("apps", "packages")

#: The store methods that resolve a tenant. Derived from ``key_store.py``: each
#: one opens with ``org = await self._resolve_org(organization_id)``.
#: ``encrypt``/``decrypt`` are NOT here (no tenant dimension — see the module
#: docstring), and neither is ``_execute`` (raw SQL, carries its own scope).
_TENANT_SCOPED_METHODS = frozenset(
    {"get", "put", "delete", "get_all", "get_by_type", "get_by_service"}
)

_BLOB_FUNCTIONS = frozenset({"load_blob", "save_blob"})

#: ``model_config.py`` defines ``load_blob``/``save_blob``; the spec's own
#: measuring command excludes it and so does this scan.
_BLOB_DEFINITION_FILE = "packages/acb_llm/acb_llm/model_config.py"


# ── The scan ────────────────────────────────────────────────────────────────

def _python_files() -> list[Path]:
    out: list[Path] = []
    for tree in _SCANNED_TREES:
        for path in (_ROOT / tree).rglob("*.py"):
            parts = path.parts
            if "__pycache__" in parts or "tests" in parts:
                continue
            out.append(path)
    return sorted(out)


def _rel(path: Path) -> str:
    return path.relative_to(_ROOT).as_posix()


def _store_bindings(tree: ast.AST) -> set[str]:
    """Names assigned from ``get_key_store()`` anywhere in the module."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        func = node.value.func
        is_accessor = (isinstance(func, ast.Name) and func.id == "get_key_store") or (
            isinstance(func, ast.Attribute) and func.attr == "get_key_store"
        )
        if not is_accessor:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _is_store_expr(node: ast.expr, bindings: set[str]) -> bool:
    """Is *node* a ``ProviderKeyStore``? Either a bound name or the accessor.

    ⚠️ **Two receiver shapes this does NOT see. Both are named here rather than
    fixed, so every count below is a count of the walker-visible set** (repair
    round 3, 2026-08-19):

    1. ``self.<method>`` inside ``key_store.py`` itself. Three real untenanted
       calls live there — see
       :func:`test_every_remaining_untenanted_site_is_an_owner_gated_one`.
    2. An accessor **wrapper**. ``routes/tasks/core.py:326`` defines
       ``_key_store()`` (a one-line ``return get_key_store()``); six modules
       import it and call it 15 times. The receiver is then a call to
       ``_key_store``, not to ``get_key_store``, so this returns ``False``.
       Nothing is missed *today* — all 15 are ``encrypt``/``decrypt``, which
       have no tenant dimension at all — but a credential read grown behind
       that wrapper would be invisible to every count in this file. If that
       happens, widen the accepted receivers here; the shape is recorded so
       the next agent recognises it rather than trusting the number.
    """
    if isinstance(node, ast.Name):
        return node.id in bindings
    if isinstance(node, ast.Call):
        func = node.func
        return (isinstance(func, ast.Name) and func.id == "get_key_store") or (
            isinstance(func, ast.Attribute) and func.attr == "get_key_store"
        )
    return False


def _tenanted(call: ast.Call) -> bool:
    return any(kw.arg == "organization_id" for kw in call.keywords)


def untenanted_sites() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """``({file: [key_store sites]}, {file: [model_config sites]})``.

    A "site" is ``line:name``. Parsed rather than grepped because the argument
    that matters — ``organization_id=`` — routinely lands on a different
    physical line from the call, and a line-oriented ratchet would read a
    correctly-threaded multi-line call as unconverted.
    """
    key_store: dict[str, list[str]] = {}
    blobs: dict[str, list[str]] = {}
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        if not any(
            token in source
            for token in ("get_key_store", "load_blob", "save_blob")
        ):
            continue
        rel = _rel(path)
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover — a broken file is not our fence
            continue
        bindings = _store_bindings(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in _TENANT_SCOPED_METHODS
                and _is_store_expr(func.value, bindings)
                and not _tenanted(node)
            ):
                key_store.setdefault(rel, []).append(f"{node.lineno}:{func.attr}")
            if rel == _BLOB_DEFINITION_FILE:
                continue
            name = None
            if isinstance(func, ast.Name) and func.id in _BLOB_FUNCTIONS:
                name = func.id
            elif isinstance(func, ast.Attribute) and func.attr in _BLOB_FUNCTIONS:
                name = func.attr
            if name and not _tenanted(node):
                blobs.setdefault(rel, []).append(f"{node.lineno}:{name}")
    return key_store, blobs


def _total(sites: dict[str, list[str]]) -> int:
    return sum(len(v) for v in sites.values())


# ── The pinned numbers ──────────────────────────────────────────────────────

#: The spec's own greps, verbatim, as CEILINGS. A converted site keeps both
#: strings, so these do not fall when work lands — what they forbid is a NEW
#: credential or model-config call site appearing while slice 5 is in flight.
#: Measured 2026-08-19 on this branch's parent (50e203c0) and unchanged by
#: round 1, which is the point: 38 and 10 describe the surface, not the debt.
GET_KEY_STORE_LINES_CEILING = 38
BLOB_LINES_CEILING = 10

#: The real debt, measured with :func:`untenanted_sites` on 50e203c0:
#: **11** untenanted tenant-scoped store calls and **10** untenanted blob
#: calls. Round 1 banked 11 → 9 and 10 → 1; round 2 banks 9 → 4 (the five
#: ``routes/integrations.py`` sites), leaving exactly the H4 remainder below.
UNTENANTED_KEY_STORE_BASELINE = 4
UNTENANTED_BLOB_BASELINE = 1

#: Files converted to ZERO untenanted sites, pinned parametrically — the
#: ``H2_CONVERTED_PACKAGES`` idiom. Round 1 (the model-config subgraph on the
#: LLM/model surface, plus the provider-key write on the same surface):
CONVERTED_FILES: tuple[str, ...] = (
    # Settings → Models: `tier_overrides` + `enabled_models` (6 blob calls) and
    # the provider-key write behind POST /settings/llm/key (2 store calls, the
    # running-loop and no-loop branches). Every route here is under the
    # app-wide `require_authenticated`, so the tenant is bound.
    "apps/services/gateway/gateway/routes/settings.py",
    # Agent display-name aliases (3 blob calls), read by the Agents page and by
    # observability's roster — both HTTP handlers with a bound tenant.
    "apps/services/gateway/gateway/routes/agent.py",
    # Round 2 — the Integration Registry credential surface (5 store calls):
    # `/integrations/status` and `GET /integrations/keys` (`get_by_type`),
    # `POST /integrations/configure` and `PUT /integrations/keys` (`put`), and
    # `DELETE /integrations/keys` (`delete`). Every one is reached through the
    # app-wide `require_authenticated` — the module's only `PUBLIC_ROUTES`
    # entry is the OAuth callback, which touches none of them.
    "apps/services/gateway/gateway/routes/integrations.py",
)

#: Sites that STAY untenanted, as file → exact remaining count, each a decision
#: with its reason — the ``H2_EXEMPT_FILES`` / ``H2_APPS_EXEMPT_SITES`` idiom.
#: EXACT counts, not ceilings: an entry that silently dropped would mean
#: somebody threaded a guessed tenant through a path that has none, which is
#: the R5/R11 violation this class of marker exists to prevent. Every site
#: carries a matching ``H4:`` comment in place.
H4_KEY_STORE_SITES: dict[str, int] = {
    "apps/services/gateway/gateway/main.py":
        # lifespan startup: no request, no session, no bound tenant — and the
        # destination (`configure_litellm` → `litellm.<provider>_api_key` +
        # os.environ) is process-global regardless.
        2,
    "packages/acb_llm/acb_llm/client.py":
        # `_ensure_keys_loaded` — a once-per-process latch on the LIVE
        # completion path, whose only caller (`/v1/chat/completions`) is
        # authenticated by the deployment-wide LITELLM_MASTER_KEY and binds no
        # tenant. A real fix needs a tenant-scoped API key: owner gate (f).
        2,
}

H4_BLOB_SITES: dict[str, int] = {
    "packages/acb_llm/acb_llm/client.py":
        # `_init_tier_models` runs at IMPORT time and fills the process-global
        # `_TIER_MODEL`. There is no tenant, and a per-org value in a global
        # map would be worse than none.
        1,
}


# ── The surface ceilings (the spec's own greps) ─────────────────────────────

_GET_KEY_STORE_LINE = re.compile(r"get_key_store\(\)")
_BLOB_LINE = re.compile(r"\b(?:load_blob|save_blob)\(")


def _matching_lines(pattern: re.Pattern[str], *, skip_blob_definition: bool) -> list[str]:
    hits: list[str] = []
    for path in _python_files():
        rel = _rel(path)
        if skip_blob_definition and rel == _BLOB_DEFINITION_FILE:
            continue
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if "def get_key_store" in line:
                continue
            if pattern.search(line):
                hits.append(f"{rel}:{number}")
    return hits


class TestTheCredentialSurfaceDoesNotGrow:
    """The spec's two measuring commands, as ceilings.

    These reproduce ``saas_multitenancy.md`` §11 slice 5's own greps so the
    published numbers stay checkable against the tree. They cannot fall as
    conversion lands (a threaded call still says ``get_key_store()``), which is
    exactly why the untenanted ratchet below exists as well — pinning only
    these would be a fence that can never move.
    """

    def test_no_new_get_key_store_call_site(self):
        hits = _matching_lines(_GET_KEY_STORE_LINE, skip_blob_definition=False)
        assert len(hits) <= GET_KEY_STORE_LINES_CEILING, (
            f"{len(hits)} `get_key_store()` lines, ceiling "
            f"{GET_KEY_STORE_LINES_CEILING}. A new credential call site while "
            "slice 5 is converting the existing ones is debt written past the "
            "ratchet — pass organization_id at the new site "
            f"(saas_multitenancy.md §11 MT-1j slice 5). Sites: {hits}"
        )

    def test_no_new_model_config_blob_call_site(self):
        hits = _matching_lines(_BLOB_LINE, skip_blob_definition=True)
        assert len(hits) <= BLOB_LINES_CEILING, (
            f"{len(hits)} `load_blob`/`save_blob` lines, ceiling "
            f"{BLOB_LINES_CEILING}. Sites: {hits}"
        )


class TestTheUntenantedRatchet:
    """The number that must only go DOWN."""

    def test_untenanted_key_store_calls_only_fall(self):
        sites, _ = untenanted_sites()
        total = _total(sites)
        assert total <= UNTENANTED_KEY_STORE_BASELINE, (
            f"{total} untenanted tenant-scoped key_store calls, baseline "
            f"{UNTENANTED_KEY_STORE_BASELINE}. Each one returns nothing (or "
            "raises, on a write) the moment a second organization exists — "
            "pass organization_id from the bound tenant, never from request "
            f"input (R5 / R11). Sites: {sites}"
        )

    def test_untenanted_blob_calls_only_fall(self):
        _, sites = untenanted_sites()
        total = _total(sites)
        assert total <= UNTENANTED_BLOB_BASELINE, (
            f"{total} untenanted load_blob/save_blob calls, baseline "
            f"{UNTENANTED_BLOB_BASELINE}. Sites: {sites}"
        )

    def test_the_baselines_are_not_stale(self):
        """A ratchet whose baseline drifted above reality stops ratcheting.

        Landing a conversion without lowering the number here leaves headroom
        for the next unthreaded site to be added for free — the same failure
        ``test_the_baseline_is_not_stale`` guards in
        ``test_org_provisioning.py``. Bank your progress in the same commit.
        """
        key_store, blobs = untenanted_sites()
        assert (_total(key_store), _total(blobs)) == (
            UNTENANTED_KEY_STORE_BASELINE,
            UNTENANTED_BLOB_BASELINE,
        ), (
            f"measured {_total(key_store)} key_store + {_total(blobs)} blob "
            f"untenanted calls, pinned at {UNTENANTED_KEY_STORE_BASELINE} + "
            f"{UNTENANTED_BLOB_BASELINE}. If that is progress, lower the "
            "baselines here in the same commit. key_store: "
            f"{key_store}  blobs: {blobs}"
        )


@pytest.mark.parametrize("relative_path", CONVERTED_FILES)
def test_converted_files_stay_converted(relative_path: str):
    """A converted file has ZERO untenanted sites, and stays that way.

    The total ratchet alone cannot express this: a PR could thread one new site
    and un-thread one converted site for a net zero. Pinning the converted set
    at zero is what makes the progress banked rather than merely netted.
    """
    key_store, blobs = untenanted_sites()
    leftovers = key_store.get(relative_path, []) + blobs.get(relative_path, [])
    assert leftovers == [], (
        f"{relative_path} is pinned as converted but carries untenanted "
        f"credential/config calls at {leftovers}. Either thread the tenant "
        "from the bound session, or — if this site genuinely has none — move "
        "it to H4_KEY_STORE_SITES/H4_BLOB_SITES with its reason and an `H4:` "
        "comment in place, which is a decision rather than a regression."
    )


@pytest.mark.parametrize(
    ("relative_path", "expected"), sorted(H4_KEY_STORE_SITES.items())
)
def test_h4_key_store_sites_hold_their_exact_count(relative_path: str, expected: int):
    """Exact, in both directions — the H2 exempt-site discipline.

    Growing means a new unthreadable site appeared without an argument.
    *Shrinking* means somebody threaded a tenant through a path that has none,
    which is the guess R5 forbids and is worse than the leftover: it would look
    converted while resolving to whatever happened to be in context.
    """
    key_store, _ = untenanted_sites()
    found = key_store.get(relative_path, [])
    assert len(found) == expected, (
        f"{relative_path}: {len(found)} untenanted key_store calls, pinned at "
        f"{expected}. Sites: {found}"
    )


@pytest.mark.parametrize(
    ("relative_path", "expected"), sorted(H4_BLOB_SITES.items())
)
def test_h4_blob_sites_hold_their_exact_count(relative_path: str, expected: int):
    _, blobs = untenanted_sites()
    found = blobs.get(relative_path, [])
    assert len(found) == expected, (
        f"{relative_path}: {len(found)} untenanted blob calls, pinned at "
        f"{expected}. Sites: {found}"
    )


@pytest.mark.parametrize("relative_path", sorted(
    set(H4_KEY_STORE_SITES) | set(H4_BLOB_SITES)
))
def test_every_h4_site_carries_its_marker_in_place(relative_path: str):
    """The reason lives beside the code, not only in this file.

    ``H2_WHATSAPP_EXEMPT_SITES``'s comment records the same rule: *"every site
    carries the matching ``# H4``/``# H4/H6`` marker"*. A reader of
    ``main.py`` must not have to find this test to learn why the call has no
    tenant.
    """
    source = (_ROOT / relative_path).read_text(encoding="utf-8")
    assert re.search(r"H4[:\s]", source), (
        f"{relative_path} is exempted here but carries no `H4:` marker in "
        "place — the exemption is a decision and belongs beside the code"
    )


def test_every_remaining_untenanted_site_is_an_owner_gated_one():
    """The terminal statement — and its subject is the WALKER-VISIBLE set.

    **Scope, stated precisely because the short form of this claim was
    false.** What is measured is every direct call to a tenant-scoped store
    method in ``apps/`` + ``packages/`` whose receiver is ``get_key_store()``
    or a name bound from it (:func:`_is_store_expr`). Over *that* set, round 2
    lands the last agent-safe subgraph and the remainder is EXACTLY the H4
    set — the once-per-process latch on the live completion path, the lifespan
    startup, and the import-time tier map. Each needs a **tenant-scoped API
    key** and per-request provider credentials, which is a credential-scope
    change and ``work_plan.md`` §6 gate (f).

    ⚠️ **Known remainder this test does NOT count: three untenanted calls
    inside ``packages/acb_llm/acb_llm/key_store.py`` itself**, verified against
    the file 2026-08-19 — ``self.get(provider)`` at **:420** and
    ``self.put(...)`` at **:433** (both in ``configure_integrations``), and
    ``self.get_all()`` at **:472** (in ``configure_litellm``). Their receiver
    is ``self``, so the walker is blind to them by construction; "the
    remainder is exactly the H4 set" is therefore a statement about the
    walker-visible set, never about the tree. They are **H4 in class, not in
    count**: the two methods holding them are called from exactly the sites
    the H4 tables already name — ``configure_integrations`` only from
    ``gateway/main.py``'s lifespan, ``configure_litellm`` from that same
    lifespan and from ``acb_llm.client._ensure_keys_loaded`` — and both
    destinations (``litellm.<provider>_api_key``, ``os.environ``) are
    process-global, so threading them travels with the same owner-gated
    credential-scope act rather than being separately dispatchable. One fact
    for whoever performs it: **:433 is a WRITE, and ``put`` RAISES** once a
    second organization exists, aborting ``configure_integrations`` mid-loop;
    its caller wraps the whole block in ``try/except`` →
    ``gateway.key_store_skipped``, so every integration after the raising one
    is silently left unconfigured rather than the process failing.

    Stated as set equality rather than as a total, because two totals can agree
    while naming different sites. This is the fence a future agent trips by
    threading a *guessed* org at an owner-gated site to make the number fall —
    the leftover is the honest state, and the number cannot go lower without
    the owner's act. When that act lands, this test and the H4 tables move
    together or the fence is lying.
    """
    key_store, blobs = untenanted_sites()
    assert set(key_store) == set(H4_KEY_STORE_SITES), (
        "the untenanted key_store sites are no longer exactly the H4 set — "
        f"measured {sorted(key_store)}, pinned {sorted(H4_KEY_STORE_SITES)}"
    )
    assert set(blobs) == set(H4_BLOB_SITES), (
        "the untenanted blob sites are no longer exactly the H4 set — "
        f"measured {sorted(blobs)}, pinned {sorted(H4_BLOB_SITES)}"
    )
    assert (_total(key_store), _total(blobs)) == (
        sum(H4_KEY_STORE_SITES.values()),
        sum(H4_BLOB_SITES.values()),
    ), (
        f"measured {_total(key_store)} + {_total(blobs)} untenanted calls "
        f"against an H4 remainder of {sum(H4_KEY_STORE_SITES.values())} + "
        f"{sum(H4_BLOB_SITES.values())}. Every WALKER-VISIBLE untenanted "
        "credential call in apps/ + packages/ must be one the H4 tables name "
        "with a reason; a new one that is not is debt written past the "
        "ratchet. (Scope, not a loophole: key_store.py's own three `self.` "
        "calls at :420/:433/:472 are an uncounted remainder of the same "
        "class — see this test's docstring.)"
    )


class TestTheFailClosedContractIsNotRepaired:
    """The convenience that must NOT be fixed (``work_plan.md`` §3, D33.3).

    Slice 5's whole method is *supplying* a tenant, never *softening* the
    resolution. The predictable wrong turn is a future agent reading
    "untenanted reads return nothing" as a bug and adding a default-org
    fallback — which keeps answering after tenant #2 and serves the operator's
    keys to a customer. Both copies of the predicate are pinned by content.

    The two behavioural tripwires (``test_mt0d_per_org_credentials.py:163``
    and ``:176``) stay green and UNEDITED; this asserts the source shape they
    depend on, so a rewrite that keeps them passing by accident still fails.
    """

    _SOLE_ORG = re.compile(
        r"WHERE\s*\(\s*SELECT\s+count\(\*\)\s+FROM\s+organization\s*\)\s*=\s*1",
        re.IGNORECASE,
    )

    @pytest.mark.parametrize(
        "relative_path",
        [
            "packages/acb_llm/acb_llm/key_store.py",
            "packages/acb_llm/acb_llm/model_config.py",
        ],
    )
    def test_the_sole_org_predicate_survives(self, relative_path: str):
        source = (_ROOT / relative_path).read_text(encoding="utf-8")
        assert self._SOLE_ORG.search(source), (
            f"{relative_path} no longer resolves an untenanted call with "
            "`count(*) = 1`. That predicate is the fail-closed contract MT-0d "
            "shipped deliberately and work_plan.md §3 (D33 finding 3) records "
            "as one that must NOT be 'fixed'. Thread the tenant at the call "
            "site instead."
        )

    def test_no_default_org_fallback_appears(self):
        """The specific replacement D33.3 predicts, refused by name."""
        for relative_path in (
            "packages/acb_llm/acb_llm/key_store.py",
            "packages/acb_llm/acb_llm/model_config.py",
        ):
            source = (_ROOT / relative_path).read_text(encoding="utf-8")
            offending = [
                f"{relative_path}:{n}"
                for n, line in enumerate(source.splitlines(), start=1)
                if re.search(r"slug\s*=\s*'default'", line, re.IGNORECASE)
            ]
            assert not offending, (
                f"a `slug = 'default'` fallback appeared at {offending} — that "
                "is the default-org lookup MT-0d refused: it keeps answering "
                "after tenant #2 and serves the operator's keys to a customer"
            )


# ==========================================================================
# R8 — two organizations, one real Postgres.
# ==========================================================================
#
# ⚠️ A hermetic version of the section below would be worth less than nothing.
# The subjects are a `count(*)` sub-select, a composite primary key and an
# `ON CONFLICT (organization_id, provider)` arbiter; a fake agrees with
# whatever SQL it is handed, which is how slice 6's 42P10 shipped green. The
# structural half above never skips; this half is gated on
# `TENANT_LADDER_DATABASE_URL` — never `DATABASE_URL`, which arms
# `test_tenant_coverage.py`'s two DB-gated tests (see `tests/conftest.py`).
#
# Run::
#
#     export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant
#     uv run pytest tests/unit/test_credential_tenant_threading.py -v -rs

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text  # noqa: E402

from tests.unit._tenant_ladder import apply_ladder  # noqa: E402

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector (infra/postgres/01_schema.sql needs uuid-ossp AND vector). "
        "A skip here is not a pass; CI must set it. ⚠️ Do NOT export it as "
        "DATABASE_URL — see tests/conftest.py."
    ),
)


@pytest.fixture(scope="module")
def replayed():
    engine = create_engine(_URL, future=True)
    with engine.begin() as connection:
        apply_ladder(connection)
    yield engine
    engine.dispose()


@pytest.fixture
def conn(replayed):
    """One rolled-back transaction per test — writes never outlive a test."""
    with replayed.connect() as connection:
        trans = connection.begin()
        try:
            yield connection
        finally:
            trans.rollback()


@pytest.fixture
def master_key(monkeypatch):
    monkeypatch.setenv("ACB_MASTER_KEY", "test-master-key-for-mt1j-slice5")
    from acb_common.settings import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _LadderStore:
    """``ProviderKeyStore`` with its transport pointed at the test connection.

    ⚠️ **The SQL is the production module's, byte for byte** — only
    ``_execute``'s connection changes, from "open a psycopg connection to
    ``settings.database_url``" to "use the connection this test already holds".
    That is what keeps the fixture honest under R8: Postgres parses and plans
    every statement under test, including the ``count(*) = 1`` sub-select and
    the ``(organization_id, provider)`` conflict arbiter, and the rows live
    inside the test's own transaction so nothing survives the rollback.

    ``:param`` is SQLAlchemy's native bind syntax, so the statement passes
    through unrewritten; production's ``:x`` → ``%(x)s`` pass exists only
    because it talks to psycopg directly.
    """

    def __new__(cls, connection):
        from acb_llm.key_store import ProviderKeyStore

        store = ProviderKeyStore()
        store._execute = _make_execute(connection)  # type: ignore[method-assign]
        return store


def _make_execute(connection):
    async def _execute(sql: str, **params):
        result = connection.execute(text(sql), params)
        if not result.returns_rows:
            return []
        return [dict(row) for row in result.mappings()]
    return _execute


def _default_org(connection) -> str:
    """The ladder's own seeded organization — the *operator's*.

    Named here rather than inlined because the fail-closed tests below only
    mean anything if this organization HOLDS the credential an untenanted read
    might wrongly be served. That is MT-0d's stated leak in one line:
    *"a default-org fallback would keep answering after tenant #2 and would
    serve the operator's keys to a customer."*
    """
    return str(
        connection.execute(
            text("SELECT id FROM organization WHERE slug = :slug"),
            {"slug": "default"},
        ).scalar_one()
    )


def _provision(connection, slug: str, name: str) -> str:
    return str(
        connection.execute(
            text("SELECT provision_organization(:slug, :name, NULL)"),
            {"slug": slug, "name": name},
        ).scalar_one()
    )


@_DB_GATE
class TestTwoOrganizationsResolveTheirOwnCredentials:
    """MT-1j slice 5's done-when, against the schema the ladder produces.

    ``provision_organization`` is migration 179's (slice 3) — the two
    organizations are created the way the product creates them, not by a
    hand-written INSERT that could disagree with it.
    """

    def test_each_org_reads_its_own_provider_key_and_neither_reads_the_others(
        self, conn, master_key
    ):
        import asyncio

        org_a = _provision(conn, "mt1j-s5-alpha", "Alpha")
        org_b = _provision(conn, "mt1j-s5-beta", "Beta")
        assert org_a != org_b

        store = _LadderStore(conn)

        async def _drive():
            # Written through the store's OWN write path, so the encryption,
            # the composite conflict target and the cache all take part.
            await store.put("openai", "sk-alpha-secret", organization_id=org_a)
            await store.put("openai", "sk-beta-secret", organization_id=org_b)
            return (
                await store.get("openai", organization_id=org_a),
                await store.get("openai", organization_id=org_b),
                await store.get_all(organization_id=org_a),
                await store.get_all(organization_id=org_b),
            )

        a_key, b_key, a_all, b_all = asyncio.run(_drive())

        assert a_key == "sk-alpha-secret"
        assert b_key == "sk-beta-secret"
        assert a_all == {"openai": "sk-alpha-secret"}
        assert b_all == {"openai": "sk-beta-secret"}

    def test_integration_credentials_are_org_scoped_on_listing_and_delete(
        self, conn, master_key
    ):
        """The `/integrations/keys` arm of the store — round 2's own surface.

        `get_by_type` and `delete` are not covered by the `get`/`get_all` pair
        above. `get_by_type` stacks a `credential_type` predicate on top of
        `organization_id`, and `delete` is the only DESTRUCTIVE method in the
        set — and the one whose untenanted arm returns *silently*
        (`key_store.delete_no_tenant`) rather than raising, so a delete that
        resolved the wrong organization would destroy another tenant's
        credential and report `{"deleted": true}` either way.

        **Three seeds, and each one answers a different mutation — do not
        "simplify" them away:**

        - `openai` in A (an ``llm`` credential) makes the ``credential_type``
          predicate load-bearing: drop it and A's integration listing carries
          `openai`, so `a_before` fails.
        - `clickup:token` in **B alone** makes the ``organization_id`` predicate
          load-bearing *on the listing itself*: drop it from `get_by_type` and
          B's credential appears in A's listing, so `a_before` fails.
          ⚠️ Measured 2026-08-19: while both organizations held only the
          *same-named* `zoho-crm:client_id`, that same mutation left both
          `*_before` assertions **green** — the unscoped rows collapsed onto one
          dict key and `_decrypt_rows`' write-through cache re-served the
          caller's own value — and only the post-delete pair went red. A
          credential one organization holds and the other does not cannot
          collapse.
        - `zoho-crm:client_id` in **both** is what makes the delete half a
          *cross-tenant* test: drop ``organization_id`` from `delete`'s
          ``WHERE`` and B's row goes with A's, which `b_after` catches.
        """
        import asyncio

        org_a = _provision(conn, "mt1j-s5-int-a", "Integrations A")
        org_b = _provision(conn, "mt1j-s5-int-b", "Integrations B")
        store = _LadderStore(conn)

        async def _drive():
            for org, tag in ((org_a, "alpha"), (org_b, "beta")):
                await store.put(
                    "zoho-crm:client_id",
                    f"cid-{tag}",
                    credential_type="integration",
                    service="zoho-crm",
                    organization_id=org,
                )
            # Held by B and by nobody else: an unscoped listing cannot hide it
            # behind a shared provider name or behind A's own cache entry.
            await store.put(
                "clickup:token",
                "tok-beta",
                credential_type="integration",
                service="clickup",
                organization_id=org_b,
            )
            await store.put("openai", "sk-alpha", organization_id=org_a)
            before = (
                await store.get_by_type("integration", organization_id=org_a),
                await store.get_by_type("integration", organization_id=org_b),
            )
            await store.delete("zoho-crm:client_id", organization_id=org_a)
            after = (
                await store.get_by_type("integration", organization_id=org_a),
                await store.get_by_type("integration", organization_id=org_b),
            )
            return before, after

        (a_before, b_before), (a_after, b_after) = asyncio.run(_drive())

        _b_integrations = {
            "zoho-crm:client_id": "cid-beta",
            "clickup:token": "tok-beta",
        }

        assert a_before == {"zoho-crm:client_id": "cid-alpha"}, (
            "organization A's integration listing carried a credential A does "
            "not hold — GET /integrations/keys is org-scoped or it serves "
            "another tenant's credentials"
        )
        assert b_before == _b_integrations
        assert a_after == {}
        assert b_after == _b_integrations, (
            "deleting an integration credential in one organization removed "
            "the other organization's row — DELETE /integrations/keys is "
            "org-scoped or it is a cross-tenant destructive write"
        )

    def test_the_untenanted_read_returns_nothing_with_three_orgs_present(
        self, conn, master_key
    ):
        """The fail-closed arm, on a real database rather than a fake.

        ``test_mt0d_per_org_credentials.py:163`` pins this against an
        in-memory table that mirrors the predicate; here Postgres evaluates
        ``(SELECT count(*) FROM organization) = 1`` for real, against the
        ladder's own ``default`` row plus the two just provisioned.

        ⚠️ **The operator's organization must HOLD the key**, or this test
        passes for the wrong reason. Measured: with no ``default`` key, swapping
        the predicate for ``WHERE slug = 'default'`` — D33.3's predicted wrong
        turn — left this assertion GREEN, because the fallback resolved to an
        organization that happened to have nothing stored. Seeding the operator
        first is what makes the leak visible rather than merely absent.
        """
        import asyncio

        operator = _default_org(conn)
        org_a = _provision(conn, "mt1j-s5-gamma", "Gamma")
        _provision(conn, "mt1j-s5-delta", "Delta")
        assert conn.execute(
            text("SELECT count(*) FROM organization")
        ).scalar_one() >= 2

        store = _LadderStore(conn)

        async def _drive():
            await store.put(
                "openai", "sk-OPERATOR-must-not-leak", organization_id=operator
            )
            await store.put("openai", "sk-gamma", organization_id=org_a)
            return await store.get("openai")

        assert asyncio.run(_drive()) == "", (
            "an untenanted read answered while more than one organization "
            "exists — the MT-0d fail-closed contract has been softened, and "
            "what it served is the operator's key"
        )

    def test_the_untenanted_write_raises_with_more_than_one_org(
        self, conn, master_key
    ):
        import asyncio

        _provision(conn, "mt1j-s5-epsilon", "Epsilon")
        store = _LadderStore(conn)

        with pytest.raises(RuntimeError, match="requires an organization_id"):
            asyncio.run(store.put("openai", "sk-nobody"))


@_DB_GATE
class TestTwoOrganizationsResolveTheirOwnModelConfig:
    """The other half of slice 5's defect: tier overrides per organization.

    ``model_config`` opens its own psycopg connection, so the production
    functions are pointed at the connection this test holds — same discipline
    as ``_LadderStore``: real SQL, real Postgres, inside the rollback.
    """

    @staticmethod
    def _patch(monkeypatch, connection):
        import psycopg

        raw = connection.connection.dbapi_connection

        class _Shared:
            """The live psycopg connection, with lifecycle calls neutered.

            ``model_config`` uses ``with psycopg.connect(...) as conn`` — whose
            ``__exit__`` commits and closes. Both must be no-ops here or the
            test's rollback would have nothing left to roll back.
            """

            def cursor(self):
                return raw.cursor()

            def commit(self):
                return None

            def close(self):
                return None

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _Shared())

    def test_each_org_reads_its_own_tier_overrides(self, conn, monkeypatch):
        from acb_llm.model_config import load_blob, save_blob

        org_a = _provision(conn, "mt1j-s5-mc-a", "MC A")
        org_b = _provision(conn, "mt1j-s5-mc-b", "MC B")
        self._patch(monkeypatch, conn)

        save_blob(
            "tier_overrides",
            {"model_list": [{"model_name": "tier-fast", "owner": "a"}]},
            organization_id=org_a,
        )
        save_blob(
            "tier_overrides",
            {"model_list": [{"model_name": "tier-fast", "owner": "b"}]},
            organization_id=org_b,
        )

        assert load_blob("tier_overrides", organization_id=org_a) == {
            "model_list": [{"model_name": "tier-fast", "owner": "a"}]
        }
        assert load_blob("tier_overrides", organization_id=org_b) == {
            "model_list": [{"model_name": "tier-fast", "owner": "b"}]
        }

    def test_an_untenanted_read_returns_the_default_with_two_orgs(
        self, conn, monkeypatch
    ):
        from acb_llm.model_config import load_blob, save_blob

        operator = _default_org(conn)
        org_a = _provision(conn, "mt1j-s5-mc-c", "MC C")
        _provision(conn, "mt1j-s5-mc-d", "MC D")
        self._patch(monkeypatch, conn)

        # The operator holds a blob too — see the key_store twin above for why
        # an empty `default` would make this assertion vacuous.
        save_blob(
            "tier_overrides",
            {"model_list": ["OPERATOR-must-not-leak"]},
            organization_id=operator,
        )
        save_blob("tier_overrides", {"model_list": ["a"]}, organization_id=org_a)

        assert load_blob("tier_overrides", default="sentinel") == "sentinel", (
            "an untenanted model_config read answered while more than one "
            "organization exists — this is the half that silently loses a "
            "tenant's tier overrides, and it must fail closed"
        )

    def test_an_untenanted_write_raises_with_two_orgs(self, conn, monkeypatch):
        from acb_llm.model_config import save_blob

        _provision(conn, "mt1j-s5-mc-e", "MC E")
        self._patch(monkeypatch, conn)

        with pytest.raises(RuntimeError, match="requires an organization_id"):
            save_blob("tier_overrides", {"model_list": []})


def test_this_suite_is_named_in_the_ci_skip_guard():
    """Its own removal from CI must fail here.

    Slice 2's deviation box records why: ``test_owner_bootstrap.py`` gated on a
    variable no CI job set, so five fences skipped in every run ever made while
    the statement beneath them raised on every call. A fence that never
    executes is not a fence.
    """
    workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
        encoding="utf-8"
    )
    # ⚠️ Match the pytest ARGUMENT, not a mention. The comment block above
    # that step names this file too, so a substring search stayed green with
    # the invocation line deleted — measured while mutation-proving this fence,
    # and it is the same "green because something else matched" shape the
    # skip guard itself exists to catch.
    invoked = re.search(
        r"^\s*tests/unit/test_credential_tenant_threading\.py\s*\\\s*$",
        workflow,
        re.MULTILINE,
    )
    assert invoked, (
        "this suite is no longer an argument of pr-check.yml's R8 platform "
        "step, so its two-org half would skip unnoticed"
    )
    assert "TENANT_LADDER_DATABASE_URL unset" in workflow, (
        "the skip-guard grep line this suite's gate reuses has been removed"
    )
