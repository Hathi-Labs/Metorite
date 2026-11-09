"""Catalog health: which bound steps cannot work, why, and the fix (MS-1).

Spec: ``project-docs/specs/customer_console.md`` §6A.18 "Health rules".
Fences: ``tests/unit/test_catalog_health_r8.py`` (R8, real database) and
``tests/unit/test_provider_registry.py``.

**Pure functions.** The caller reads the in-force chains, the capability rows
and the PLATFORM keys in one transaction, and hands them in. Nothing here
reads a database, a secret or the network. So ``GET /catalog/health`` and
``POST /catalog/health/fix`` judge one chain by one rule set.

The rules, in the order the fixes compose (H2, then H4, then H1 and H3):

* **H2.** The provider id is neither a native vendor nor a litellm provider.
  The fix is the alias target (``sarvam.ai`` becomes ``sarvam``).
* **H4.** A native handler refuses the model id. The fix is the registry
  default for that provider and job. An id with no mode is NOT an entry by
  itself, because the handler adds its default mode. A fix still writes the
  registry id that names the mode.
* **H1.** The verb does not fit the job (``provider_registry.JOB_VERBS``), or
  the pair fails a ``catalog`` check that no other rule names.
* **H3.** ``handlers.native_verb_for(prefix, task)`` names a native verb, and
  the declared verb differs.
* **H8.** No live platform key sits under the exact prefix of the model id.
* **H5.** The first ``MAX_CHAIN_ATTEMPTS`` steps that the door counts cannot
  serve. The door counts a step with a key, and with a capability row except
  on ``chat`` and ``vision`` (those doors call ``acompletion`` whatever the
  row says). H5 is a chain line, not a step entry.
* **H6.** A fix is blocked because the key sits under an alias. MS-2 adds
  one credential entry for each platform key under an alias, whether a
  chain uses it or not (:func:`key_entries`), and the button that moves it.
* **H7.** A registry model retires within 30 days, or has retired. The step
  still works, so the window counts it. The fix is the registry successor.

**The guards (MS-2).** :func:`pair_refusal` runs H1 to H4 on one pair at
declare time and at bind time, so the Console refuses a bad pair before it
is written. H8 and H7 never refuse: a key can come after the declare.

🔴 **The exact-key rule.** A fix is offered only when a live PLATFORM key sits
under the EXACT provider string of the fixed model id. The Router matches that
string exactly, so an alias never counts here.

🔴 **A fix writes the working steps first.** A 400 or a 404 is terminal in the
Router, so a dead first step stops the walk. :func:`plan_fix` writes the
working steps in their order, then the dead steps it was not asked to remove.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

from customer_console import catalog, handlers
from customer_console import provider_registry as registry
from customer_console.router import DEFAULT_INVOCATION, MAX_CHAIN_ATTEMPTS

__all__ = [
    "CONNECT_HELP",
    "ChainHealth",
    "Entry",
    "Fix",
    "FixPlan",
    "FixRefused",
    "KeyEntry",
    "PairRefusal",
    "Report",
    "assess",
    "key_entries",
    "pair_refusal",
    "plan_fix",
    "window",
]

#: The order in which an entry picks the rule whose reason it shows. It is
#: the order the fixes compose, then H8. H7 shows only on a step that has no
#: other rule.
_PRECEDENCE = ("H2", "H4", "H1", "H3", "H8")

#: The rules that refuse a pair at declare time and at bind time.
_GUARD_RULES = ("H2", "H4", "H1", "H3")

#: The doors that drop a step for a missing key only (`chat_completions`
#: and the vision try). Every other door drops a step with no capability row
#: too (`_transcribe_attempts`, the decide try, the image, speak and embed
#: doors).
_KEY_ONLY_TASKS = frozenset({"chat", "vision"})

#: What an entry with no fix tells the operator to do (H8).
CONNECT_HELP = "Connect a provider to fix this"


def move_help(target: str) -> str:
    """What an H6 step entry tells the operator to do (MS-2 adds the button)."""
    return f"Move the key to '{target}' to fix this"

_SAME_ABOVE = "Remove: same as the fix above"
_CHAIN_HAS = "Remove: the chain already has this model"

#: The 400 for a fix after which no step of the chain could serve. A dead
#: first step stops the Router's walk, so the Console writes no such chain.
NOTHING_WORKS = "No entry on this job can work. Connect a provider first."


@dataclass(frozen=True)
class Fix:
    """What one entry becomes. ``remove`` drops the step from the chain."""

    action: str
    model: str
    invocation: str
    label: str
    same_as: str | None = None


@dataclass(frozen=True)
class Entry:
    """One bound step that cannot work, or that retires soon (H7)."""

    kind: str
    rules: tuple[str, ...]
    tier: str
    task: str
    rank: int
    model: str
    invocation: str | None
    reason: str
    fix: Fix | None
    help: str | None
    #: True for an H7 entry: the step serves today. The window counts it,
    #: and a fix keeps it as a working step until the operator replaces it.
    works: bool = False
    #: Organizations with their own live key (BYOK) under the exact prefix
    #: of an H8 step. Health counts platform keys only, so the step is dead
    #: to it and alive to them. A fix that drops it moves them to a platform
    #: key, and the wire says so before the write.
    byok_orgs: int = 0


@dataclass(frozen=True)
class ChainHealth:
    """One chain with an entry, and H5's verdict on its first steps."""

    tier: str
    task: str
    entries: int
    window_blocked: bool
    reason: str
    #: H5's fix: the working steps alone. None when no step works.
    fix_chain: tuple[str, ...] | None
    #: The dead steps that BYOK customers still use, with their org counts.
    byok_steps: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class KeyEntry:
    """One live platform key under an alias of a registry provider (H6).

    ``action`` is ``revoke_alias`` when a live platform key already sits
    under ``target``, and ``move_key`` when the key must be copied there.
    """

    provider: str
    target: str
    action: str
    reason: str
    label: str


@dataclass(frozen=True)
class Report:
    entries: tuple[Entry, ...]
    chains: tuple[ChainHealth, ...]
    keys: tuple[KeyEntry, ...] = ()


@dataclass(frozen=True)
class PairRefusal:
    """Why the Console refuses one ``(model, task, verb)`` pair, and the fix."""

    rule: str
    reason: str
    #: ``(model, verb)`` that would work, or None when the registry has none.
    fix: tuple[str, str] | None

    def sentence(self, model: str) -> str:
        """The 400 detail: the reason, then the fix in plain words."""
        if self.fix is None:
            if self.rule == "H2":
                return (
                    f"{self.reason}. Use a provider id that litellm or a Metorite "
                    "handler knows"
                )
            return f"{self.reason}"
        fixed, verb = self.fix
        if fixed == model:
            return f"{self.reason}. Fix: declare it with {verb}"
        return f"{self.reason}. Fix: declare {fixed} with {verb}"


@dataclass(frozen=True)
class FixPlan:
    """What one tier's fix writes, in one transaction."""

    chain: tuple[str, ...]
    #: ``(model, invocation)`` pairs to declare before the chain is written.
    declares: tuple[tuple[str, str], ...]
    replaced: tuple[tuple[str, str], ...]
    removed: tuple[str, ...]


class FixRefused(Exception):
    """A fix the Console refuses. ``status`` is 400 (bad input) or 409 (stale)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def job_label(task: str) -> str:
    return registry.JOB_LABELS.get(task, task)


def model_label(model: str, task: str) -> str | None:
    known = registry.registry_model(model, task)
    return None if known is None else known.label


def _prefix(model: str) -> str:
    return model.partition("/")[0]


# ── One step ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Diagnosis:
    rules: tuple[str, ...]
    #: The ``(model, invocation)`` the rules lead to, before the key rule.
    target: tuple[str, str] | None


def _after_h2(model: str, rules: list[str]) -> str | None:
    """H2: the id with a callable provider, or None when no alias gives one."""
    prefix, sep, rest = model.partition("/")
    if registry.is_callable_provider(prefix):
        return model
    rules.append("H2")
    target = registry.normalise(prefix)
    if sep and rest and target != prefix and registry.is_callable_provider(target):
        return f"{target}/{rest}"
    return None


def _after_h4(model: str, task: str, native: str | None, rules: list[str]) -> str | None:
    """H4: the id a native handler can call, or None when the registry has none."""
    if native is None:
        return model
    if handlers.native_model_refusal(native, model) is not None:
        rules.append("H4")
        default = registry.default_model(_prefix(model), task)
        return None if default is None else default.id
    if registry.registry_model(model, task) is None:
        # An id with no mode works, because the handler adds its default.
        # The fix still writes the curated id, so the mode is on record.
        longer = registry.extension_of(model, task)
        if longer is not None:
            return longer.id
    return model


def _verb_rules(
    model: str, declared: str | None, task: str, native: str | None, rules: list[str]
) -> None:
    """H1 and H3, on the declared verb."""
    if declared is None:
        rules.append("H1")
        return
    if declared not in registry.JOB_VERBS.get(task, frozenset()):
        rules.append("H1")
    if native is not None and declared != native:
        rules.append("H3")
    if rules:
        return
    # A pair that no rule above names, for example a native verb on another
    # vendor's model. The declare checks refuse it, so it cannot work.
    try:
        catalog.check_invocation_for_task(declared, task)
        catalog.check_model_for_invocation(model, declared, task=task)
    except catalog.CatalogRefused:
        rules.append("H1")


def _target_verb(model: str, task: str, declared: str | None, native: str | None) -> str | None:
    """The verb the fix writes: the registry's, the native one, or the job's."""
    known = registry.registry_model(model, task)
    if known is not None:
        return known.invocation
    if native is not None:
        return native
    verbs = registry.JOB_VERBS.get(task, frozenset())
    if declared in verbs and not declared.startswith(catalog.NATIVE_INVOCATION_PREFIX):
        return declared
    if _prefix(model) in registry.litellm_providers():
        return registry.single_litellm_verb(task)
    return None


def _pair_ok(model: str, verb: str, task: str) -> bool:
    """The fix passes the same checks a declare passes."""
    if verb not in registry.JOB_VERBS.get(task, frozenset()):
        return False
    try:
        catalog.check_invocation_for_task(verb, task)
        catalog.check_model_for_invocation(model, verb, task=task)
    except catalog.CatalogRefused:
        return False
    return True


def _diagnose(
    model: str, task: str, declared: str | None, keys: frozenset[str],
    today: date | None = None,
) -> _Diagnosis:
    rules: list[str] = []
    if declared is None and task in _KEY_ONLY_TASKS:
        # The chat and vision doors call `acompletion` with no row.
        declared = DEFAULT_INVOCATION
    normal = _after_h2(model, rules)
    native = handlers.native_verb_for(_prefix(normal), task) if normal else None
    fixed = _after_h4(normal, task, native, rules) if normal else None
    _verb_rules(model, declared, task, native, rules)
    if _prefix(model) not in keys:
        rules.append("H8")
    target: tuple[str, str] | None = None
    if rules and fixed is not None:
        verb = _target_verb(fixed, task, declared, native)
        if verb is not None and (fixed, verb) != (model, declared) and _pair_ok(fixed, verb, task):
            target = (fixed, verb)
    if not rules and today is not None:
        return _retiring(model, task, today)
    ordered = tuple(r for r in _PRECEDENCE if r in rules)
    return _Diagnosis(ordered, target)


def _retiring(model: str, task: str, today: date) -> _Diagnosis:
    """H7: a working registry model that retires within 30 days, or has retired."""
    known = registry.registry_model(model, task)
    if known is None or not registry.is_retiring(known, today):
        return _Diagnosis((), None)
    after = registry.registry_model(known.successor, task) if known.successor else None
    target = None
    if after is not None and _pair_ok(after.id, after.invocation, task):
        target = (after.id, after.invocation)
    return _Diagnosis(("H7",), target)


def _alias_key(provider_id: str, keys: frozenset[str]) -> str | None:
    """A platform key whose provider string is an alias of *provider_id* (H6)."""
    return next(
        (k for k in sorted(keys) if k != provider_id and registry.normalise(k) == provider_id),
        None,
    )


def _reason(rule: str, *, task: str, provider_id: str, model: str = "") -> str:
    job = job_label(task)
    if rule == "H7":
        known = registry.registry_model(model, task)
        when = "soon" if known is None or known.retires is None else known.retires.isoformat()
        return f"This model retires on {when}"
    return {
        "H1": f"This model needs a different call type for {job}",
        "H2": f"No key or handler matches '{provider_id}'",
        "H3": "This model needs Metorite's own handler",
        "H4": f"Metorite cannot call this model id for {job}",
        "H8": f"No key for '{provider_id}'",
    }[rule]


def _entry(
    *, tier: str, task: str, rank: int, model: str, declared: str | None,
    diag: _Diagnosis, keys: frozenset[str], byok: Mapping[str, int],
) -> Entry:
    fix: Fix | None = None
    missing: str | None = None
    if diag.target is not None:
        fixed, verb = diag.target
        if _prefix(fixed) in keys:
            fix = Fix("replace", fixed, verb, f"Use {model_label(fixed, task) or fixed}")
        else:
            missing = _prefix(fixed)
    elif diag.rules[0] == "H8":
        missing = _prefix(model)

    kind = diag.rules[0]
    reason = _reason(kind, task=task, provider_id=_prefix(model), model=model)
    help_text: str | None = None if fix is not None else CONNECT_HELP
    if missing is not None:
        alias = _alias_key(missing, keys)
        kind = "H6" if alias is not None else "H8"
        reason = (
            f"The key is saved as '{alias}'"
            if alias is not None
            else _reason("H8", task=task, provider_id=missing)
        )
        if alias is not None:
            help_text = move_help(missing)
    works = diag.rules == ("H7",)
    return Entry(
        kind=kind, rules=diag.rules, tier=tier, task=task, rank=rank, model=model,
        invocation=declared, reason=reason, fix=fix,
        help=None if works else help_text,
        works=works,
        byok_orgs=byok.get(_prefix(model), 0) if diag.rules == ("H8",) else 0,
    )


def _dedupe(entries: list[Entry], working: set[str]) -> list[Entry]:
    """Two entries that map to one fix count as one fix (the second removes).

    A fix whose model already serves in the chain removes the entry. It never
    writes a second copy of that model, and it never moves it.
    """
    first: dict[str, str] = {}
    out: list[Entry] = []
    for e in entries:
        if e.fix is None or e.fix.action != "replace":
            out.append(e)
        elif e.fix.model in working:
            out.append(replace(e, fix=replace(
                e.fix, action="remove", label=_CHAIN_HAS, same_as=e.fix.model)))
        elif e.fix.model in first:
            out.append(replace(e, fix=replace(
                e.fix, action="remove", label=_SAME_ABOVE, same_as=first[e.fix.model])))
        else:
            first[e.fix.model] = e.model
            out.append(e)
    return out


# ── One chain, then all of them ──────────────────────────────────────────────


def _counted(task: str, models: Sequence[str], capable: set[str], keys: frozenset[str]) -> list[str]:
    """The steps the door tries, in order, capped as the door caps them."""
    counted = [
        m for m in models
        if _prefix(m) in keys and (task in _KEY_ONLY_TASKS or m in capable)
    ]
    return counted[:MAX_CHAIN_ATTEMPTS]


def window(
    task: str, models: Sequence[str], capable: set[str], keys: frozenset[str]
) -> list[str]:
    """The steps the door tries, as the doors count them (§6A.18 "The window").

    Adopt reads it to name a working step that a prepend pushes past the
    third place in that count.
    """
    return _counted(task, models, capable, keys)


def _assess_chain(
    tier: str,
    task: str,
    models: Sequence[str],
    capabilities: Mapping[tuple[str, str], str],
    keys: frozenset[str],
    *,
    byok: Mapping[str, int],
    today: date | None,
) -> tuple[list[Entry], ChainHealth | None]:
    entries: list[Entry] = []
    working: set[str] = set()
    for rank, model in enumerate(models, start=1):
        declared = capabilities.get((model, task))
        diag = _diagnose(model, task, declared, keys, today)
        if not diag.rules or diag.rules == ("H7",):
            working.add(model)
        if not diag.rules:
            continue
        entries.append(_entry(
            tier=tier, task=task, rank=rank, model=model, declared=declared,
            diag=diag, keys=keys, byok=byok,
        ))
    entries = _dedupe(entries, working)

    capable = {m for m in models if (m, task) in capabilities}
    counted = _counted(task, models, capable, keys)
    blocked = not any(m in working for m in counted)
    if not entries and not blocked:
        return entries, None
    count = len(entries)
    dead = sum(1 for e in entries if not e.works)
    if dead or blocked:
        noun = "entry" if dead == 1 else "entries"
        reason = f"{dead} {noun} on {job_label(task)} cannot work"
    else:
        noun = "entry on" if count == 1 else "entries on"
        verb = "retires" if count == 1 else "retire"
        reason = f"{count} {noun} {job_label(task)} {verb} soon"
    alive = tuple(m for m in models if m in working)
    return entries, ChainHealth(
        tier=tier,
        task=task,
        entries=count,
        window_blocked=blocked,
        reason=reason,
        fix_chain=alive if blocked and alive else None,
        byok_steps=tuple((e.model, e.byok_orgs) for e in entries if e.byok_orgs),
    )


def assess(
    chains: Mapping[tuple[str, str], Sequence[str]],
    capabilities: Mapping[tuple[str, str], str],
    keys: frozenset[str],
    *,
    byok: Mapping[str, int] | None = None,
    today: date | None = None,
) -> Report:
    """Judge every in-force chain.

    *chains* maps ``(tier, task)`` to its models in rank order (no tombstone).
    *capabilities* maps ``(model, task)`` to the declared verb. *keys* holds
    the provider string of each live PLATFORM credential, exactly as stored.
    *byok* maps a provider string to the count of organizations with a live
    key of their own under it (MS-2). *today* turns on rule H7.
    """
    entries: list[Entry] = []
    reports: list[ChainHealth] = []
    for (tier, task) in sorted(chains):
        found, chain = _assess_chain(
            tier, task, chains[(tier, task)], capabilities, keys,
            byok=byok or {}, today=today,
        )
        entries.extend(found)
        if chain is not None:
            reports.append(chain)
    return Report(entries=tuple(entries), chains=tuple(reports), keys=key_entries(keys))


def key_entries(keys: frozenset[str]) -> tuple[KeyEntry, ...]:
    """One entry for each live platform key under an alias (H6, MS-2).

    It needs no chain. A key that no chain uses still sits under the wrong
    string, and the next adopt or fix of that provider would not find it.
    """
    out: list[KeyEntry] = []
    for key in sorted(keys):
        target = registry.alias_target(key)
        if target is None:
            continue
        if target in keys:
            out.append(KeyEntry(
                provider=key, target=target, action="revoke_alias",
                reason=f"The key is saved as '{key}', and a key under '{target}' exists too",
                label=f"Revoke the key under '{key}'",
            ))
        else:
            out.append(KeyEntry(
                provider=key, target=target, action="move_key",
                reason=f"The key is saved as '{key}'",
                label=f"Move the key to '{target}'",
            ))
    return tuple(out)


def pair_refusal(model: str, task: str, invocation: str) -> PairRefusal | None:
    """Rules H1 to H4 on one pair, at declare time and at bind time (MS-2).

    Task 2457 found that the declare checked the shape of the verb only, so
    ``sarvam.ai/saaras:v4`` with ``acompletion`` on ``transcribe`` went in
    and failed at the first customer call. This judges the pair by the rules
    the health list uses. H8 (no key) never refuses: the key can come later.
    The fix ignores the key rule too, because a declare is not a chain.
    """
    prefix = _prefix(model)
    diag = _diagnose(model, task, invocation, frozenset({prefix}))
    rules = [r for r in diag.rules if r in _GUARD_RULES]
    if not rules:
        return None
    return PairRefusal(
        rule=rules[0],
        reason=_reason(rules[0], task=task, provider_id=prefix, model=model),
        fix=diag.target,
    )


# ── The fix ──────────────────────────────────────────────────────────────────


def _check_picks(
    tier: str, models: Sequence[str], picked: Sequence[str], by_model: Mapping[str, Entry]
) -> None:
    for model in picked:
        if model not in models:
            raise FixRefused(
                409, f"'{model}' is not a step of {tier} now. Read the health list again"
            )
        entry = by_model.get(model)
        if entry is None:
            raise FixRefused(
                409, f"'{model}' on {tier} has no entry now. Read the health list again"
            )
        if entry.fix is None:
            raise FixRefused(400, f"'{model}' on {tier} has no fix: {entry.reason}")


def plan_fix(
    report: Report,
    *,
    tier: str,
    task: str,
    models: Sequence[str],
    picked: Sequence[str | None],
) -> FixPlan:
    """The chain one tier's fix writes. *picked* names entries by model.

    ``None`` in *picked* selects H5's chain fix: drop every step that still
    cannot work after the step fixes. It is legal only when the chain line
    has a fix. Raises :class:`FixRefused`.

    🔴 **No fix writes a chain in which no step works.** A dead first step
    stops the Router's walk, so such a chain serves nobody. The answer is a
    400 (:data:`NOTHING_WORKS`), and nothing is written.
    """
    by_model = {e.model: e for e in report.entries if e.tier == tier and e.task == task}
    steps = [m for m in picked if m is not None]
    _check_picks(tier, models, steps, by_model)
    chain_fix = None in picked
    if chain_fix:
        line = next((c for c in report.chains if c.tier == tier and c.task == task), None)
        if line is None:
            raise FixRefused(409, f"{tier} has no entry now. Read the health list again")
        if line.fix_chain is None:
            raise FixRefused(400, f"the chain line of {tier} has no fix: {line.reason}")

    working = {m for m in models if m not in by_model or by_model[m].works}
    new: list[str] = []
    declares: list[tuple[str, str]] = []
    replaced: list[tuple[str, str]] = []
    removed: list[str] = []
    for model in models:
        entry = by_model.get(model)
        if entry is None or entry.fix is None or model not in steps:
            new.append(model)
        elif entry.fix.action == "replace":
            new.append(entry.fix.model)
            declares.append((entry.fix.model, entry.fix.invocation))
            replaced.append((model, entry.fix.model))
            working.add(entry.fix.model)
        else:
            removed.append(model)

    chain = list(dict.fromkeys(new))
    if not any(m in working for m in chain):
        # 🔴 No step would serve, so the first step is dead whatever the
        # order. Write nothing.
        raise FixRefused(400, NOTHING_WORKS)
    if chain_fix:
        removed.extend(m for m in chain if m not in working)
        chain = [m for m in chain if m in working]
    chain = [m for m in chain if m in working] + [m for m in chain if m not in working]
    if tuple(chain) == tuple(models) and not declares:
        raise FixRefused(400, f"the fix changes nothing on {tier}")
    # A model the chain keeps is never "removed" (MS-1 verifier, P3). In
    # [nano, /en] with /en on the wrong verb, the /en entry is "same as the
    # fix above". A pick of both writes [/en], so the response and the audit
    # row must not say that /en went.
    return FixPlan(
        chain=tuple(chain),
        declares=tuple(dict.fromkeys(declares)),
        replaced=tuple(replaced),
        removed=tuple(m for m in dict.fromkeys(removed) if m not in chain),
    )


def entry_wire(e: Entry) -> dict[str, object]:
    """One entry as ``GET /catalog/health`` returns it."""
    return {
        "kind": e.kind,
        "rules": list(e.rules),
        "tier": e.tier,
        "task": e.task,
        "job": job_label(e.task),
        "rank": e.rank,
        "model": e.model,
        "model_label": model_label(e.model, e.task),
        "invocation": e.invocation,
        "reason": e.reason,
        "fix": None if e.fix is None else {
            "action": e.fix.action,
            "model": e.fix.model,
            "invocation": e.fix.invocation,
            "label": e.fix.label,
            "same_as": e.fix.same_as,
        },
        "help": e.help,
        "byok_orgs": e.byok_orgs,
        "byok_note": _byok_note(e.byok_orgs),
    }


def _byok_note(orgs: int) -> str | None:
    """The sentence the screen shows before a fix that drops a BYOK step."""
    if not orgs:
        return None
    noun = "organization uses its" if orgs == 1 else "organizations use their"
    return (
        f"{orgs} {noun} own key for this model. A fix that drops it sends "
        "them to a platform key."
    )


def key_wire(k: KeyEntry) -> dict[str, object]:
    """One H6 credential entry as ``GET /catalog/health`` returns it."""
    return {
        "kind": "H6",
        "provider": k.provider,
        "target": k.target,
        "reason": k.reason,
        "fix": {"action": k.action, "label": k.label},
    }


def chain_wire(c: ChainHealth) -> dict[str, object]:
    """One chain line (H5) as ``GET /catalog/health`` returns it."""
    return {
        "kind": "H5",
        "tier": c.tier,
        "task": c.task,
        "job": job_label(c.task),
        "entries": c.entries,
        "window_blocked": c.window_blocked,
        "reason": c.reason,
        "fix": None if c.fix_chain is None else {"action": "reorder", "chain": list(c.fix_chain)},
        "byok": [{"model": m, "orgs": n} for m, n in c.byok_steps],
    }
