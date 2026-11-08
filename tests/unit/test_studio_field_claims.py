"""WS-PKG PKG-14a-2 — a Studio manifest makes ``fields`` claims (hermetic).

Spec: ``project-docs/specs/package_host.md`` §8.3 and the PKG-14a-2 row of
§10. Code: ``gateway/package_host/studio_app.py`` (``claims_of`` and
``_field_claims``) and ``packages/acb_common/acb_common/studio_manifest.py``
(``_check_host_fields``). The R8 twin is
``tests/unit/test_studio_field_claims_r8.py``.

What this suite holds:

a. Each entry of ``contributes.fields`` gives one ``fields`` claim, keyed
   ``<record>:<key>``, after the ``nav`` claim and the ``jobs`` claims, in
   the order of the manifest. A manifest with no entry gives none.
b. The payload is the declaration, and ``field_values.declared_field``
   parses it back to the same field. It holds no other key.
c. An unknown record kind, a bad key, a refused type or part, an unknown
   key, a duplicate and more than ``MAX_HOST_FIELDS`` entries raise
   ``ContributionRefused`` (422). ``keep_scope_install`` and
   ``keep_private_install`` raise it before the store runs.
d. The copies in ``studio_manifest.py`` equal the tuples of
   ``field_values.py``.
e. The R8 twin is in both ``--must-run`` lists.

**R7: mutations that go red here.** M1: let ``_field_claims`` take any
record kind. M2: skip the duplicate check. M3: return an empty payload.
"""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import studio_manifest
from gateway.package_host import field_values as fv
from gateway.package_host import store, studio_app
from gateway.package_host.store import ContributionRefused

_ROOT = Path(__file__).resolve().parents[2]
PID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
_WARRANTY = {"record": "task", "key": "warranty_end", "type": "date", "label": "Warranty end"}


def _manifest(*entries: Any, jobs: list[dict[str, str]] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"name": "Field visits",
                           "contributes": {"fields": [copy.deepcopy(e) for e in entries]}}
    if jobs is not None:
        out["shell"] = {"jobs": jobs}
    return out


def _fields(manifest: dict[str, Any]) -> list[store.Contribution]:
    return [c for c in studio_app.claims_of(PID, manifest) if c.point == "fields"]


def _refused(manifest: Any, code: str) -> ContributionRefused:
    with pytest.raises(ContributionRefused) as exc:
        studio_app.claims_of(PID, manifest)
    assert exc.value.code == code, exc.value
    assert exc.value.status == 422
    return exc.value


# ── a. One claim for each entry ─────────────────────────────────────────────


def test_each_declared_field_gives_one_claim_keyed_by_record_and_key() -> None:
    tier = {"record": "crm.deal", "key": "tier", "type": "select", "options": ["Gold", "Silver"]}
    claims = studio_app.claims_of(PID, _manifest(
        _WARRANTY, tier, jobs=[{"verb": "Log", "view": "v"}]))
    assert [(c.point, c.key, c.position) for c in claims] == [
        ("nav", f"/apps/{PID}", 0), ("jobs", f"{PID}:v", 0),
        ("fields", "task:warranty_end", 0), ("fields", "crm.deal:tier", 1)]
    assert claims[2].payload == {"type": "date", "many": False, "label": "Warranty end"}
    assert claims[3].payload == {"type": "select", "many": False, "options": ["Gold", "Silver"]}


def test_each_host_record_kind_takes_a_field() -> None:
    entries = [{**_WARRANTY, "record": kind} for kind in fv.HOST_RECORD_KINDS]
    assert [c.key for c in _fields(_manifest(*entries))] == [
        f"{kind}:warranty_end" for kind in fv.HOST_RECORD_KINDS]


@pytest.mark.parametrize("manifest", [
    {}, {"name": "x"}, {"contributes": {}}, {"contributes": {"nav": []}},
    {"contributes": {"fields": []}}, {"contributes": None}, {"contributes": "x"}])
def test_no_declared_field_gives_no_fields_claim(manifest: dict[str, Any]) -> None:
    assert _fields(manifest) == []


def test_the_order_is_the_order_of_the_manifest_and_repeats() -> None:
    entries = [{**_WARRANTY, "key": f"f{i}"} for i in (3, 1, 2)]
    first = _fields(_manifest(*entries))
    assert [c.key for c in first] == ["task:f3", "task:f1", "task:f2"]
    assert [c.position for c in first] == [0, 1, 2]
    assert _fields(_manifest(*entries)) == first


# ── b. The payload is the declaration ───────────────────────────────────────


@pytest.mark.parametrize("entry", [
    _WARRANTY,
    {"record": "crm.lead", "key": "budget", "type": "money"},
    {"record": "crm.contact", "key": "tags", "type": "multiSelect", "options": ["a", "b"]},
    {"record": "crm.company", "key": "sites", "type": "url", "many": True, "label": "Sites"},
])
def test_the_payload_parses_back_to_the_declared_field(entry: dict[str, Any]) -> None:
    [claim] = _fields(_manifest(entry))
    assert claim.key == fv.claim_key(entry["record"], entry["key"])
    field = fv.declared_field(entry["key"], claim.payload)
    want = fv.declared_field(
        entry["key"], {k: v for k, v in entry.items() if k not in ("record", "key")})
    assert field == want
    assert set(claim.payload) <= {"type", "many", "label", "options"}
    assert claim.payload["type"] == entry["type"]


def test_the_payload_is_what_the_resolve_answers_and_the_door_reads() -> None:
    """The resolve adds four keys to the payload (§5.2). The door takes them
    off and parses the rest with ``declared_field``. So no other key may be
    in the payload, a ``feature`` too, or the door drops the field."""
    [claim] = _fields(_manifest(_WARRANTY))
    entry = {**claim.payload, "key": claim.key, "source": "org", "install_id": "i",
             "needs_setup": False}
    rest = {k: v for k, v in entry.items()
            if k not in ("key", "source", "install_id", "needs_setup")}
    assert fv.declared_field("warranty_end", rest).label == "Warranty end"
    assert "feature" not in claim.payload


# ── c. A bad entry is refused ───────────────────────────────────────────────


@pytest.mark.parametrize("record", ["crm.leads", "Task", "project", "", None, 3, ["task"]])
def test_an_unknown_record_kind_is_refused(record: Any) -> None:
    _refused(_manifest({**_WARRANTY, "record": record}), "bad_record_kind")


@pytest.mark.parametrize("key", ["Warranty", "1st", "a-b", "x" * 49, "", None, "a:b",
                                 "warranty_end\n"])
def test_a_bad_field_key_is_refused(key: Any) -> None:
    _refused(_manifest({**_WARRANTY, "key": key}), "bad_field_key")


@pytest.mark.parametrize("kind", ["relation", "file", "warranty", None])
def test_a_refused_or_unknown_type_is_refused(kind: Any) -> None:
    _refused(_manifest({**_WARRANTY, "type": kind}), "bad_field")


@pytest.mark.parametrize(("part", "value"), [
    ("required", True), ("required", False), ("hot", True), ("to", "visit"),
    ("feature", "studio"), ("key2", 1)])
def test_a_refused_part_or_an_unknown_key_is_refused(part: str, value: Any) -> None:
    _refused(_manifest({**_WARRANTY, part: value}), "bad_field")


def test_a_long_label_and_bad_options_are_refused() -> None:
    _refused(_manifest({**_WARRANTY, "label": "x" * 121}), "bad_field")
    _refused(_manifest({**_WARRANTY, "type": "select"}), "bad_field")
    _refused(_manifest({**_WARRANTY, "type": "select", "options": ["o"] * 101}), "bad_field")


def test_a_duplicate_in_one_manifest_is_refused_and_two_kinds_may_share_a_key() -> None:
    _refused(_manifest(_WARRANTY, {**_WARRANTY, "type": "text"}), "duplicate_field")
    claims = _fields(_manifest(_WARRANTY, {**_WARRANTY, "record": "crm.deal"}))
    assert [c.key for c in claims] == ["task:warranty_end", "crm.deal:warranty_end"]


def test_more_than_the_cap_is_refused() -> None:
    entries = [{**_WARRANTY, "key": f"f{i}"} for i in range(studio_manifest.MAX_HOST_FIELDS)]
    assert len(_fields(_manifest(*entries))) == studio_manifest.MAX_HOST_FIELDS
    _refused(_manifest(*entries, {**_WARRANTY, "key": "one_more"}), "too_many_fields")


@pytest.mark.parametrize(("fields", "code"), [
    ("x", "bad_fields"), ({"a": 1}, "bad_fields"), (["x"], "bad_field"), ([None], "bad_field")])
def test_a_malformed_list_is_refused(fields: Any, code: str) -> None:
    _refused({"contributes": {"fields": fields}}, code)


def test_no_field_claim_can_take_a_key_of_another_point_or_kind() -> None:
    """The key always starts with a known record kind and a colon. So it can
    never equal a nav href, a job key or a field of another kind."""
    entries = [{**_WARRANTY, "record": kind, "key": f"k{i}"}
               for i, kind in enumerate(fv.HOST_RECORD_KINDS)]
    for claim in _fields(_manifest(*entries)):
        kind, _, key = claim.key.partition(":")
        assert kind in fv.HOST_RECORD_KINDS and fv.claim_key(kind, key) == claim.key
        assert not claim.key.startswith("/")


def _fake_store(monkeypatch: Any) -> list[str]:
    calls: list[str] = []

    async def record(*_a: Any, **_k: Any) -> None:
        calls.append("store")

    monkeypatch.setattr(store, "set_version", record)
    monkeypatch.setattr(store, "install", record)
    return calls


def test_a_bad_entry_refuses_each_install_before_the_store(monkeypatch: Any) -> None:
    calls = _fake_store(monkeypatch)
    bad = _manifest({**_WARRANTY, "record": "crm.leads"})
    with pytest.raises(ContributionRefused):
        asyncio.run(studio_app.keep_private_install(
            None, app_id=PID, version=1, owner="ana@x.test", manifest=bad))
    with pytest.raises(ContributionRefused):
        asyncio.run(studio_app.keep_scope_install(
            None, app_id=PID, version=1, scope_kind="org", scope_subject="org",
            member="ana@x.test", manifest=bad))
    assert calls == []


# ── d. The copies equal the tuples of the field store ───────────────────────


def test_the_manifest_copies_equal_the_field_store() -> None:
    assert studio_manifest.HOST_RECORD_KINDS == fv.HOST_RECORD_KINDS
    assert studio_manifest.HOST_FIELD_REFUSED_TYPES == fv.REFUSED_TYPES
    assert frozenset(fv.REFUSED_SPEC_KEYS) == studio_manifest.HOST_FIELD_REFUSED_KEYS
    assert fv.FIELDS_POINT in studio_manifest.CONTRIBUTION_POINTS
    assert fv.FIELDS_POINT not in store.MANY_CLAIMS


def test_the_validator_and_claims_of_refuse_the_same_entries() -> None:
    base = {"studio": 1, "id": "visits", "name": "V", "purpose": "p"}
    cases = [
        {**_WARRANTY}, {**_WARRANTY, "record": "crm.leads"}, {**_WARRANTY, "key": "Bad"},
        {**_WARRANTY, "type": "file"}, {**_WARRANTY, "hot": False}, {**_WARRANTY, "x": 1},
        {**_WARRANTY, "type": "select"}]
    for entry in cases:
        manifest = {**base, "contributes": {"fields": [entry]}}
        valid = studio_manifest.validate_manifest(manifest) == []
        try:
            studio_app.claims_of(PID, manifest)
            took = True
        except ContributionRefused:
            took = False
        assert valid == took, entry


# ── e. The R8 twin runs in CI ───────────────────────────────────────────────


def _must_run(workflow: str) -> list[str]:
    lines = (_ROOT / ".github" / "workflows" / workflow).read_text(
        encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "--must-run \\")
    listed: list[str] = []
    for line in lines[start + 1:]:
        listed.append(line.strip().removesuffix("\\").strip())
        if not line.rstrip().endswith("\\"):
            break
    return listed


@pytest.mark.parametrize("workflow", ["pr-check.yml", "deploy.yml"])
def test_the_r8_twin_is_in_the_must_run_list(workflow: str) -> None:
    assert "tests/unit/test_studio_field_claims_r8.py" in _must_run(workflow)
