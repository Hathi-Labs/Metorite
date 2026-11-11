"""WS-STU I6 — the FHIR map and the visit export, with no database.

Spec: ``project-docs/specs/ai_studio.md`` §14 row I6 (task 2403).
Code: ``acb_common/studio_fhir.py``. Pure, so no R8 suite is needed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from acb_common import studio_fhir as fh

NOW = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)

BINDINGS = [
    fh.FhirBinding("patient", "full_name", "Patient", "name"),
    fh.FhirBinding("patient", "dob", "Patient", "birthDate"),
    fh.FhirBinding("patient", "sex", "Patient", "gender"),
    fh.FhirBinding("visit", "started", "Encounter", "period.start"),
    fh.FhirBinding("visit", "complaint", "Encounter", "reason"),
    fh.FhirBinding("observation", "pulse", "Observation", "valueQuantity", unit="/min",
                   code="8867-4", code_system="http://loinc.org", display="Heart rate"),
    fh.FhirBinding("condition", "icd10", "Condition", "code", system="http://hl7.org/fhir/sid/icd-10"),
]
PATIENT = {"id": "p-1", "data": {"full_name": "Test Patient", "dob": "1990-02-03", "sex": "female",
                                   "secret_note": "never leaves"}}
VISIT = {"id": "v-1", "data": {"started": "2026-11-11T09:30:00+05:30", "complaint": "Cough"}}
OBS = [{"id": "o-1", "data": {"pulse": 72}}]
COND = [{"id": "c-1", "data": {"icd10": "J20.9"}}]
CONSENT = [{"patient_id": "p-1", "purpose": "fhir_export", "granted_at": NOW - timedelta(days=1)}]


def _export(consents=CONSENT, **kw):
    return fh.export_visit(patient=PATIENT, visit=VISIT, observations=OBS, conditions=COND,
                           bindings=BINDINGS, consents=consents, now=NOW, **kw)


def test_test_visit_exports_a_valid_bundle():
    bundle = _export()
    fh.validate_bundle(bundle)
    kinds = [e["resource"]["resourceType"] for e in bundle["entry"]]
    assert kinds == ["Patient", "Encounter", "Observation", "Condition"]
    obs = bundle["entry"][2]["resource"]
    assert obs["valueQuantity"] == {"value": 72, "unit": "/min"}
    assert obs["subject"]["reference"] == "Patient/p-1"


def test_unbound_field_never_leaves():
    assert "never leaves" not in repr(_export())


@pytest.mark.parametrize("consents", [
    [],
    [{"patient_id": "p-2", "purpose": "fhir_export", "granted_at": NOW}],
    [{"patient_id": "p-1", "purpose": "ai_scribe", "granted_at": NOW}],
    [{"patient_id": "p-1", "purpose": "fhir_export", "granted_at": NOW, "withdrawn_at": NOW}],
    [{"patient_id": "p-1", "purpose": "fhir_export", "granted_at": NOW,
      "expires_at": NOW - timedelta(seconds=1)}],
    [{"patient_id": "p-1", "purpose": "fhir_export"}],
])
def test_no_valid_consent_exports_nothing(consents):
    with pytest.raises(fh.ConsentError) as err:
        _export(consents)
    assert "Test Patient" not in str(err.value)


def test_consent_is_checked_before_any_field_is_read():
    bad = {"id": "p-1", "data": {"dob": 12}}
    with pytest.raises(fh.ConsentError):
        fh.export_visit(patient=bad, visit=VISIT, bindings=BINDINGS, consents=[], now=NOW)


def test_bad_value_names_the_field_and_not_the_value():
    bad = {"id": "p-1", "data": {"dob": "not-a-date-SECRET"}}
    with pytest.raises(fh.FhirError) as err:
        fh.export_visit(patient=bad, visit=VISIT, bindings=BINDINGS, consents=CONSENT, now=NOW)
    assert "dob" in str(err.value) and "SECRET" not in str(err.value)


def test_binding_to_unknown_element_is_refused():
    with pytest.raises(fh.FhirError):
        fh.check_binding(fh.FhirBinding("patient", "x", "Patient", "nope"))
    with pytest.raises(fh.FhirError):
        fh.check_binding(fh.FhirBinding("patient", "x", "Medication", "name"))
    with pytest.raises(fh.FhirError):
        fh.check_binding(fh.FhirBinding("observation", "x", "Observation", "valueQuantity", unit="u"))
    with pytest.raises(fh.FhirError):
        fh.check_binding(fh.FhirBinding("observation", "x", "Observation", "value", code="1-1"))


def test_validate_bundle_refuses_a_dangling_reference_and_a_duplicate():
    bundle = _export()
    broken = {**bundle, "entry": bundle["entry"][1:]}
    with pytest.raises(fh.FhirError):
        fh.validate_bundle(broken)
    dup = {**bundle, "entry": [bundle["entry"][0], bundle["entry"][0]]}
    with pytest.raises(fh.FhirError):
        fh.validate_bundle(dup)


def test_bad_record_id_is_refused():
    with pytest.raises(fh.FhirError):
        fh.export_visit(patient={"id": "bad id!", "data": {}}, visit=VISIT, bindings=BINDINGS,
                        consents=[{"patient_id": "bad id!", "purpose": "fhir_export",
                                   "granted_at": NOW}], now=NOW)
