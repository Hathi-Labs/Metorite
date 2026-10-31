"""The command bar's jobs, read from ``shell/<package>.json`` (WS-PKG, PKG-1).

Spec: ``project-docs/specs/package_host.md`` §3.3 and §4.

One source feeds both lists of jobs. The gateway builds ``intent.JOBS`` from
:func:`load_jobs`, and ``scripts/gen_shell_manifests.py`` writes
``workbench/control_plane/src/lib/shell/jobs.generated.ts`` from the same call.
Each file holds the jobs of one owning package, in the spec §3.2 envelope. The
``position`` of a job sets the global order, because the empty bar and the
model prompt both depend on it.

The files are data, so nothing here is code. ``tests/unit/test_shell_manifests.py``
is the fence.
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


def load_jobs(directory: Path | None = None) -> tuple[JobSpec, ...]:
    """Every job of every ``shell/*.json`` file, sorted by ``position``."""
    folder = directory or SHELL_DIR
    jobs: list[JobSpec] = []
    for path in sorted(folder.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or set(doc) - _ENVELOPE_KEYS:
            raise ShellManifestError(f"{path.name}: unknown or missing envelope keys")
        if doc.get("studio") != 1:
            raise ShellManifestError(f"{path.name}: studio must be 1")
        if doc.get("id") != path.stem:
            raise ShellManifestError(f"{path.name}: id must equal the file name")
        contributes = doc.get("contributes")
        if not isinstance(contributes, dict) or set(contributes) != {"jobs"}:
            raise ShellManifestError(f"{path.name}: contributes must hold only jobs")
        raw_jobs = contributes["jobs"]
        if not isinstance(raw_jobs, list):
            raise ShellManifestError(f"{path.name}: jobs must be a list")
        jobs.extend(_job(raw, path.name) for raw in raw_jobs)
    for what, values in (("id", [j.id for j in jobs]), ("position", [j.position for j in jobs])):
        seen: set[Any] = set()
        for v in values:
            if v in seen:
                raise ShellManifestError(f"duplicate job {what}: {v!r}")
            seen.add(v)
    return tuple(sorted(jobs, key=lambda j: j.position))
