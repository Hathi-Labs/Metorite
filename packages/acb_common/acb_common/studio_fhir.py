"""The FHIR map of a Studio clinical record, and the export of a visit (WS-STU I6).

Spec: ``project-docs/specs/ai_studio.md`` §14 row I6 and D202.8 (task 2403).

**Scope of this slice.** This module is the FHIR map only. The ABDM link, the
ABHA check and the consent manager wait for the first clinic (OWNER-GATE:
ABDM partner registration). The module is pure: no gateway import, no I/O and
no database. A caller passes the records and the consent rows it already read
under the rights of the viewer.

**The map.** :class:`FhirBinding` ties one field key of an object to one
element of one FHIR resource. A package declares its bindings as data. The
module reads four resource kinds: ``Patient``, ``Encounter``, ``Observation``
and ``Condition``. A field with no binding never leaves.

**Consent.** :func:`export_visit` refuses with :class:`ConsentError` unless the
consent rows hold a grant for the patient, for the purpose ``fhir_export``,
that has not been withdrawn and has not expired. It checks consent before it
reads one field, so a refusal builds nothing.

**Check.** :func:`validate_bundle` checks the shape that the export promises:
a resource type that the module knows, a well-formed id, the required elements
of each type, and a reference that resolves inside the bundle. It does not
replace the HL7 validator. It is the fence that the export test runs.

A refusal never echoes a field value, because a record holds patient data.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

#: The resource kinds of this slice.
RESOURCE_TYPES: tuple[str, ...] = ("Patient", "Encounter", "Observation", "Condition")

#: The consent purpose that opens an export.
CONSENT_PURPOSE = "fhir_export"

#: A FHIR id: 1 to 64 characters of letters, digits, dash and dot.
_ID_RE = re.compile(r"[A-Za-z0-9.\-]{1,64}", re.ASCII)
_DATE_RE = re.compile(r"[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?", re.ASCII)
_DATETIME_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?(Z|[+-][0-9]{2}:[0-9]{2})",
    re.ASCII,
)

#: ``element`` values that each resource kind accepts, with the value shape.
#: ``text``: a string. ``date``: a date. ``code``: a code from the field.
#: ``quantity``: a number with the ``unit`` of the binding.
ELEMENTS: dict[str, dict[str, str]] = {
    "Patient": {"name": "text", "birthDate": "date", "gender": "code", "telecom": "text"},
    "Encounter": {"period.start": "datetime", "period.end": "datetime", "reason": "text"},
    "Observation": {"valueQuantity": "quantity", "valueString": "text", "effectiveDateTime": "datetime"},
    "Condition": {"code": "code", "onsetDateTime": "datetime"},
}

_GENDERS = frozenset({"male", "female", "other", "unknown"})
_REQUIRED: dict[str, tuple[str, ...]] = {
    "Patient": (),
    "Encounter": ("status", "class", "subject"),
    "Observation": ("status", "code", "subject"),
    "Condition": ("subject",),
}


class ConsentError(PermissionError):
    """No valid consent opens the export. The text names no patient data."""


class FhirError(ValueError):
    """A binding, a record or a bundle breaks the map. The text names no value."""


@dataclass(frozen=True)
class FhirBinding:
    """One field of one object, mapped to one element of one resource."""

    object: str
    field: str
    resource: str
    element: str
    system: str | None = None  # the code system of a ``code`` element
    unit: str | None = None  # the unit of a ``quantity`` element
    code: str | None = None  # the fixed Observation.code, such as a LOINC code
    code_system: str | None = None
    display: str | None = None


def check_binding(b: FhirBinding) -> None:
    """Raise :class:`FhirError` when a binding names no known element."""
    elements = ELEMENTS.get(b.resource)
    if elements is None:
        raise FhirError(f"Binding {b.object}.{b.field}: resource type is not supported.")
    kind = elements.get(b.element)
    if kind is None:
        raise FhirError(f"Binding {b.object}.{b.field}: element is not supported.")
    if kind == "quantity" and not b.unit:
        raise FhirError(f"Binding {b.object}.{b.field}: a quantity needs a unit.")
    if b.resource == "Observation" and not b.code:
        raise FhirError(f"Binding {b.object}.{b.field}: an Observation needs a fixed code.")


def check_consent(consents: Sequence[Mapping[str, Any]], patient_id: str, *,
                  now: datetime | None = None) -> None:
    """Raise :class:`ConsentError` unless one live grant covers this patient."""
    at = now or datetime.now(UTC)
    for row in consents:
        if row.get("patient_id") != patient_id or row.get("purpose") != CONSENT_PURPOSE:
            continue
        if row.get("withdrawn_at"):
            continue
        expires = row.get("expires_at")
        if isinstance(expires, datetime) and expires <= at:
            continue
        if row.get("granted_at"):
            return
    raise ConsentError("No consent covers this export. Nothing was exported.")


def _fhir_id(raw: Any) -> str:
    text = str(raw)
    if not _ID_RE.fullmatch(text):
        raise FhirError("A record id is not a valid FHIR id.")
    return text


def _value(b: FhirBinding, raw: Any) -> Any:
    kind = ELEMENTS[b.resource][b.element]
    if kind == "text":
        if not isinstance(raw, str):
            raise FhirError(f"Field {b.field}: expected text.")
        return raw
    if kind == "date":
        if not isinstance(raw, str) or not _DATE_RE.fullmatch(raw):
            raise FhirError(f"Field {b.field}: expected a date.")
        return raw
    if kind == "datetime":
        if not isinstance(raw, str) or not _DATETIME_RE.fullmatch(raw):
            raise FhirError(f"Field {b.field}: expected a date and time with a zone.")
        return raw
    if kind == "quantity":
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise FhirError(f"Field {b.field}: expected a number.")
        return {"value": raw, "unit": b.unit}
    # code
    if not isinstance(raw, str) or not raw:
        raise FhirError(f"Field {b.field}: expected a code.")
    if b.resource == "Patient" and b.element == "gender":
        return raw if raw in _GENDERS else "unknown"
    coding: dict[str, str] = {"code": raw}
    if b.system:
        coding["system"] = b.system
    return {"coding": [coding]}


def _set(resource: dict[str, Any], element: str, value: Any) -> None:
    head, _, tail = element.partition(".")
    if tail:
        resource.setdefault(head, {})[tail] = value
    else:
        resource[head] = value


def _telecom(value: str) -> list[dict[str, str]]:
    return [{"value": value}]


def export_visit(*, patient: Mapping[str, Any], visit: Mapping[str, Any],
                 observations: Sequence[Mapping[str, Any]] = (),
                 conditions: Sequence[Mapping[str, Any]] = (),
                 bindings: Sequence[FhirBinding],
                 consents: Sequence[Mapping[str, Any]],
                 now: datetime | None = None) -> dict[str, Any]:
    """Build a FHIR ``collection`` bundle for one visit, after the consent check.

    Each record is ``{"id": ..., "data": {field: value}}``. A field with no
    binding is left out. The consent check runs first, so a refusal builds
    nothing.
    """
    patient_id = _fhir_id(patient.get("id"))
    check_consent(consents, str(patient.get("id")), now=now)
    for b in bindings:
        check_binding(b)
    by_object: dict[str, list[FhirBinding]] = {}
    for b in bindings:
        by_object.setdefault(b.object, []).append(b)

    def fill(resource: dict[str, Any], obj: str, data: Mapping[str, Any]) -> None:
        for b in by_object.get(obj, []):
            if b.field not in data or data[b.field] in (None, ""):
                continue
            value = _value(b, data[b.field])
            if b.resource == "Patient" and b.element == "telecom":
                value = _telecom(value)
            elif (b.resource, b.element) in {("Patient", "name"), ("Encounter", "reason")}:
                value = [{"text": value}]
            _set(resource, b.element, value)

    p: dict[str, Any] = {"resourceType": "Patient", "id": patient_id}
    fill(p, "patient", patient.get("data") or {})
    visit_id = _fhir_id(visit.get("id"))
    e: dict[str, Any] = {
        "resourceType": "Encounter", "id": visit_id, "status": "finished",
        "class": {"code": "AMB"}, "subject": {"reference": f"Patient/{patient_id}"},
    }
    fill(e, "visit", visit.get("data") or {})
    entries: list[dict[str, Any]] = [p, e]
    for rec in observations:
        obs_id = _fhir_id(rec.get("id"))
        for b in by_object.get("observation", []):
            data = rec.get("data") or {}
            if b.resource != "Observation" or data.get(b.field) in (None, ""):
                continue
            o: dict[str, Any] = {
                "resourceType": "Observation", "id": f"{obs_id}-{b.field.replace(chr(95), chr(45))}",
                "status": "final",
                "code": {"coding": [{"code": b.code, **({"system": b.code_system} if b.code_system else {}),
                                     **({"display": b.display} if b.display else {})}]},
                "subject": {"reference": f"Patient/{patient_id}"},
                "encounter": {"reference": f"Encounter/{visit_id}"},
            }
            _set(o, b.element, _value(b, data[b.field]))
            entries.append(o)
    for rec in conditions:
        c: dict[str, Any] = {
            "resourceType": "Condition", "id": _fhir_id(rec.get("id")),
            "subject": {"reference": f"Patient/{patient_id}"},
            "encounter": {"reference": f"Encounter/{visit_id}"},
        }
        fill(c, "condition", rec.get("data") or {})
        entries.append(c)
    bundle = {"resourceType": "Bundle", "type": "collection",
              "entry": [{"fullUrl": f"{r['resourceType']}/{r['id']}", "resource": r} for r in entries]}
    validate_bundle(bundle)
    return bundle


def validate_bundle(bundle: Mapping[str, Any]) -> None:
    """Raise :class:`FhirError` when the bundle breaks the shape the export promises."""
    if bundle.get("resourceType") != "Bundle" or bundle.get("type") != "collection":
        raise FhirError("The bundle is not a collection Bundle.")
    entries = bundle.get("entry")
    if not isinstance(entries, list) or not entries:
        raise FhirError("The bundle has no entry.")
    seen: set[str] = set()
    refs: list[str] = []
    for item in entries:
        res = item.get("resource") if isinstance(item, Mapping) else None
        if not isinstance(res, Mapping) or res.get("resourceType") not in RESOURCE_TYPES:
            raise FhirError("An entry holds a resource type that the export does not use.")
        rid = res.get("id")
        if not isinstance(rid, str) or not _ID_RE.fullmatch(rid):
            raise FhirError("A resource has no valid id.")
        key = f"{res['resourceType']}/{rid}"
        if key in seen:
            raise FhirError("Two resources share one id.")
        seen.add(key)
        for need in _REQUIRED[res["resourceType"]]:
            if need not in res:
                raise FhirError(f"A {res['resourceType']} lacks {need}.")
        for ref_key in ("subject", "encounter"):
            ref = res.get(ref_key)
            if isinstance(ref, Mapping):
                refs.append(str(ref.get("reference")))
    for ref in refs:
        if ref not in seen:
            raise FhirError("A reference does not resolve inside the bundle.")
