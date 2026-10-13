"""WS-53 CRM-C4p — the client check of a custom field mirrors the gateway.

Spec: ``project-docs/specs/crm_platform.md`` §20.7, "CRM-C4p: the shared
client", done-when item 8.

``checkValue`` in ``app/crm/lib/fieldLayout.ts`` runs before each save, so the
gateway seldom gets a bad value. It mirrors ``routes/crm/field_studio.py``:
the six limits, the type list, the targets of a lookup and five patterns. Two
hand-kept copies drift in silence:

* a limit that is higher in TypeScript lets a form send a value that the
  gateway refuses, and the member reads a 422 after the save;
* a limit that is lower refuses a value that the gateway takes, and nothing
  tells the member why.

So this test READS the TypeScript, as ``test_crm_stage_discipline_parity.py``
reads ``board.ts``. The limits and the type list compare by value. A pattern
compares by what it does: each side runs the same table of sample strings, and
the verdicts must agree. A compare of the regex text would fail on a change
that keeps the meaning.

⚠️ The TypeScript patterns compile here with ``re.ASCII``, because JS ``\\d``
matches ASCII digits only. Python ``\\d`` also matches other scripts, so the
samples stay ASCII. The client then refuses a few values that the gateway
takes. That is safe: the gateway stays the authority.

R7: this file is the fence that ``fieldLayout.ts`` names.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_CRM_LIB = (
    Path(__file__).resolve().parents[2]
    / "workbench" / "control_plane" / "src" / "app" / "crm" / "lib"
)
FIELD_LAYOUT_TS = _CRM_LIB / "fieldLayout.ts"
FIELDS_TS = _CRM_LIB / "fields.ts"

LIMITS = ("TEXT_MAX", "TEXTAREA_MAX", "NUMBER_LIMIT", "EMAIL_MAX", "PHONE_MAX", "URL_MAX")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _ts_number(name: str) -> float:
    """``export const NAME = 1e15;`` → its value. The literal must be numeric."""
    found = re.search(
        rf"export\s+const\s+{name}\s*=\s*(?P<value>[0-9][0-9_]*(?:\.[0-9_]+)?(?:e[0-9]+)?)\s*;",
        _source(FIELD_LAYOUT_TS),
    )
    assert found is not None, (
        f"fieldLayout.ts no longer exports {name} as a numeric literal. Keep it "
        "a literal, so this fence can read it, rather than deleting the check."
    )
    return float(found.group("value").replace("_", ""))


def _ts_strings(name: str) -> list[str]:
    """``export const NAME = [ "a", … ] as const;`` → the strings."""
    found = re.search(
        rf"export\s+const\s+{name}\s*=\s*\[(?P<body>.*?)\]\s*as\s+const",
        _source(FIELD_LAYOUT_TS),
        re.S,
    )
    assert found is not None, f"fieldLayout.ts no longer exports a {name} array literal"
    return re.findall(r'"([^"]+)"', found.group("body"))


def _compile(src: str, flags: str) -> re.Pattern[str]:
    """A JS regex literal as a Python pattern with the same meaning on ASCII."""
    assert set(flags) <= {"i"}, f"a flag this fence cannot read: {flags!r}"
    return re.compile(src, re.ASCII | (re.IGNORECASE if "i" in flags else 0))


def _ts_const_regex(name: str) -> re.Pattern[str]:
    """``export const NAME = /…/flags;`` → the pattern."""
    found = re.search(
        rf"export\s+const\s+{name}\s*=\s*/(?P<src>.+)/(?P<flags>[a-z]*)\s*;",
        _source(FIELD_LAYOUT_TS),
    )
    assert found is not None, f"fieldLayout.ts no longer exports {name} as a regex literal"
    return _compile(found.group("src"), found.group("flags"))


def _ts_function_regex(function: str) -> re.Pattern[str]:
    """The first ``/^…$/flags.test`` in ``export function <function>`` of fields.ts."""
    found = re.search(
        rf"export\s+function\s+{function}\b.*?/(?P<src>\^.*?\$)/(?P<flags>[a-z]*)\.test\(",
        _source(FIELDS_TS),
        re.S,
    )
    assert found is not None, f"fields.ts::{function} no longer tests a regex literal"
    return _compile(found.group("src"), found.group("flags"))


# ── The limits and the type list ────────────────────────────────────────────


@pytest.mark.parametrize("name", LIMITS)
def test_each_limit_is_the_same_on_both_sides(name: str) -> None:
    from gateway.routes.crm import field_studio

    assert _ts_number(name) == float(getattr(field_studio, name)), (
        f"fieldLayout.ts::{name} and field_studio.{name} have drifted"
    )


def test_the_type_list_is_the_same_list_on_both_sides() -> None:
    from gateway.routes.crm.field_studio import FIELD_TYPES

    assert _ts_strings("FIELD_TYPES") == list(FIELD_TYPES), (
        "fieldLayout.ts::FIELD_TYPES and field_studio.FIELD_TYPES have drifted. "
        "A type on one side only is an input with no check, or a check with no input."
    )


def test_the_lookup_targets_are_the_same_on_both_sides() -> None:
    from gateway.routes.crm.field_studio import NATIVE_LOOKUP_TARGETS

    assert _ts_strings("LOOKUP_TARGETS") == list(NATIVE_LOOKUP_TARGETS)


# ── The patterns, on one table of samples ───────────────────────────────────

#: Each pattern: the Python twin, how the gateway runs it, the TypeScript
#: pattern, and the samples. A sample is stripped first where the gateway
#: strips it.
_DATES = [
    "2026-10-14", "0001-01-01", "9999-12-31", "2026-1-14", "20261014",
    "2026-W42-1", "2026-10-14T00:00", " 2026-10-14", "2026/10/14", "",
]
_DATETIMES = [
    "2026-10-14T10:30Z", "2026-10-14T10:30:00Z", "2026-10-14T10:30:00.123Z",
    "2026-10-14T10:30:00.123456Z", "2026-10-14T10:30:00.1234567Z",
    "2026-10-14T10:30+05:30", "2026-10-14T10:30:00-08:00", "2026-10-14T10:30",
    "2026-10-14 10:30Z", "+010000-01-01T00:00:00.000Z", "2026-10-14T10Z",
    "2026-10-14T10:30+0530", "2026-10-14t10:30z",
]
_EMAILS = [
    "a@b.in", "first.last@sub.example.co.in", "a@b", "a b@c.in", "a@b.com?bcc=x",
    "a@b.com#x", "a%b@c.in", "a,b@c.in", "a;b@c.in", "<a@b.in>", "a@@b.in",
    "@b.in", "a@.in", "a&b@c.in", "a?b@c.in", "a#b@c.in", "a<b@c.in",
    "a@b?c.in", "a@b.c;d",
]
_PHONES = [
    "+91 98450 12345", "(080) 4123-4567", "+91.80.4123.4567", "1234",
    "+91 98450 12345 x56", "+91 98450 12345 ext. 56", "+91 98450 12345 EXT 56",
    "080 4123 4567 #2", "080 4123 4567 X2", "abc", "+91+98450", "12/34",
    "1234;5678", "x12", "1234 x", "++1234", "1234 ext.", "98450 12345 ext56",
]
_URLS = [
    "https://example.com", "http://x", "HTTPS://EXAMPLE.COM/a?b=c", "ftp://x.in",
    "https://", "https://a b", "javascript:alert(1)", "//example.com",
    "https:/example.com", "http://exa\tmple.com",
]


def _gateway_patterns() -> dict[str, tuple[re.Pattern[str], bool, re.Pattern[str], list[str]]]:
    from gateway.routes.crm import field_studio as fs

    return {
        # name: (python pattern, the gateway strips first, the TS pattern, samples)
        "date": (fs._DATE_RE, False, _ts_const_regex("DATE_RE"), _DATES),
        "datetime": (fs._DATETIME_RE, False, _ts_const_regex("DATETIME_RE"), _DATETIMES),
        "email": (fs._EMAIL_RE, True, _ts_function_regex("isEmail"), _EMAILS),
        "phone": (fs._PHONE_RE, True, _ts_const_regex("PHONE_RE"), _PHONES),
        "url": (fs._URL_RE, True, _ts_function_regex("safeHref"), _URLS),
    }


@pytest.mark.parametrize("kind", ["date", "datetime", "email", "phone", "url"])
def test_each_pattern_gives_the_gateway_verdict_on_every_sample(kind: str) -> None:
    python, strips, typescript, samples = _gateway_patterns()[kind]
    differ = []
    for sample in samples:
        text = sample.strip() if strips else sample
        # The gateway: `fullmatch` for a date and a time, `match` for the
        # rest. JS `.test` with `^…$` and no `m` flag is a whole-string match.
        server = bool(python.fullmatch(text) if not strips else python.match(text))
        client = typescript.fullmatch(text) is not None
        if server != client:
            differ.append((sample, server, client))
    assert not differ, (
        f"the {kind} pattern of fieldLayout.ts or fields.ts and field_studio.py "
        f"give other verdicts (sample, gateway, client): {differ}"
    )


@pytest.mark.parametrize("kind", ["date", "datetime", "email", "phone", "url"])
def test_each_table_holds_a_pass_and_a_refusal(kind: str) -> None:
    """A table where every sample passes, or every one fails, proves nothing."""
    python, strips, _, samples = _gateway_patterns()[kind]
    verdicts = {
        bool(python.fullmatch(s) if not strips else python.match(s.strip())) for s in samples
    }
    assert verdicts == {True, False}
