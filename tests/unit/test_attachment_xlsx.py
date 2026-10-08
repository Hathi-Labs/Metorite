"""WS-17 EM-T11b — the shared reader reads an Excel workbook (``.xlsx``).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.13. The reader is
``acb_skills.attachment_text``, the one hardened reader of H-229. Each
workbook here is built in code with :mod:`zipfile` (``_xlsx_build``), so a
test can make any part hostile.

R7 fences named here, each a test class:

* ``xlsx-text-shape`` (:class:`TestTheTextShape`). Two sheets, shared
  strings, inline strings, numbers, a boolean, a date and a formula with a
  cached value read as item 2 of the spec. A hidden sheet says so. A tab in
  a value stays in one field. A phonetic run adds no text. A cell with no
  ``r`` takes the next column, and a cell that goes back is dropped.
* ``xlsx-caps`` (:class:`TestTheCaps`). Each cap of item 3 stops the read and
  sets ``stopped``: the sheets, the rows, the column, the ``<c>`` elements,
  the shared strings, the unpacked XML and the characters, also in the
  middle of a row. A strings part over its byte cap is refused. A sheet of
  a million empty cells stops, and ``_Guard`` never refuses it.
* ``xlsx-parts`` (:class:`TestTheParts`). A sheet target outside ``xl/`` is
  refused. A target that starts with ``/`` starts at the package root. An
  external target is ignored, each part is read once, and only a worksheet
  is read.
* ``xlsx-refusals`` (:class:`TestTheRefusals`). A zip bomb, a part that
  hides its size, a DTD, an entity, a part that is not UTF-8, deep nesting
  and a file with a password each give one sentence that names an Excel
  workbook. A parse past its deadline stops.

Run::

    uv run pytest tests/unit/test_attachment_xlsx.py -v -rs
"""
from __future__ import annotations

import time
import tracemalloc
import zipfile
from typing import Any

import pytest
from acb_skills import attachment_text as at
from acb_skills import attachment_tools as tools

from tests.unit import _xlsx_build as xb
from tests.unit._xlsx_build import inline, num, row, shared_ref


def _read(data: bytes, **kw: Any) -> at.Extracted:
    return at.extract_text(data, ".xlsx", **kw)


def _one_sheet(rows: str, name: str = "S", **kw: Any) -> bytes:
    return xb.workbook([(name, xb.sheet(rows))], **kw)


# ── 1. The text shape ───────────────────────────────────────────────────────


class TestTheTextShape:

    def test_a_workbook_reads_as_rows_of_cells(self) -> None:
        """Two sheets, shared and inline strings, numbers, a boolean, a date
        and a formula with a cached value (spec item 2)."""
        shared = xb.strings(["Item", "Qty", "Nozzle"])
        first = xb.sheet(
            row(1, shared_ref("A1", 0) + shared_ref("B1", 1) + inline("C1", "Price"))
            + row(2, shared_ref("A2", 2) + num("B2", 4) + num("C2", 12.5)
                  + '<c r="D2"><f>B2*C2</f><v>50</v></c>')
            + row(4, '<c r="B4" t="b"><v>1</v></c><c r="C4" t="b"><v>0</v></c>')
            + row(5, '<c r="A5" s="3"><v>46301</v></c>')
        )
        second = xb.sheet(row(3, inline("B3", "Total") + num("D3", 50)))
        got = _read(xb.workbook([("Invoice", first), ("Summary", second)], shared=shared))
        assert got.text.splitlines() == [
            "## Sheet: Invoice",
            "A1\tItem\tQty\tPrice",
            "A2\tNozzle\t4\t12.5\t50",
            "B4\tTRUE\tFALSE",
            "A5\t46301",
            "## Sheet: Summary",
            "B3\tTotal\t\t50",
        ]
        assert (got.kind, got.unit, got.read, got.total, got.stopped) == (
            "xlsx", "cell", 12, 12, False,
        )

    def test_a_formula_gives_its_cached_value_never_its_formula(self) -> None:
        got = _read(_one_sheet(row(1, '<c r="A1"><f>SUM(B1:B9)</f><v>42</v></c>'
                                      '<c r="B1" t="str"><f>"x"&amp;"y"</f><v>xy</v></c>'
                                      '<c r="C1"><f>NOW()</f></c>')))
        assert got.text == "## Sheet: S\nA1\t42\txy"
        assert "SUM" not in got.text and "NOW" not in got.text

    def test_a_hidden_sheet_says_so(self) -> None:
        data = xb.workbook(
            [("Open", xb.sheet(row(1, num("A1", 1)))),
             ("Rates", xb.sheet(row(1, num("A1", 2))))],
            hidden=frozenset({"Rates"}),
        )
        assert _read(data).text.splitlines() == [
            "## Sheet: Open", "A1\t1", "## Sheet: Rates (hidden)", "A1\t2",
        ]

    def test_a_very_hidden_sheet_says_so_too(self) -> None:
        files = xb.parts([("Keys", xb.sheet(row(1, num("A1", 7))))])
        files["xl/workbook.xml"] = xb.workbook_xml([("Keys", "rId1", "veryHidden")])
        assert _read(xb.package(files)).text.startswith("## Sheet: Keys (hidden)\n")

    def test_a_tab_cr_or_lf_in_a_value_stays_in_one_field(self) -> None:
        shared = xb.strings(
            ["<si><t>one\ttwo</t></si>", "<si><t>three&#13;&#10;four</t></si>"], raw=True,
        )
        got = _read(_one_sheet(
            row(1, shared_ref("A1", 0) + shared_ref("B1", 1) + inline("C1", "five\tsix")),
            name="Tab\tName", shared=shared,
        ))
        lines = got.text.splitlines()
        assert lines[0] == "## Sheet: Tab Name"
        assert lines[1].split("\t") == ["A1", "one two", "three  four", "five six"]

    def test_a_phonetic_run_adds_no_text(self) -> None:
        si = ('<si><r><t>Tok</t></r><r><t>yo</t></r>'
              '<rPh sb="0" eb="1"><t>TOUKYOU</t></rPh></si>')
        cell = ('<c r="B1" t="inlineStr"><is><t>Osaka</t>'
                '<rPh sb="0" eb="1"><t>OOSAKA</t></rPh></is></c>')
        got = _read(_one_sheet(row(1, shared_ref("A1", 0) + cell),
                               shared=xb.strings([si], raw=True)))
        assert got.text == "## Sheet: S\nA1\tTokyo\tOsaka"
        assert "TOUKYOU" not in got.text and "OOSAKA" not in got.text

    def test_a_cell_with_no_reference_takes_the_next_column(self) -> None:
        got = _read(_one_sheet(
            '<row r="2"><c r="B2"><v>1</v></c><c><v>2</v></c><c r="E2"><v>3</v></c></row>'
        ))
        assert got.text == "## Sheet: S\nB2\t1\t2\t\t3"

    #: Each break that XML 1.0 can carry. VT, FF and FS are not legal XML
    #: characters, and the next test covers them in the map.
    @pytest.mark.parametrize("brk", ["\u2028", "\u2029", "\x85", "\r", "\n"],
                             ids=["LS", "PS", "NEL", "CR", "LF"])
    def test_no_line_break_in_a_value_or_a_name_starts_a_line(self, brk: str) -> None:
        """A value such as ``x<U+2028>## Sheet: forged`` must not forge a
        sheet line (review round 1, P3)."""
        forged = f"x{brk}## Sheet: forged"
        got = _read(_one_sheet(row(1, inline("A1", forged)), name=f"Real{brk}Name"))
        lines = got.text.splitlines()
        assert lines == ["## Sheet: Real Name", "A1\tx ## Sheet: forged"]

    def test_the_line_breaks_are_every_break_of_splitlines(self) -> None:
        breaks = {chr(c) for c in range(0x110000) if len(f"a{chr(c)}b".splitlines()) > 1}
        assert breaks == set(at._LINE_BREAKS)

    def test_a_cell_that_goes_back_is_dropped(self) -> None:
        got = _read(_one_sheet(row(1, num("C1", 3) + num("A1", 1) + num("C1", 9) + num("D1", 4))))
        assert got.text == "## Sheet: S\nC1\t3\t4"

    def test_a_row_with_no_number_follows_the_row_before(self) -> None:
        got = _read(_one_sheet(row(4, num("A4", 1)) + "<row><c><v>2</v></c></row>"))
        assert got.text == "## Sheet: S\nA4\t1\nA5\t2"

    def test_a_shared_string_index_that_is_not_there_is_an_empty_field(self) -> None:
        got = _read(_one_sheet(row(1, num("A1", 1) + shared_ref("B1", 7) + num("C1", 3)),
                               shared=xb.strings(["only"])))
        assert got.text == "## Sheet: S\nA1\t1\t\t3"

    def test_a_number_that_is_not_ascii_is_no_index_and_no_row(self) -> None:
        """``str.isdigit`` takes ``²``, and ``int`` refuses it. Neither may
        refuse the whole file."""
        got = _read(_one_sheet(
            '<row r="²"><c t="s"><v>²</v></c><c><v>5</v></c></row>'
            + row(3, shared_ref("A3", "9" * 40)),  # type: ignore[arg-type]
            shared=xb.strings(["s0"]),
        ))
        assert got.text == "## Sheet: S\nB1\t5"

    def test_an_empty_row_and_an_empty_sheet_give_no_line(self) -> None:
        data = xb.workbook([
            ("Blank", xb.sheet(row(1, '<c r="A1" s="2"/>'))),
            ("Data", xb.sheet(row(1, "") + row(2, num("A2", 5)))),
        ])
        assert _read(data).text == "## Sheet: Data\nA2\t5"

    def test_a_workbook_with_no_value_gives_empty_text(self) -> None:
        got = _read(_one_sheet(row(1, '<c r="A1"><f>A2</f></c>')))
        assert (got.text, got.read, got.stopped) == ("", 0, False)

    def test_attributes_match_by_local_name(self) -> None:
        """Strict OOXML uses other namespaces, and a file may pick any prefix."""
        files = xb.parts([("Strict", xb.sheet(row(1, num("A1", 1))))])
        files["xl/workbook.xml"] = (
            b'<x:workbook xmlns:x="http://purl.oclc.org/ooxml/spreadsheetml/main" '
            b'xmlns:q="http://purl.oclc.org/ooxml/officeDocument/relationships">'
            b'<x:sheets><x:sheet name="Strict" sheetId="1" q:id="rId1"/></x:sheets>'
            b"</x:workbook>"
        )
        assert _read(xb.package(files)).text == "## Sheet: Strict\nA1\t1"

    def test_the_tool_names_an_excel_workbook(self) -> None:
        got = _read(_one_sheet(row(1, num("A1", 1) + num("B1", 2))))
        assert tools._render("plan.xlsx", got, 0).startswith(
            "Attachment: plan.xlsx (Excel workbook, 2 cells read)"
        )


# ── 2. The caps ─────────────────────────────────────────────────────────────


def _rows(count: int, start: int = 1) -> str:
    return "".join(row(n, num(f"A{n}", n)) for n in range(start, start + count))


class TestTheCaps:

    def test_the_sheet_cap_stops_the_read(self, monkeypatch) -> None:
        monkeypatch.setattr(at, "MAX_XLSX_SHEETS", 2)
        data = xb.workbook([(f"S{n}", xb.sheet(row(1, num("A1", n)))) for n in (1, 2, 3)])
        got = _read(data)
        assert got.text.splitlines() == ["## Sheet: S1", "A1\t1", "## Sheet: S2", "A1\t2"]
        assert got.stopped is True and got.total is None

    def test_the_row_cap_ends_the_sheet(self, monkeypatch) -> None:
        monkeypatch.setattr(at, "MAX_XLSX_ROWS", 3)
        data = xb.workbook([("Big", xb.sheet(_rows(5))), ("Next", xb.sheet(_rows(1)))])
        got = _read(data)
        assert got.text.splitlines() == [
            "## Sheet: Big", "A1\t1", "A2\t2", "A3\t3", "## Sheet: Next", "A1\t1",
        ]
        assert got.stopped is True

    def test_the_row_cap_counts_empty_rows(self, monkeypatch) -> None:
        monkeypatch.setattr(at, "MAX_XLSX_ROWS", 3)
        got = _read(_one_sheet(row(1, "") + row(2, "") + row(3, "") + row(4, num("A4", 4))))
        assert (got.text, got.stopped) == ("", True)

    def test_the_real_row_cap_is_fast(self) -> None:
        started = time.monotonic()
        got = _read(_one_sheet(_rows(at.MAX_XLSX_ROWS + 500)))
        assert got.stopped is True
        assert got.text.splitlines()[-1] == f"A{at.MAX_XLSX_ROWS}\t{at.MAX_XLSX_ROWS}"
        assert time.monotonic() - started < 5.0

    def test_a_cell_past_column_gr_is_dropped(self) -> None:
        assert at.MAX_XLSX_COLUMN == 200
        got = _read(_one_sheet(row(1, num("A1", 1) + num("GR1", 200) + num("GS1", 201))
                               + row(2, num("A2", 2))))
        lines = got.text.splitlines()
        assert lines[1] == "A1\t1" + "\t" * 199 + "200"
        assert lines[2] == "A2\t2"
        assert "201" not in got.text and got.stopped is True

    def test_the_cell_cap_stops_the_read_in_a_row(self, monkeypatch) -> None:
        monkeypatch.setattr(at, "MAX_XLSX_CELLS", 4)
        three = "".join(num(f"{c}{{n}}", f"{c}{{n}}") for c in "ABC")
        rows = "".join(row(n, three.format(n=n)) for n in (1, 2, 3))
        got = _read(_one_sheet(rows))
        assert got.text.splitlines() == ["## Sheet: S", "A1\tA1\tB1\tC1", "A2\tA2"]
        assert got.stopped is True

    def test_the_cell_cap_counts_empty_cells(self, monkeypatch) -> None:
        monkeypatch.setattr(at, "MAX_XLSX_CELLS", 3)
        got = _read(_one_sheet(row(1, "<c/><c/><c/><c/>" + num("E1", 5))))
        assert (got.text, got.stopped) == ("", True)

    def test_a_million_empty_cells_stop_and_are_not_refused(self) -> None:
        """Without the ``<c>`` cap, ``_Guard`` refuses this part at its
        element cap. The cap turns that into a stop (M11)."""
        cells = at.MAX_DOCX_ELEMENTS + 50
        xml = xb.sheet("<row r=\"1\">" + "<c/>" * cells + "</row>")
        started = time.monotonic()
        got = _read(_one_sheet_raw(xml))
        assert got.stopped is True and got.text == ""
        assert time.monotonic() - started < 5.0

    def test_a_cell_with_no_reference_after_column_gr_is_dropped_too(self) -> None:
        """A dropped cell past GR still moves the column, so the next cell with
        no ``r`` is past GR as well (review round 1, P3)."""
        got = _read(_one_sheet(row(1, num("A1", 1) + num("GS1", 201)
                                   + "<c><v>LEFT</v></c>")))
        assert got.text == "## Sheet: S\nA1\t1"
        assert got.stopped is True

    def test_the_strings_cap_leaves_an_empty_field(self, monkeypatch) -> None:
        monkeypatch.setattr(at, "MAX_XLSX_STRINGS", 2)
        shared = xb.strings(["s0", "s1", "s2"])
        got = _read(_one_sheet(
            row(1, shared_ref("A1", 0) + shared_ref("B1", 2) + shared_ref("C1", 1)),
            shared=shared,
        ))
        assert got.text == "## Sheet: S\nA1\ts0\t\ts1"
        assert got.stopped is True

    def test_a_strings_part_over_its_byte_cap_is_refused(self, monkeypatch) -> None:
        shared = xb.strings(["x" * 400])
        monkeypatch.setattr(at, "MAX_XLSX_STRINGS_BYTES", 300)
        with pytest.raises(at.AttachmentRefused, match="This Excel workbook is too large"):
            _read(_one_sheet(row(1, shared_ref("A1", 0)), shared=shared))

    def test_the_unpacked_xml_cap_stops_before_the_next_sheet(self, monkeypatch) -> None:
        files = xb.parts([("One", xb.sheet(_rows(3))), ("Two", xb.sheet(_rows(3)))])
        used = sum(len(files[p]) for p in (
            "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/worksheets/sheet1.xml",
        ))
        monkeypatch.setattr(at, "MAX_XLSX_XML_BYTES", used + 10)
        got = _read(xb.package(files))
        assert got.text.splitlines() == ["## Sheet: One", "A1\t1", "A2\t2", "A3\t3"]
        assert got.stopped is True

    def test_the_char_cap_stops_in_the_middle_of_a_row(self, monkeypatch) -> None:
        """The cap is checked after each cell, not after each row (M7)."""
        monkeypatch.setattr(at, "MAX_EXTRACT_CHARS", 30)
        cells = "".join(inline(f"{c}1", c.lower() * 10) for c in "ABCDE")
        got = _read(_one_sheet(row(1, cells)))
        assert got.stopped is True
        assert got.read == 2, "the read went on past the cap to the end of the row"
        assert got.text == ("## Sheet: S\nA1\taaaaaaaaaa\tbbbbbbbbbb")[:30]

    def test_a_long_value_is_cut_at_the_char_cap(self, monkeypatch) -> None:
        monkeypatch.setattr(at, "MAX_EXTRACT_CHARS", 50)
        got = _read(_one_sheet(row(1, inline("A1", "z" * 500)) + row(2, num("A2", 1))))
        assert got.stopped is True and len(got.text) == 50 and "A2" not in got.text


def _one_sheet_raw(xml: bytes) -> bytes:
    return xb.workbook([("S", xml)])


# ── 3. The parts ────────────────────────────────────────────────────────────


def _with_links(links: list[tuple[str, ...]], extra: dict[str, bytes],
                names: list[str] | None = None) -> bytes:
    """A workbook whose sheets link to *links*, with *extra* parts beside."""
    names = names or [f"S{n}" for n in range(1, len(links) + 1)]
    files = xb.parts([])
    files["xl/workbook.xml"] = xb.workbook_xml(
        [(name, f"rId{n}", "") for n, name in enumerate(names, start=1)]
    )
    files["xl/_rels/workbook.xml.rels"] = xb.rels(
        [(f"rId{n}", *link) for n, link in enumerate(links, start=1)]  # type: ignore[misc]
    )
    files.update(extra)
    return xb.package(files)


_SECRET = xb.sheet(row(1, inline("A1", "WORD SECRET")))


class TestTheParts:

    @pytest.mark.parametrize("target", [
        "../word/document.xml", "/word/document.xml", "worksheets/../../word/document.xml",
        "../xlother/sheet1.xml",
    ])
    def test_a_sheet_target_outside_xl_is_refused(self, target: str) -> None:
        """``xlother/`` is outside ``xl/`` too: the check holds the slash."""
        data = _with_links([(xb.WORKSHEET, target)], {"word/document.xml": _SECRET,
                                                     "xlother/sheet1.xml": _SECRET})
        with pytest.raises(at.AttachmentRefused, match="as an Excel workbook"):
            _read(data)

    def test_a_target_from_the_package_root_reads(self) -> None:
        data = _with_links([(xb.WORKSHEET, "/xl/worksheets/a.xml")],
                           {"xl/worksheets/a.xml": xb.sheet(row(1, num("A1", 1)))})
        assert _read(data).text == "## Sheet: S1\nA1\t1"

    def test_an_external_target_is_ignored(self) -> None:
        data = _with_links(
            [(xb.WORKSHEET, "https://evil.example/s.xml", "External"),
             (xb.WORKSHEET, "/etc/passwd", "External"),
             (xb.WORKSHEET, "worksheets/b.xml")],
            {"xl/worksheets/b.xml": xb.sheet(row(1, num("A1", 2)))},
        )
        assert _read(data).text == "## Sheet: S3\nA1\t2"

    def test_each_part_is_read_once(self, monkeypatch) -> None:
        reads: list[str] = []
        real = at._read_part

        def _spy(zf, info, cap, fmt=at._WORD):
            reads.append(info.filename)
            return real(zf, info, cap, fmt)

        monkeypatch.setattr(at, "_read_part", _spy)
        data = _with_links(
            [(xb.WORKSHEET, "worksheets/a.xml")] * 3 + [(xb.WORKSHEET, "workbook.xml")],
            {"xl/worksheets/a.xml": xb.sheet(row(1, num("A1", 1)))},
        )
        got = _read(data)
        assert got.text == "## Sheet: S1\nA1\t1"
        assert reads.count("xl/worksheets/a.xml") == 1
        assert reads.count("xl/workbook.xml") == 1

    def test_only_a_worksheet_is_read(self) -> None:
        chart = f"{xb.REL}/chartsheet"
        data = _with_links(
            [(chart, "chartsheets/c.xml"), (xb.WORKSHEET, "worksheets/w.xml")],
            {"xl/chartsheets/c.xml": xb.sheet(row(1, inline("A1", "CHART"))),
             "xl/worksheets/w.xml": xb.sheet(row(1, inline("A1", "data")))},
        )
        assert _read(data).text == "## Sheet: S2\nA1\tdata"

    def test_many_sheet_entries_for_one_part_keep_one(self) -> None:
        """The workbook keeps the first entry of each part, and at most
        MAX_XLSX_SHEETS + 1 parts, so its entries cost no memory."""
        files = xb.parts([("S", xb.sheet(row(1, num("A1", 1))))])
        entries = "".join(f'<sheet name="n{n}" sheetId="{n}" r:id="rId1"/>'
                          for n in range(200_000))
        files["xl/workbook.xml"] = (
            f'<workbook xmlns="{xb.MAIN}" xmlns:r="{xb.REL}"><sheets>{entries}</sheets>'
            "</workbook>"
        ).encode()
        started = time.monotonic()
        got = _read(xb.package(files))
        assert got.text == "## Sheet: n0\nA1\t1"
        assert time.monotonic() - started < 5.0
        # The handler itself: one entry for one part, and the cap + 1 parts.
        same = at._Workbook({"rId1": "xl/a.xml"})
        at._parse_xml(files["xl/workbook.xml"], same, at._Deadline(20.0), at._EXCEL)
        assert len(same.sheets) == 1
        many = at._Workbook({f"rId{n}": f"xl/s{n}.xml" for n in range(500)})
        distinct = "".join(f'<sheet name="d{n}" r:id="rId{n}"/>' for n in range(500))
        xml = f'<workbook xmlns:r="{xb.REL}"><sheets>{distinct}</sheets></workbook>'
        at._parse_xml(xml.encode(), many, at._Deadline(20.0), at._EXCEL)
        assert len(many.sheets) == at.MAX_XLSX_SHEETS + 1

    def test_an_entry_with_no_part_does_not_count_toward_the_bound(self, monkeypatch) -> None:
        """Entries that name a missing part, or another part of the book, must
        not fill the bound of ``_Workbook`` and hide a real sheet after them
        with ``stopped`` false (review round 1, P3)."""
        monkeypatch.setattr(at, "MAX_XLSX_SHEETS", 2)
        links = [(xb.WORKSHEET, f"worksheets/gone{n}.xml") for n in range(5)]
        links += [(xb.WORKSHEET, "workbook.xml"), (xb.WORKSHEET, "worksheets/real.xml")]
        data = _with_links(links, {"xl/worksheets/real.xml": xb.sheet(row(1, num("A1", 7)))})
        got = _read(data)
        assert (got.text, got.stopped) == ("## Sheet: S7\nA1\t7", False)

    def test_the_strings_come_from_their_relationship(self) -> None:
        data = _with_links(
            [(xb.WORKSHEET, "worksheets/w.xml"), (xb.STRINGS, "strings/my.xml")],
            {"xl/worksheets/w.xml": xb.sheet(row(1, shared_ref("A1", 0))),
             "xl/strings/my.xml": xb.strings(["from the rel"]),
             "xl/sharedStrings.xml": xb.strings(["from the usual name"])},
            names=["S1"],
        )
        assert _read(data).text == "## Sheet: S1\nA1\tfrom the rel"

    def test_the_strings_fall_back_to_the_usual_name(self) -> None:
        data = _with_links(
            [(xb.WORKSHEET, "worksheets/w.xml")],
            {"xl/worksheets/w.xml": xb.sheet(row(1, shared_ref("A1", 0))),
             "xl/sharedStrings.xml": xb.strings(["usual"])},
        )
        assert _read(data).text == "## Sheet: S1\nA1\tusual"

    def test_a_strings_target_outside_xl_is_refused(self) -> None:
        data = _with_links(
            [(xb.WORKSHEET, "worksheets/w.xml"), (xb.STRINGS, "../docProps/s.xml")],
            {"xl/worksheets/w.xml": xb.sheet(row(1, num("A1", 1))),
             "docProps/s.xml": xb.strings(["x"])},
            names=["S1"],
        )
        with pytest.raises(at.AttachmentRefused, match="as an Excel workbook"):
            _read(data)

    def test_a_main_part_outside_xl_is_refused(self) -> None:
        files = xb.parts([("S", xb.sheet(row(1, num("A1", 1))))])
        files["_rels/.rels"] = xb.rels([("rId1", xb.OFFICE_DOCUMENT, "word/workbook.xml")])
        files["word/workbook.xml"] = files["xl/workbook.xml"]
        with pytest.raises(at.AttachmentRefused, match="as an Excel workbook"):
            _read(xb.package(files))

    def test_the_parse_of_a_sheet_ends_at_sheet_data(self) -> None:
        """What follows ``</sheetData>`` is never parsed, broken or not."""
        xml = xb.sheet(row(1, num("A1", 1)), after="<mergeCells><broken")
        assert _read(_one_sheet_raw(xml)).text == "## Sheet: S\nA1\t1"


# ── 4. The refusals ─────────────────────────────────────────────────────────


def _bomb() -> bytes:
    """A sheet part of 120 MB of XML inside a zip of about 120 KB."""
    files = xb.parts([("S", b"")])
    buf = __import__("io").BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            if name != "xl/worksheets/sheet1.xml":
                zf.writestr(name, data)
        with zf.open("xl/worksheets/sheet1.xml", "w") as fh:
            fh.write(f'<worksheet xmlns="{xb.MAIN}"><sheetData><row><c t="inlineStr"><is><t>'
                     .encode())
            chunk = b"A" * (1024 * 1024)
            for _ in range(120):
                fh.write(chunk)
            fh.write(b"</t></is></c></row></sheetData></worksheet>")
    return buf.getvalue()


def _lie_about_size(data: bytes, name: bytes, declared: int) -> bytes:
    out = bytearray(data)
    for sig, size_at, name_len_at, name_at in ((b"PK\x03\x04", 22, 26, 30),
                                               (b"PK\x01\x02", 24, 28, 46)):
        pos = out.find(sig)
        while pos != -1:
            n = int.from_bytes(out[pos + name_len_at:pos + name_len_at + 2], "little")
            if bytes(out[pos + name_at:pos + name_at + n]) == name:
                out[pos + size_at:pos + size_at + 4] = declared.to_bytes(4, "little")
            pos = out.find(sig, pos + 4)
    return bytes(out)


class TestTheRefusals:

    def test_a_zip_bomb_is_refused_before_its_part_is_unpacked(self, monkeypatch) -> None:
        data = _bomb()
        assert len(data) < 1024 * 1024
        opened: list[str] = []
        real_open = zipfile.ZipFile.open

        def _spy(self, name, *a, **k):
            opened.append(getattr(name, "filename", name))
            return real_open(self, name, *a, **k)

        monkeypatch.setattr(zipfile.ZipFile, "open", _spy)
        with pytest.raises(at.AttachmentRefused, match="Excel workbook is too large once"):
            _read(data)
        assert "xl/worksheets/sheet1.xml" not in opened

    def test_a_part_that_hides_its_size_unpacks_no_more_than_the_cap(self, monkeypatch) -> None:
        monkeypatch.setattr(at, "MAX_DOCX_XML_BYTES", 1024 * 1024)
        lying = _lie_about_size(_bomb(), b"xl/worksheets/sheet1.xml", 4096)
        tracemalloc.start()
        try:
            with pytest.raises(at.AttachmentRefused):
                _read(lying)
            _now, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert peak < 8 * 1024 * 1024, peak

    @pytest.mark.parametrize("part", [
        "xl/worksheets/sheet1.xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels",
        "xl/sharedStrings.xml",
    ])
    def test_a_dtd_in_any_part_is_refused_at_its_first_event(self, part: str) -> None:
        """An entity would put text into the answer that no member wrote (M3)."""
        files = xb.parts([("S", xb.sheet(row(1, shared_ref("A1", 0))))],
                         shared=xb.strings(["ok"]))
        body = files[part].decode()
        head, rest = body.split("?>", 1)
        files[part] = (head + '?><!DOCTYPE d [<!ENTITY x "INJECTED">]>' + rest).encode()
        with pytest.raises(at.AttachmentRefused, match="as an Excel workbook"):
            _read(xb.package(files))

    def test_an_entity_in_a_sheet_never_reaches_the_text(self) -> None:
        xml = (
            '<?xml version="1.0"?><!DOCTYPE w [<!ENTITY a "AAAAAAAAAA">'
            '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
            f'<worksheet xmlns="{xb.MAIN}"><sheetData><row r="1"><c r="A1" t="inlineStr">'
            "<is><t>&b;</t></is></c></row></sheetData></worksheet>"
        ).encode()
        with pytest.raises(at.AttachmentRefused, match="as an Excel workbook"):
            _read(_one_sheet_raw(xml))

    @pytest.mark.parametrize("xml", [
        f'<?xml version="1.0" encoding="UTF-16"?><worksheet xmlns="{xb.MAIN}"/>'.encode("utf-16"),
        f'<?xml version="1.0" encoding="ISO-8859-1"?><worksheet xmlns="{xb.MAIN}"/>'.encode(),
    ], ids=["utf-16", "latin-1"])
    def test_a_part_that_is_not_utf8_is_refused(self, xml: bytes) -> None:
        with pytest.raises(at.AttachmentRefused,
                           match=r"This Excel workbook is not stored as UTF-8.*from Excel"):
            _read(_one_sheet_raw(xml))

    def test_deep_nesting_is_refused(self) -> None:
        depth = at.MAX_DOCX_DEPTH + 10
        xml = (f'<worksheet xmlns="{xb.MAIN}"><sheetData>' + "<x>" * depth + "</x>" * depth
               + "</sheetData></worksheet>").encode()
        with pytest.raises(at.AttachmentRefused, match=r"This Excel workbook .*too deeply"):
            _read(_one_sheet_raw(xml))

    @pytest.mark.parametrize("data", [
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504,
        b"not a zip",
        xb.package({"xl/other.xml": b"<x/>"}),
        _one_sheet_raw(b'<worksheet xmlns="x"><sheetData><row>unclosed'),
    ], ids=["password-ole", "not-a-zip", "no-workbook", "broken-xml"])
    def test_a_broken_or_locked_workbook_is_refused_cleanly(self, data: bytes) -> None:
        with pytest.raises(at.AttachmentRefused) as err:
            _read(data)
        assert str(err.value) == (
            "I could not read this file as an Excel workbook. If it has a password, "
            "ask the member to remove it, or to attach a PDF or a text copy."
        )

    def test_a_parse_past_its_deadline_stops(self) -> None:
        with pytest.raises(at.AttachmentRefused, match="too long"):
            _read(_one_sheet(row(1, num("A1", 1))), seconds=-1.0)

    def test_the_deadline_stops_a_slow_sheet_inside_one_chunk(self, monkeypatch) -> None:
        real = at._Sheet._end_cell

        def _slow(self: Any) -> None:
            time.sleep(0.001)
            real(self)

        monkeypatch.setattr(at._Sheet, "_end_cell", _slow)
        xml = xb.sheet("".join(row(n, num(f"A{n}", n)) for n in range(1, 1300)))
        assert len(xml) < at._CHUNK
        started = time.monotonic()
        with pytest.raises(at.AttachmentRefused, match="too long"):
            _read(_one_sheet_raw(xml), seconds=0.3)
        assert time.monotonic() - started < 2.5

    def test_a_word_document_keeps_its_own_sentences(self) -> None:
        """The helpers take the format name, and a ``.docx`` keeps each text."""
        assert at._NOT_WORD == (
            "I could not read this file as a Word document. If it has a password, "
            "ask the member to remove it, or to attach a PDF or a text copy."
        )
        assert at._NOT_UTF8 == (
            "This Word document is not stored as UTF-8, so I did not read it. Ask the "
            "member to save it again from Word, or to attach a PDF."
        )
        assert at._TOO_COMPLEX == (
            "This Word document holds too many parts, or nests them too deeply, so I "
            "did not read it. Ask the member for a PDF or a text copy."
        )
        assert at._too_large(at._WORD) == (
            "This Word document is too large once unpacked, so I did not read it."
        )
