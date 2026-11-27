"""The Studio limits and error codes are golden (STU-L6a, spec 19.10 rule 17).

Python, the golden file and the two TypeScript twins hold the same data.
The numbers are proposed. Only the source size has an enforcement point
today. The manifest cap ``MAX_DOC_BYTES`` and the gateway cap
``MAX_MANIFEST_BYTES`` in ``packages_store.py`` must equal the table, so
the caps and the table cannot drift.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from acb_common.studio_contract import CONTRACT_UNSUPPORTED
from acb_common.studio_limits import ERROR_CODES, LIMITS, check_source_size
from acb_common.studio_manifest import MAX_DOC_BYTES

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests/unit/golden/studio_code_errors.json"
TS_DIR = ROOT / "workbench/control_plane/src/lib/studio"
STORE = ROOT / "apps/services/gateway/gateway/studio/packages_store.py"

CODES = [
    "compile_failed",
    "build_failed",
    "limit_exceeded",
    "timeout",
    "door_refused",
    "not_approved",
    "code_off",
    "code_paused",
    "runtime_busy",
    "bad_output",
    "contract_unsupported",
]


def _plain() -> dict:
    return {
        "codes": dict(ERROR_CODES),
        "limits": {k: dict(v) for k, v in LIMITS.items()},
    }


def test_golden_file_equals_python():
    text = GOLDEN.read_text(encoding="utf-8")
    assert json.loads(text) == _plain()
    assert text == json.dumps(_plain(), indent=2, sort_keys=True) + "\n"


def test_the_set_is_the_eleven_codes():
    assert set(ERROR_CODES) == set(CODES)
    assert len(ERROR_CODES) == 11


def test_contract_unsupported_has_one_definition():
    assert CONTRACT_UNSUPPORTED == "contract_unsupported"
    assert CONTRACT_UNSUPPORTED in ERROR_CODES


def test_runtime_busy_keeps_the_spec_text():
    assert ERROR_CODES["runtime_busy"] == "Try again in a few seconds."


@pytest.mark.parametrize("code", CODES)
def test_each_sentence_is_one_short_sentence(code):
    s = ERROR_CODES[code]
    assert s.endswith(".")
    assert ";" not in s
    assert s.count(".") == 1
    assert len(s.split()) <= 20
    assert not re.search(r"\d|/|\\", s)


def test_typescript_codes_equal_python():
    src = (TS_DIR / "codeErrors.ts").read_text(encoding="utf-8")
    body = re.search(r"STUDIO_CODE_ERRORS = \{(.*?)\n\} as const", src, re.S)
    assert body, "STUDIO_CODE_ERRORS is missing"
    pairs = dict(re.findall(r'^  (\w+): "([^"]*)",$', body.group(1), re.M))
    assert pairs == dict(ERROR_CODES)


def test_typescript_limits_equal_python():
    src = (TS_DIR / "limits.ts").read_text(encoding="utf-8")
    body = re.search(r"STUDIO_LIMITS = \{(.*?)\n\} as const", src, re.S)
    assert body, "STUDIO_LIMITS is missing"
    got = {}
    for kind, inner in re.findall(r"^  (\w+): \{\n(.*?)\n  \},$", body.group(1), re.M | re.S):
        got[kind] = {k: int(v) for k, v in re.findall(r"^    (\w+): (\d+),$", inner, re.M)}
    assert got == {k: dict(v) for k, v in LIMITS.items()}


@pytest.mark.parametrize("kind", ["browser", "server"])
def test_check_source_size(kind):
    nbytes = LIMITS[kind]["source_bytes"]
    nfiles = LIMITS[kind]["source_files"]
    assert check_source_size(nbytes, nfiles, kind) is None
    assert check_source_size(nbytes + 1, 1, kind) == "limit_exceeded"
    assert check_source_size(1, nfiles + 1, kind) == "limit_exceeded"


def test_check_source_size_defaults_to_server():
    assert check_source_size(LIMITS["server"]["source_bytes"] + 1, 1) == "limit_exceeded"


def test_manifest_caps_equal_the_table():
    assert LIMITS["server"]["source_bytes"] == MAX_DOC_BYTES
    if STORE.exists():
        m = re.search(r"^MAX_MANIFEST_BYTES = (\d+)$", STORE.read_text(encoding="utf-8"), re.M)
        assert m, "MAX_MANIFEST_BYTES is missing"
        assert int(m.group(1)) == LIMITS["server"]["source_bytes"]
