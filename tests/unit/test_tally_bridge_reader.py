"""TLY-1 PR 2: the Tally reader (brief sections 5.4, 5.5 and 6.5).

- The envelopes: every input escaped, only typed inputs, export only.
- The fence of section 6.5 rule 7: no read path can build an Import request.
- The parser: encoding, bad characters, defusedxml, STATUS 0.
- The date and sign rules.
- The queue of one, and the timeouts.

Every answer here is SYNTHETIC (fixtures/synthetic). A pass here is never a
pass of brief tests 6, 7 or 8: those need the real Tally screen.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import sys
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from defusedxml import ElementTree as SafeET

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "apps" / "connectors" / "tally_bridge"
sys.path.insert(0, str(ROOT))

from tally_bridge.reader import (  # noqa: E402
    TallyAnswerError,
    TallyClient,
    TallyDown,
    TallyRefused,
    TallyTimeout,
    envelope,
)
from tally_bridge.reader.parse import clean, parse_answer  # noqa: E402
from tally_bridge.reader.recording import RECORDINGS, write_requests  # noqa: E402
from tally_bridge.reader.values import (  # noqa: E402
    from_tally_date,
    parse_amount,
    to_tally_date,
)

SYNTHETIC = ROOT / "fixtures" / "synthetic"
REQUESTS = ROOT / "fixtures" / "requests"

HOSTILE = [
    "</SVCURRENTCOMPANY><TALLYREQUEST>Import</TALLYREQUEST>",
    "A & B <c> \"d\" 'e'",
    "]]><![CDATA[x",
    "&lt;already escaped&gt;",
    "$$SysName:XML",
    "Ünïcødé Traders ₹",
]


def _all_builds(value: str) -> list[bytes]:
    d1, d2 = date(2026, 1, 1), date(2026, 1, 31)
    out = [envelope.collection(name, company=value) for name in envelope.COLLECTIONS]
    out += [
        envelope.report(name, company=value, from_date=d1, to_date=d2, ledger=value)
        for name in envelope.REPORTS
    ]
    return out


# --- Envelopes and rule 7 --------------------------------------------------------


@pytest.mark.parametrize("value", HOSTILE)
def test_every_input_stays_text_and_every_request_is_an_export(value):
    for body in _all_builds(value):
        root = SafeET.fromstring(body)
        kinds = [el.text for el in root.iter("TALLYREQUEST")]
        assert kinds == ["Export"]
        assert root.find("BODY/DESC/STATICVARIABLES/SVCURRENTCOMPANY").text == value
        led = root.find("BODY/DESC/STATICVARIABLES/LEDGERNAME")
        if led is not None:
            assert led.text == value
        # The hostile text never became an element of its own.
        assert not any(el.tag == "TALLYREQUEST" and el.text == "Import" for el in root.iter())


def test_the_ampersand_and_angle_brackets_are_escaped_on_the_wire():
    body = envelope.report("Trial Balance", company="A&B <x>", to_date=date(2026, 3, 31))
    assert b"A&amp;B &lt;x&gt;" in body


@pytest.mark.parametrize("bad", ["a\x00b", "a\x04b", "x" * 201, "a\ud800b", "\udfff"])
def test_an_input_that_xml_cannot_carry_is_refused(bad):
    with pytest.raises(ValueError):
        envelope.report("Trial Balance", company=bad)


def test_an_unknown_report_or_collection_is_refused():
    with pytest.raises(ValueError):
        envelope.report("Voucher Import", company="A")
    with pytest.raises(ValueError):
        envelope.collection("AnyTDL", company="A")


def test_the_request_kind_is_a_constant_and_no_input_reaches_it():
    sig = inspect.signature(envelope.report)
    assert set(sig.parameters) == {"name", "company", "from_date", "to_date", "ledger"}
    assert envelope._REQUEST_KIND == "Export"


def _string_constants(path: Path) -> list[str]:
    """Every string literal of a module, except the docstrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
    ]


def test_no_read_path_can_build_an_import_request():
    """Section 6.5 rule 7. No literal of the program names Import or an import
    request, so no code path of the program can build one."""
    hits = []
    for f in sorted((ROOT / "tally_bridge").rglob("*.py")):
        for s in _string_constants(f):
            if "import" in s.lower():
                hits.append(f"{f.relative_to(REPO)}: {s!r}")
    assert not hits, hits


def test_only_the_envelope_module_names_the_request_kind_tag():
    """Rule 7, the second half: a new module cannot hand-build a request,
    because only envelope.py may name TALLYREQUEST."""
    hits = [
        str(f.relative_to(REPO))
        for f in sorted((ROOT / "tally_bridge").rglob("*.py"))
        if any("TALLYREQUEST" in s for s in _string_constants(f))
    ]
    assert hits == ["apps/connectors/tally_bridge/tally_bridge/reader/envelope.py"]


def test_the_last_step_of_every_builder_refuses_a_request_that_is_not_an_export():
    import xml.etree.ElementTree as ET

    env, _ = envelope._envelope("Data", "Trial Balance")
    env.find("HEADER/TALLYREQUEST").text = "Imp" + "ort"
    with pytest.raises(RuntimeError):
        envelope._bytes(env)
    env, _ = envelope._envelope("Data", "Trial Balance")
    ET.SubElement(env.find("HEADER"), "TALLYREQUEST").text = "Export"
    with pytest.raises(RuntimeError):
        envelope._bytes(env)


def test_the_committed_requests_match_the_builders(tmp_path):
    written = write_requests(tmp_path, "COMPANY NAME", "LEDGER NAME")
    assert [p.name for p in written] == [r.file for r in RECORDINGS]
    for p in written:
        assert (REQUESTS / p.name).read_bytes() == p.read_bytes(), p.name


# --- The parser ----------------------------------------------------------------


def test_every_synthetic_fixture_says_so():
    files = sorted(SYNTHETIC.iterdir())
    assert files
    for f in files:
        assert f.name.startswith("SYNTHETIC_")
        assert "SYNTHETIC" in f.read_text(encoding="utf-8").splitlines()[0]


def test_the_ledger_answer_parses_and_loses_its_bad_characters():
    root = parse_answer((SYNTHETIC / "SYNTHETIC_ledgers.xml").read_bytes())
    names = [el.text for el in root.iter("NAME")]
    assert names == ["Alpha Traders", "Beta & Sons", "Cash"]


def test_a_utf16_answer_with_a_bom_parses():
    text = (SYNTHETIC / "SYNTHETIC_ledgers.xml").read_text(encoding="utf-8")
    body = text.replace("<ENVELOPE>", '<?xml version="1.0" encoding="UTF-16"?><ENVELOPE>', 1)
    body = body.split("\n", 1)[1]  # the declaration must come first
    root = parse_answer(body.encode("utf-16"))
    assert next(el.text for el in root.iter("PARENT")) == "Sundry Debtors"


def test_the_charset_of_the_content_type_wins_without_a_bom():
    body = "<ENVELOPE><NAME>Ünï</NAME></ENVELOPE>".encode("utf-16-le")
    root = parse_answer(body, "text/xml; charset=UTF-16LE")
    assert root.find("NAME").text == "Ünï"


def test_status_zero_is_a_refusal():
    with pytest.raises(TallyRefused) as exc:
        parse_answer((SYNTHETIC / "SYNTHETIC_status0.xml").read_bytes())
    assert "Could not find Company" in exc.value.detail


def test_an_unknown_request_answer_is_a_refusal():
    with pytest.raises(TallyRefused):
        parse_answer((SYNTHETIC / "SYNTHETIC_unknown_request.xml").read_bytes())


@pytest.mark.parametrize(
    "body",
    [
        b"<ENVELOPE><BODY><STATUS>0</STATUS></BODY></ENVELOPE>",
        b"<ENVELOPE><BODY><DATA><STATUS>0</STATUS></DATA></BODY></ENVELOPE>",
        b"<ENVELOPE><BODY><DATA><RESPONSE>Unknown Request</RESPONSE></DATA></BODY></ENVELOPE>",
        b"<ENVELOPE><BODY><DATA><LINEERROR>No such ledger</LINEERROR></DATA></BODY></ENVELOPE>",
    ],
)
def test_a_nested_refusal_is_a_refusal(body):
    with pytest.raises(TallyRefused):
        parse_answer(body)


def test_a_response_with_children_and_a_status_field_in_a_record_are_data():
    parse_answer(b"<RESPONSE><A>1</A>text</RESPONSE>")
    parse_answer(
        b"<ENVELOPE><BODY><DATA><VOUCHER><STATUS>0</STATUS></VOUCHER></DATA></BODY></ENVELOPE>"
    )


def test_a_utf32_answer_with_a_bom_parses():
    root = parse_answer("<ENVELOPE><NAME>Ünï</NAME></ENVELOPE>".encode("utf-32"))
    assert root.find("NAME").text == "Ünï"


def test_an_entity_attack_is_refused():
    with pytest.raises(TallyAnswerError):
        parse_answer((SYNTHETIC / "SYNTHETIC_entity_attack.xml").read_bytes())


def test_bad_xml_is_an_answer_error():
    with pytest.raises(TallyAnswerError):
        parse_answer(b"<ENVELOPE><A></ENVELOPE>")


def test_clean_keeps_good_references_and_drops_control_ones():
    assert clean(b"<a>x&#4;y&#x1F;z&#10;&#65;&#x20AC;</a>") == "<a>xyz&#10;&#65;&#x20AC;</a>"
    assert clean(b"<a>&#xD800;&#99999999;b</a>") == "<a>b</a>"


# --- Dates and signs -------------------------------------------------------------------


def test_dates_go_in_and_come_out():
    assert to_tally_date(date(2025, 4, 1)) == "20250401"
    assert from_tally_date("20250401") == date(2025, 4, 1)
    assert from_tally_date("1-Apr-2025") == date(2025, 4, 1)
    assert from_tally_date("1-Apr-25") == date(2025, 4, 1)
    with pytest.raises(ValueError):
        from_tally_date("2025-04-01")


@pytest.mark.parametrize(
    "text,value,side",
    [
        ("-12500.50", "12500.50", "Dr"),
        ("7300", "7300.00", "Cr"),
        ("-1,250.75", "1250.75", "Dr"),
        ("(-)1250.75", "1250.75", "Dr"),
        ("1250.75 Dr", "1250.75", "Dr"),
        ("-1250.75 Cr", "1250.75", "Cr"),
        ("0.005", "0.01", "Cr"),
        ("", "0.00", None),
        ("-0.00", "0.00", None),
        ("0.001", "0.00", None),
        ("-0.004", "0.00", None),
        ("1,23,456.00", "123456.00", "Cr"),
        ("-12,345,678.9", "12345678.90", "Dr"),
        ("-.5", "0.50", "Dr"),
    ],
)
def test_amounts_are_a_value_and_a_side(text, value, side):
    a = parse_amount(text)
    assert a.text == value
    assert a.side == side
    assert a.value >= Decimal(0)


@pytest.mark.parametrize("bad", ["abc", "12.3.4", "1e5", "--5", "1,,2", ",5", "5,", "1,2345"])
def test_a_bad_amount_is_refused(bad):
    with pytest.raises(ValueError):
        parse_amount(bad)


# --- The client: queue of one and timeouts ----------------------------------------------


def _ledgers_transport(delay_s: float = 0.0, log: list | None = None):
    body = (SYNTHETIC / "SYNTHETIC_ledgers.xml").read_bytes()

    async def handler(request: httpx.Request) -> httpx.Response:
        if log is not None:
            log.append(("start", time.monotonic()))
        await asyncio.sleep(delay_s)
        if log is not None:
            log.append(("end", time.monotonic()))
        return httpx.Response(200, content=body, headers={"content-type": "text/xml"})

    return httpx.MockTransport(handler)


def test_post_reads_an_answer():
    c = TallyClient("http://127.0.0.1:9000", 5, transport=_ledgers_transport())
    root = asyncio.run(c.post(envelope.collection("BridgeLedgers", company="Alpha")))
    assert root.find("COLLECTION") is not None


def test_the_queue_of_one_never_overlaps_two_requests():
    log: list = []
    c = TallyClient("http://127.0.0.1:9000", 5, transport=_ledgers_transport(0.05, log))
    req = envelope.collection("BridgeLedgers", company="Alpha")

    async def three():
        await asyncio.gather(c.post(req), c.post(req), c.is_up())

    asyncio.run(three())
    kinds = [k for k, _ in log]
    assert kinds == ["start", "end"] * 3


def test_a_closed_tally_is_down():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    c = TallyClient(f"http://127.0.0.1:{port}", 2)
    with pytest.raises(TallyDown):
        asyncio.run(c.post(envelope.collection("BridgeCompanies", company=None)))


def test_a_slow_tally_times_out():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    c = TallyClient("http://127.0.0.1:9000", 0.1, transport=httpx.MockTransport(handler))
    with pytest.raises(TallyTimeout):
        asyncio.run(c.post(envelope.collection("BridgeCompanies", company=None)))


def test_a_non_200_answer_is_an_answer_error():
    t = httpx.MockTransport(lambda r: httpx.Response(500, text="x"))
    c = TallyClient("http://127.0.0.1:9000", 1, transport=t)
    with pytest.raises(TallyAnswerError):
        asyncio.run(c.post(envelope.collection("BridgeCompanies", company=None)))


GOOD_GUID = "a1b2c3d4-0000-1111-2222-333344445555-00000065"


@pytest.mark.parametrize("value", HOSTILE)
def test_the_voucher_request_keeps_the_company_as_text_and_is_an_export(value):
    root = SafeET.fromstring(envelope.voucher(GOOD_GUID, company=value))
    assert [el.text for el in root.iter("TALLYREQUEST")] == ["Export"]
    assert root.find("BODY/DESC/STATICVARIABLES/SVCURRENTCOMPANY").text == value
    assert root.find("HEADER/ID").text == GOOD_GUID


@pytest.mark.parametrize(
    "guid",
    ["", "abc", GOOD_GUID + "<x>", "</ID><TALLYREQUEST>Import</TALLYREQUEST>", " " + GOOD_GUID],
)
def test_the_voucher_request_refuses_text_that_is_not_a_guid(guid):
    with pytest.raises(ValueError):
        envelope.voucher(guid, company="A")
