"""WS-41 I-1 — decode an export and read it as CSV, for every adapter.

Spec: ``project-docs/specs/project_import.md`` §4.3 items 6 and 11.
"""

from __future__ import annotations

import codecs

from gateway.routes.projects.importer.text import decode, read_csv


def test_a_utf8_bom_is_stripped_and_named() -> None:
    assert decode(codecs.BOM_UTF8 + "Tâche".encode()) == ("Tâche", "utf-8-sig")


def test_plain_utf8_wins_before_cp1252() -> None:
    assert decode("Tâche".encode()) == ("Tâche", "utf-8")


def test_an_excel_resave_falls_back_to_cp1252() -> None:
    text = "Caf\u00e9 \u2013 done"  # an e-acute and an en dash, both cp1252
    assert decode(text.encode("cp1252")) == (text, "cp1252")


def test_a_repeated_header_keeps_every_column() -> None:
    """Jira writes one ``Comment`` column per comment. A DictReader would keep
    only the last one."""
    table = read_csv("Key,Comment,Comment\nA-1,first,second\n")
    assert table.header == ["Key", "Comment", "Comment"]
    assert table.rows == [["A-1", "first", "second"]]


def test_short_rows_pad_and_blank_rows_drop() -> None:
    table = read_csv("a,b,c\n1\n\n,,\n1,2,3\n")
    assert table.rows == [["1", "", ""], ["1", "2", "3"]]
    assert table.wide_rows == 0


def test_a_wide_row_is_cut_and_counted() -> None:
    """An unquoted comma shifts every later column. The cut is never silent."""
    table = read_csv("a,b,c\n1,2,3,4\n1,2,3,\n")
    assert table.rows == [["1", "2", "3"], ["1", "2", "3"]]
    # A trailing empty cell is not a shifted row.
    assert table.wide_rows == 1


def test_an_empty_file_reads_as_nothing() -> None:
    assert read_csv("") == ([], [], 0)
