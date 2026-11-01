"""The command bar's jobs and the shell's vocabulary, read from ``shell/<package>.json`` (WS-PKG, PKG-1).

Spec: ``project-docs/specs/package_host.md`` §3.3 and §4.

One source feeds both lists of jobs. The gateway builds ``intent.JOBS`` from
:func:`load_jobs`, and ``scripts/gen_shell_manifests.py`` writes
``workbench/control_plane/src/lib/shell/jobs.generated.ts`` from the same call.
Each file holds the jobs of one owning package, in the spec §3.2 envelope. The
``position`` of a job sets the global order, because the empty bar and the
model prompt both depend on it.

PKG-2 adds :func:`load_vocabulary`: the pane links, the preset ids and the card
keys (spec §3.3). PKG-3a adds :func:`load_gates`: the search scope map and the
``ALSO_SERVES`` map. PKG-3b adds two more maps to :class:`Gates`: ``href_features``
(route to feature slug) and ``always_allowed`` (routes open to every signed-in
member). The ``always_allowed`` match is EXACT, never a prefix. A path under an
entry is NOT allowed. PKG-5b adds :func:`load_contributes`: the keys that the
system packages hold at ``jobs``, ``nav`` and ``cards``, each with its feature.
The install store checks a claim against them (spec §4). The files are data, so
nothing here is code. ``tests/unit/test_shell_manifests.py`` is the fence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SHELL_DIR = Path(__file__).parent / "shell"

_ENVELOPE_KEYS = {"studio", "kind", "id", "contributes"}
_REQUIRED = {"id", "label", "position", "words", "icon", "app", "href", "meaning"}
_OPTIONAL = {"fields", "feature", "bar_feature", "admin", "fallback", "note"}


class ShellManifestError(ValueError):
    """A shell manifest file is malformed."""


@dataclass(frozen=True)
class JobSpec:
    """One job, with the fields that the Python and TypeScript readers use."""

    id: str
    label: str
    position: int
    words: tuple[str, ...]
    icon: str
    app: str
    href: str
    meaning: str
    fields: dict[str, str] = field(default_factory=dict)
    feature: str | None = None
    bar_feature: str | None = None
    admin: bool = False
    fallback_feature: str | None = None
    fallback_href: str | None = None
    note: str = ""


def _str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShellManifestError(f"{where} must be a non-empty string")
    return value


def _job(raw: Any, source: str) -> JobSpec:
    if not isinstance(raw, dict):
        raise ShellManifestError(f"{source}: a job must be an object")
    name = raw.get("id", "?")
    where = f"{source}: job {name!r}"
    unknown = set(raw) - _REQUIRED - _OPTIONAL
    if unknown:
        raise ShellManifestError(f"{where}: unknown key(s) {sorted(unknown)}")
    missing = _REQUIRED - set(raw)
    if missing:
        raise ShellManifestError(f"{where}: missing key(s) {sorted(missing)}")
    position = raw["position"]
    if isinstance(position, bool) or not isinstance(position, int):
        raise ShellManifestError(f"{where}: position must be an int")
    words = raw["words"]
    if not isinstance(words, list) or not words:
        raise ShellManifestError(f"{where}: words must be a non-empty list")
    clean_words = tuple(_str(w, f"{where}: a word") for w in words)
    fields = raw.get("fields", {})
    if not isinstance(fields, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in fields.items()
    ):
        raise ShellManifestError(f"{where}: fields must map names to hints")
    admin = raw.get("admin", False)
    if not isinstance(admin, bool):
        raise ShellManifestError(f"{where}: admin must be a bool")
    fallback = raw.get("fallback")
    fb_feature = fb_href = None
    if fallback is not None:
        if not isinstance(fallback, dict) or set(fallback) != {"feature", "href"}:
            raise ShellManifestError(f"{where}: fallback needs exactly feature and href")
        fb_feature = _str(fallback["feature"], f"{where}: fallback.feature")
        fb_href = _str(fallback["href"], f"{where}: fallback.href")
    feature = raw.get("feature")
    bar_feature = raw.get("bar_feature")
    for key, value in (("feature", feature), ("bar_feature", bar_feature)):
        if value is not None:
            _str(value, f"{where}: {key}")
    note = raw.get("note", "")
    if not isinstance(note, str):
        raise ShellManifestError(f"{where}: note must be a string")
    return JobSpec(
        id=_str(raw["id"], f"{where}: id"),
        label=_str(raw["label"], f"{where}: label"),
        position=position,
        words=clean_words,
        icon=_str(raw["icon"], f"{where}: icon"),
        app=_str(raw["app"], f"{where}: app"),
        href=_str(raw["href"], f"{where}: href"),
        meaning=_str(raw["meaning"], f"{where}: meaning"),
        fields=dict(fields),
        feature=feature,
        bar_feature=bar_feature,
        admin=admin,
        fallback_feature=fb_feature,
        fallback_href=fb_href,
        note=note,
    )


_CONTRIBUTES_KEYS = {
    "jobs", "nav", "presets", "cards", "scope", "also_serves", "gates", "always_allowed",
}
#: Presets and cards belong to the home page, so only this file may name them.
_HOME_ONLY = {"presets", "cards"}
_HOME_FILE = "home"


@dataclass(frozen=True)
class Vocabulary:
    """The names the gateway checks and the workbench draws (spec §3.3)."""

    panes: tuple[str, ...]
    presets: tuple[str, ...]
    cards: tuple[str, ...]


def _read(folder: Path) -> list[tuple[str, dict[str, Any]]]:
    """Each ``*.json`` file as ``(name, contributes)``, after the envelope checks."""
    out: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(folder.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or set(doc) - _ENVELOPE_KEYS:
            raise ShellManifestError(f"{path.name}: unknown or missing envelope keys")
        if doc.get("studio") != 1:
            raise ShellManifestError(f"{path.name}: studio must be 1")
        if doc.get("id") != path.stem:
            raise ShellManifestError(f"{path.name}: id must equal the file name")
        contributes = doc.get("contributes")
        if (
            not isinstance(contributes, dict)
            or not contributes
            or set(contributes) - _CONTRIBUTES_KEYS
        ):
            raise ShellManifestError(
                f"{path.name}: contributes must hold only {sorted(_CONTRIBUTES_KEYS)}"
            )
        wrong = set(contributes) & _HOME_ONLY
        if wrong and path.stem != _HOME_FILE:
            raise ShellManifestError(f"{path.name}: {sorted(wrong)} are allowed in home.json only")
        for key, value in contributes.items():
            if not isinstance(value, list):
                raise ShellManifestError(f"{path.name}: {key} must be a list")
        out.append((path.name, contributes))
    return out


def load_jobs(directory: Path | None = None) -> tuple[JobSpec, ...]:
    """Every job of every ``shell/*.json`` file, sorted by ``position``."""
    jobs: list[JobSpec] = []
    for name, contributes in _read(directory or SHELL_DIR):
        jobs.extend(_job(raw, name) for raw in contributes.get("jobs", []))
    for what, values in (("id", [j.id for j in jobs]), ("position", [j.position for j in jobs])):
        seen: set[Any] = set()
        for v in values:
            if v in seen:
                raise ShellManifestError(f"duplicate job {what}: {v!r}")
            seen.add(v)
    return tuple(sorted(jobs, key=lambda j: j.position))


def _names(entries: list[Any], key: str, source: str, kind: str) -> list[str]:
    names: list[str] = []
    for raw in entries:
        if not isinstance(raw, dict) or set(raw) != {key}:
            raise ShellManifestError(f"{source}: a {kind} entry must hold only {key!r}")
        names.append(_str(raw[key], f"{source}: {kind} {key}"))
    return names


def _nav_entries(entries: list[Any], source: str) -> list[tuple[str, str | None]]:
    """Each nav entry as ``(href, feature)``. An entry holds ``href`` and an optional ``feature``."""
    out: list[tuple[str, str | None]] = []
    for raw in entries:
        if not isinstance(raw, dict) or "href" not in raw or set(raw) - {"href", "feature"}:
            raise ShellManifestError(
                f"{source}: a nav entry must hold 'href' and an optional 'feature' only"
            )
        href = _str(raw["href"], f"{source}: nav href")
        feature = _str(raw["feature"], f"{source}: nav feature") if "feature" in raw else None
        out.append((href, feature))
    return out


def load_vocabulary(directory: Path | None = None) -> Vocabulary:
    """The panes, presets and cards of every ``shell/*.json`` file.

    Panes keep file order (by file name, then entry order). Presets and cards
    come from ``home.json`` in its own order. A repeated name is an error.
    """
    panes: list[str] = []
    presets: list[str] = []
    cards: list[str] = []
    for name, contributes in _read(directory or SHELL_DIR):
        for href, _feature in _nav_entries(contributes.get("nav", []), name):
            if not href.startswith("/") or "?" in href:
                raise ShellManifestError(f"{name}: nav href {href!r} must be a path with no '?'")
            panes.append(href)
        presets.extend(_names(contributes.get("presets", []), "id", name, "preset"))
        cards.extend(_names(contributes.get("cards", []), "key", name, "card"))
    for what, values in (("pane", panes), ("preset", presets), ("card", cards)):
        if len(set(values)) != len(values):
            dup = sorted({v for v in values if values.count(v) > 1})
            raise ShellManifestError(f"duplicate {what}: {dup}")
    return Vocabulary(tuple(panes), tuple(presets), tuple(cards))


@dataclass(frozen=True)
class Gates:
    """The two cosmetic maps of the shell (spec §3.3, PKG-3a).

    ``scope`` maps a scope href to a search SOURCE key (not a feature slug).
    ``also_serves`` maps an agent to the other panes it serves, in file order.
    ``href_features`` pairs a route with the feature slug that guards it (PKG-3b).
    It comes from the nav ``feature`` of each pane, then from the ``gates`` entries.
    ``always_allowed`` lists the routes any signed-in member may open. The match is
    EXACT, so ``/integrations`` does not allow ``/integrations/x``.
    """

    scope: dict[str, str]
    also_serves: dict[str, tuple[str, ...]]
    href_features: tuple[tuple[str, str], ...] = ()
    always_allowed: tuple[str, ...] = ()


def _pairs(entries: list[Any], keys: tuple[str, str], source: str, kind: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw in entries:
        if not isinstance(raw, dict) or set(raw) != set(keys):
            raise ShellManifestError(f"{source}: a {kind} entry must hold exactly {list(keys)}")
        a = _str(raw[keys[0]], f"{source}: {kind} {keys[0]}")
        b = _str(raw[keys[1]], f"{source}: {kind} {keys[1]}")
        out.append((a, b))
    return out


def _check_href(href: str, source: str, kind: str) -> None:
    if not href.startswith("/") or "?" in href:
        raise ShellManifestError(f"{source}: {kind} href {href!r} must be a path with no '?'")


def load_gates(directory: Path | None = None) -> Gates:
    """The ``scope`` and ``also_serves`` entries of every ``shell/*.json`` file.

    A scope href is unique. An (agent, href) pair is unique. A route has one feature
    at most, whether it comes from a nav entry or from ``gates``. An ``always_allowed``
    route is unique and is matched EXACTLY.
    """
    scope: dict[str, str] = {}
    served: dict[str, list[str]] = {}
    features: dict[str, str] = {}
    allowed: list[str] = []
    for name, contributes in _read(directory or SHELL_DIR):
        for href, feature in _nav_entries(contributes.get("nav", []), name):
            if feature is not None:
                _check_href(href, name, "nav")
                if href in features:
                    raise ShellManifestError(f"duplicate gate href: {href!r}")
                features[href] = feature
        for href, feature in _pairs(contributes.get("gates", []), ("href", "feature"), name, "gates"):
            _check_href(href, name, "gates")
            if href in features:
                raise ShellManifestError(f"duplicate gate href: {href!r}")
            features[href] = feature
        for href in _names(contributes.get("always_allowed", []), "href", name, "always_allowed"):
            _check_href(href, name, "always_allowed")
            if href in allowed:
                raise ShellManifestError(f"duplicate always_allowed href: {href!r}")
            allowed.append(href)
        for href, src in _pairs(contributes.get("scope", []), ("href", "source"), name, "scope"):
            _check_href(href, name, "scope")
            if href in scope:
                raise ShellManifestError(f"duplicate scope href: {href!r}")
            scope[href] = src
        for agent, href in _pairs(
            contributes.get("also_serves", []), ("agent", "href"), name, "also_serves"
        ):
            _check_href(href, name, "also_serves")
            hrefs = served.setdefault(agent, [])
            if href in hrefs:
                raise ShellManifestError(f"duplicate also_serves pair: {(agent, href)!r}")
            hrefs.append(href)
    return Gates(
        scope,
        {a: tuple(h) for a, h in served.items()},
        tuple(features.items()),
        tuple(allowed),
    )


#: The points of a system package that the resolve returns (spec §5.2, PKG-5b).
SYSTEM_POINTS: tuple[str, ...] = ("jobs", "nav", "cards")


@dataclass(frozen=True)
class SystemContribution:
    """One key that a system package holds at one point (spec §4, PKG-5b).

    ``key`` is the job ``id``, the nav ``href`` or the card ``key``.
    ``package`` is the id of the file that holds it. ``feature`` is the
    feature slug of the entry, or None. A card names no feature.
    """

    point: str
    key: str
    package: str
    feature: str | None = None


def load_contributes(
    directory: Path | None = None,
) -> dict[str, tuple[SystemContribution, ...]]:
    """The keys of the system packages at each point of :data:`SYSTEM_POINTS`.

    The jobs keep the order of ``position``, as :func:`load_jobs` does. The
    nav entries keep file order, and the cards keep the order of
    ``home.json``, as :func:`load_vocabulary` does. A key that two entries of
    one point hold is an error.
    """
    jobs: list[tuple[int, SystemContribution]] = []
    nav: list[SystemContribution] = []
    cards: list[SystemContribution] = []
    for name, contributes in _read(directory or SHELL_DIR):
        package = name.removesuffix(".json")
        for raw in contributes.get("jobs", []):
            job = _job(raw, name)
            jobs.append((job.position, SystemContribution("jobs", job.id, package, job.feature)))
        for href, feature in _nav_entries(contributes.get("nav", []), name):
            _check_href(href, name, "nav")
            nav.append(SystemContribution("nav", href, package, feature))
        for key in _names(contributes.get("cards", []), "key", name, "card"):
            cards.append(SystemContribution("cards", key, package))
    out = {
        "jobs": tuple(c for _, c in sorted(jobs, key=lambda pair: pair[0])),
        "nav": tuple(nav),
        "cards": tuple(cards),
    }
    for point, entries in out.items():
        keys = [c.key for c in entries]
        if len(set(keys)) != len(keys):
            dup = sorted({k for k in keys if keys.count(k) > 1})
            raise ShellManifestError(f"duplicate {point} key: {dup}")
    return out
