"""The data engine of the coding sandbox (WS-43y1a, maf_coding_engine.md §7.10).

One fixed script. The model never writes it and never changes it. The broker
runs it as ``python3 -I /opt/sandbox/data_engine.py <verb> <request id>``, so
the command holds no text from the model. The host writes the request to
``/workspace/.run/requests/<id>.json``, and the engine writes its answer to
``/workspace/.run/answers/<id>.json``. A dataset is a dir of Parquet files and
a ``manifest.json`` in ``/workspace/.data/<dataset id>/``.

The verbs are ``load``, ``query``, ``preview``, ``profile`` and ``export``.
The model gives one read-only SQL query. The engine runs it only on a
connection that ``_open_locked`` has locked (§7.10 "The SQL rule").

Each verb runs in a forked child with an address-space limit and a hard wall
clock (``_isolated``). So a query that eats memory or time ends as an answer
with ``error``, and the engine always writes an answer. A load streams the
file: its memory does not grow with the rows.

The engine reads ``.csv`` and ``.tsv`` (WS-43y1a), and ``.xlsx`` and
``.xlsm`` (WS-43y1b, the section "Reading an .xlsx or .xlsm workbook"). It
refuses ``.xls``, ``.xlsb`` and ``.ods`` always.

Only this file imports ``duckdb``. tests/unit/test_data_engine.py (WS43-F26)
fails when another module under ``apps/`` or ``packages/`` does.
"""

from __future__ import annotations

import codecs
import contextlib
import csv
import hashlib
import io
import json
import os
import posixpath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
import zlib
from array import array
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Context, Decimal
from itertools import accumulate
from pathlib import Path
from typing import Any
from xml.parsers import expat

# DuckDB may import numpy, and numpy's OpenBLAS starts a thread and a buffer
# for each core. In the child of ``_isolated`` they would fill its address space.
for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_name, "1")


class _LazyDuckDB:
    """DuckDB, imported on first use by the process that runs the verb.

    Its import starts a thread pool. The parent of ``_isolated`` only forks
    and waits, so it never imports DuckDB, and it forks with one thread.
    """

    def __getattr__(self, name: str) -> Any:
        import duckdb as module

        globals()["duckdb"] = module
        return getattr(module, name)


duckdb: Any = _LazyDuckDB()

try:
    import resource
except ImportError:  # Windows. The host tests run each verb in process.
    resource = None  # type: ignore[assignment]

RUN_DIR = Path("/workspace/.run")
DATA_DIR = Path("/workspace/.data")
OUTPUTS_DIR = Path("/workspace/outputs")

#: The fixed caps of the engine (§7.10 "Settings").
MAX_COLUMNS = 1000
MAX_CELL_CHARS = 200
MAX_NAME_CHARS = 200  # In an answer. The manifest keeps the full name.
MAX_NUMBER_CHARS = 64  # A longer text is never a number.
MAX_ROW_CHARS = 2**20  # One row of a file. A UTF-8 character is at most 4 bytes in DuckDB's line.
_LINE = MAX_ROW_CHARS * 4 + 2**20  # DuckDB's max_line_size. Its buffer is two lines.
MAX_ANSWER_BYTES = 900_000  # The host reads at most 1 MB.
XLSX_MAX_CELL_CHARS = 32_767  # The cell limit of Excel.
XLSX_MAX_ROWS = 1_048_575  # The sheet holds 1,048,576 rows, and one is the header.
KILL_MARGIN_SECONDS = 5  # The hard wall clock is the time cap plus this.
#: Request field -> (default, highest value). The host sends its settings here.
LIMITS = {
    "max_file_bytes": (25 * 2**20, 200 * 2**20),
    "max_rows": (2_000_000, 10_000_000),
    "max_result_rows": (100, 500),
    "timeout_seconds": (30, 600),
    "header_rows": (1, 3),
    "limit": (20, 100),
    "workspace_quota_mb": (2048, 2**20),  # sandbox_workspace_quota_mb caps a CSV export.
}
LOAD_TIMEOUT_SECONDS = 120
#: The staged rows of a load may take this many times ``max_file_bytes``.
PAD_FACTOR = 4

_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_DATASET_RE = re.compile(r"ds_[0-9a-f]{32}")
_EXPORT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,80}")
_NEVER = "The engine does not read {} files. Save the file as .xlsx or .csv."
_KINDS = (".csv", ".tsv", ".xlsx", ".xlsm")
_REFUSED_KINDS = {
    ".xls": _NEVER.format("old .xls"),
    ".xlsb": _NEVER.format(".xlsb"),
    ".ods": _NEVER.format(".ods"),
}
_TOO_WIDE = "The file is too wide for its size. Save fewer columns, or split it."
_ONE_SELECT = "The data tools run one read-only SELECT query, and nothing else."
#: The first word of a query. ``PRAGMA version`` parses as a SELECT, so the
#: statement type alone is not enough.
_FIRST_WORDS = {"select", "with", "from", "values", "("}
#: A spreadsheet app reads a cell that starts with one of these as a formula.
_FORMULA_FIRST = ("=", "+", "-", "@", "\t", "\r")
#: Result types that a cell cap cannot need. Every other type is cut in SQL.
_SHORT_TYPE = re.compile(
    r"BOOLEAN|U?(TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT)|FLOAT|DOUBLE|DECIMAL\(\d+,\d+\)"
    r"|DATE|TIME|TIMESTAMP.*|INTERVAL|UUID"
)

#: The type rules run in DuckDB, in SQL, so a load does not pay Python for
#: each cell. Each pattern is RE2, and RE2 runs in time linear in the text.
_BOOLS = "('true', 'false', 'yes', 'no')"
_MARK = "[₹$€£¥]|inr|usd|eur|gbp"
#: The parts of a number written as text: (, sign, currency, body, %, currency, ).
_NUMBER_PARTS = (
    rf"^(\()?\s*([+-])?\s*((?i:{_MARK}|rs\.?)\s*)?([0-9.,eE+-]{{1,{MAX_NUMBER_CHARS}}})"
    rf"\s*(%)?\s*((?i:{_MARK}))?\s*(\))?$"
)
_PART_NAMES = "['po', 'sign', 'cur1', 'body', 'pct', 'cur2', 'pc']"
_BODY = {
    # Western 1,234,567.5, Indian 1,23,456.5, or plain. A zero first is text (an id).
    False: (
        r"(?:0|[1-9]\d*)?(?:\.\d+)?(?:[eE][+-]?\d+)?|(?:0|[1-9]\d*)\.",
        r"[1-9]\d{0,2}(?:,\d{3})+(?:\.\d+)?|[1-9]\d?(?:,\d{2})+,\d{3}(?:\.\d+)?",
    ),
    # A decimal comma: 1.234,5 or 12,5. A plain number has no point.
    True: (r"0|[1-9]\d*", r"[1-9]\d{0,2}(?:\.\d{3})+(?:,\d+)?|(?:0|[1-9]\d*),\d+"),
}
#: In a ";" file with no decimal comma, 1.000 is one, or one thousand.
_DOTTED = r"-?[1-9]\d{0,2}(?:\.\d{3})+"
_DATE_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y")
_DATE_FORMATS += ("%d.%m.%Y", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y")
_DATETIME_FORMATS: tuple[str, ...] = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M")
_DATETIME_FORMATS += ("%Y-%m-%dT%H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M")
_DATETIME_FORMATS += ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M")
_FORMATS = _DATE_FORMATS + _DATETIME_FORMATS
#: The columns of one type query. A query plan grows faster than its width.
_BATCH = 32
#: A date of any format above has this shape. A column of another shape tries none.
_DATE_SHAPE = r"[0-9A-Za-z][0-9A-Za-z ,./:T-]{5,30}"
_DATEISH = (
    r"\d{1,4}[-/. ]\w{1,9}[-/., ]+\d{2,4}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?"
    r"|[A-Za-z]{3,9}\.? \d{1,2},? \d{4}"
)


class Refused(Exception):
    """A request that the engine will not serve. The answer names the code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class Dirs:
    run: Path = RUN_DIR
    data: Path = DATA_DIR
    outputs: Path = OUTPUTS_DIR


def _limit(req: dict[str, Any], key: str, low: int = 1, default: int | None = None) -> int:
    base, high = LIMITS[key]
    value = req.get(key, base if default is None else default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise Refused("bad_request", f"{key} must be a whole number from {low} to {high}.")
    return value


def _timeout(req: dict[str, Any], verb: str) -> int:
    return _limit(req, "timeout_seconds", default=LOAD_TIMEOUT_SECONDS if verb == "load" else None)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _letter(index: int) -> str:
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def _short(name: str) -> str:
    return name if len(name) <= MAX_NAME_CHARS else name[:MAX_NAME_CHARS] + "…"


def _duckdb_memory_mb() -> int:
    """DuckDB's ``memory_limit``: 512 MB, or 40% of a smaller container."""
    return min(512 * 2**20, _budget() * 2 // 5) // 2**20


def _budget() -> int:
    """The memory of the container: its cgroup limit, or 1 GiB (§7.1 rule 6)."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            text = Path(path).read_text(encoding="ascii").strip()
        except OSError:
            continue
        if text.isdigit() and int(text) < 2**50:
            return int(text)
    return 2**30


# --------------------------------------------------------------------------
# Reading a CSV or TSV file, and the type rules (§7.10 "Types", "CSV files").
# Python streams the records once and holds one row at a time. DuckDB then
# reads the clean copy to count what each column holds, and to cast it.
# --------------------------------------------------------------------------


def _too_many_rows(max_rows: int) -> Refused:
    message = f"The file has more than {max_rows} rows, the cap of one dataset."
    return Refused("too_many_rows", message + " Split the file, or filter it before loading.")


def _check_time(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise Refused("time", "The load passed its time cap.")


def _transcode(src: Path, dst: Path, deadline: float) -> str:
    """Copy *src* to *dst* as UTF-8, in chunks. Return the encoding it read."""
    with open(src, "rb") as fh:
        head = fh.read(3)
    if head.startswith(codecs.BOM_UTF8):
        tries = [("utf-8-sig", "replace")]
    elif head[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        tries = [("utf-16", "strict")]
    else:
        tries = [("utf-8", "strict"), ("cp1252", "replace")]
    for encoding, errors in tries:
        decoder = codecs.getincrementaldecoder(encoding)(errors)
        try:
            with open(src, "rb") as fh, open(dst, "w", encoding="utf-8", newline="") as out:
                while chunk := fh.read(2**20):
                    _check_time(deadline)
                    # A NUL byte is not text, and DuckDB's CSV reader stops on it.
                    out.write(decoder.decode(chunk).replace("\x00", "�"))
                out.write(decoder.decode(b"", final=True))
            return encoding
        except UnicodeDecodeError:
            continue
    raise Refused("bad_file", "The file has a UTF-16 mark, and it is not UTF-16.")


def _delimiter(sample: str, ext: str) -> str:
    """The delimiter that gives the most lines of one width. It reads 50 lines."""
    if ext == ".tsv":
        return "\t"
    lines = sample.splitlines()[:50]
    best, score = ",", (0, 0)
    for delim in ",;\t|":
        widths = Counter(len(r) for r in csv.reader(lines, delimiter=delim) if len(r) > 1)
        if widths:
            width, count = widths.most_common(1)[0]
            if (count, width) > score:
                best, score = delim, (count, width)
    return best


def _records(path: Path, delim: str, deadline: float) -> Iterator[tuple[int, list[str]]]:
    """Each record of the file with its number, one at a time, each cell stripped."""
    with open(path, encoding="utf-8", newline="") as fh:
        try:
            for number, record in enumerate(csv.reader(fh, delimiter=delim), 1):
                if number % 4096 == 0:
                    _check_time(deadline)
                yield number, [c.strip() for c in record]
        except csv.Error as exc:
            raise Refused("bad_file", f"The file is not a readable CSV file: {exc}") from exc


def _prepare(
    text: Path,
    raw: Path,
    delim: str,
    header_rows: int,
    max_rows: int,
    budget: int,
    deadline: float,
) -> dict[str, Any]:
    """Write each data row to *raw* with its record number.

    On the way it reads the header, the empty rows, the width and three
    samples of each column. It refuses a file over a cap at the first row past
    the cap, so a large file stops early.

    Each row is padded to the widest row so far, so the staged file grows
    with rows times width, and not with the source. *budget* caps those
    bytes as they stream, and caps the pad of a later wider row before it
    runs (round 3). Without it, a 3.8 MB file of one value a row under a
    header of 1,000 columns staged 1,364 MB.
    """
    head: list[list[str]] = []
    header: list[int] = []
    samples: list[list[str]] = []
    empty: list[int] = []  # The first 20 of them. ``blank`` counts them all.
    need: set[int] = set()
    rows = first = last = blank = width = 0
    grew, staged = False, 0
    padded: Counter[int] = Counter()  # The rows written at each width.
    with open(raw, "w", encoding="utf-8", newline="") as fh:
        # DuckDB reads a quoted line break only when each row ends in a bare LF.
        writer = csv.writer(fh, lineterminator="\n")
        for number, cells in _records(text, delim, deadline):
            if not any(cells):
                blank += 1
                empty += [number] if blank <= 20 else []
                continue
            if len(head) < header_rows:
                head.append(cells)
                header.append(number)
                width = max(width, len(cells))
                continue
            rows += 1
            if rows > max_rows:
                raise _too_many_rows(max_rows)
            if sum(map(len, cells)) > MAX_ROW_CHARS:
                raise Refused(
                    "bad_file", f"Row {number} holds more than {MAX_ROW_CHARS} characters."
                )
            if len(cells) > MAX_COLUMNS:
                raise Refused("too_many_columns", f"The file has more than {MAX_COLUMNS} columns.")
            grew |= rows > 1 and len(cells) > width
            width = max(width, len(cells))
            need.update(range(len(samples), width))
            samples += [[] for _ in range(width - len(samples))]
            for j in [j for j in need if j < len(cells) and cells[j]]:
                if cells[j] not in samples[j]:
                    samples[j].append(cells[j])
                if len(samples[j]) == 3:
                    need.discard(j)
            first, last = first or number, number
            writer.writerow([number, *cells, *[""] * (width - len(cells))])
            padded[width] += 1
            # The bytes of the row: its number, its cells and a separator a column.
            staged += len(str(number)) + sum(map(len, cells)) + width + 1
            if staged > budget:
                raise Refused("bad_file", _TOO_WIDE)
    if grew:
        if staged + sum(n * (width - w) for w, n in padded.items()) > budget:
            raise Refused("bad_file", _TOO_WIDE)
        _pad(raw, width + 1, deadline)
    if not rows:
        raise Refused("empty", "The file holds a header and no data rows.")
    if width > MAX_COLUMNS:
        raise Refused("too_many_columns", f"The file has more than {MAX_COLUMNS} columns.")
    samples += [[] for _ in range(width - len(samples))]
    return {"names": _names(head, width), "samples": samples, "rows": rows, "first": first,
            "last": last, "header": header, "empty_rows": empty, "blank": blank}  # fmt: skip


def _pad(raw: Path, width: int, deadline: float) -> None:
    """Pad each row of *raw* to *width*. A row wider than the rows above it
    makes the earlier rows short, and DuckDB's reader takes no short row."""
    padded = raw.with_suffix(".padded")
    with open(padded, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        for _, cells in _records(raw, ",", deadline):
            writer.writerow([*cells, *[""] * (width - len(cells))])
    os.replace(padded, raw)


def _names(head: list[list[str]], width: int) -> list[str]:
    """One name for each column. A header is one to three rows, and its levels join with " / "."""
    names: list[str] = []
    seen = {"_src_row"}
    for j in range(width):
        base = " / ".join(r[j] for r in head if j < len(r) and r[j]) or f"column_{j + 1}"
        name, n = base, 1
        while name.lower() in seen:
            n += 1
            name = f"{base}_{n}"
        seen.add(name.lower())
        names.append(name)
    return names


def _parts(source: str, js: list[int], modes: tuple[str, ...] = ("w",)) -> str:
    """The rows of *source*, with SQL columns that read each cell as a number.

    For column ``c{j}`` and a mode (``w`` western, ``d`` decimal comma), the
    columns are ``v`` (it is a number), ``p`` (it needed no clean-up), ``k``
    (its digits as clean text) and ``s`` (``k`` with its sign), as ``vw3``.
    Each layer computes its columns once, so no regex runs twice on a cell.
    The query carries only the columns *js*, so its plan grows with the batch
    (``_BATCH``) and not with the file.
    """
    extract = [f"regexp_extract(c{j}, {_lit(_NUMBER_PARTS)}, {_PART_NAMES}) AS e{j}" for j in js]
    layers: list[list[str]] = [[], [], [], []]  # The first stays empty: ``extract`` is it.
    for m in modes:
        simple, grouped = (_lit(p) for p in _BODY[m == "d"])
        for j in js:
            e = f"e{j}"
            layers[1].append(
                f"(regexp_full_match({e}.body, {simple}) AND regexp_matches({e}.body, '\\d')) AS i{m}{j}"
            )
            if m == "d":
                layers[2].append(
                    f"CASE WHEN i{m}{j} THEN {e}.body ELSE replace(replace({e}.body, '.', ''), ',', '.') END AS k{m}{j}"
                )
            else:
                layers[2].append(f"replace({e}.body, ',', '') AS k{m}{j}")
            k = f"k{m}{j}"
            digits = f"length(regexp_replace({k}, '[^0-9]', '', 'g'))"
            power = (
                f"coalesce(abs(try_cast(regexp_extract({k}, '[eE]([+-]?\\d+)$', 1) AS INTEGER)), 0)"
            )
            # 1e400 is not a figure of a spreadsheet. It would be inf as a DOUBLE.
            layers[3] += [
                f"(length(c{j}) <= {MAX_NUMBER_CHARS} AND ({e}.po = '') = ({e}.pc = '')"
                f" AND (i{m}{j} OR regexp_full_match({e}.body, {grouped}))"
                f" AND {digits} <= 30 AND {power} <= 30) AS v{m}{j}",
                f"({e}.po = '' AND {e}.cur1 = '' AND {e}.cur2 = '' AND {e}.pct = '' AND i{m}{j}) AS p{m}{j}",
                f"(CASE WHEN ({e}.po = '(') != ({e}.sign = '-') THEN '-' ELSE '' END || {k}) AS s{m}{j}",
            ]
    sql = f"(SELECT {', '.join(f'c{j}' for j in js)}, {', '.join(extract)} FROM {source})"
    for layer in layers:
        sql = f"(SELECT *, {', '.join(layer)} FROM {sql})" if layer else sql
    return sql


def _signed(j: int, dc: bool) -> str:
    """The value of the parts ``e{j}`` as clean text with its sign, for the cast."""
    e, simple = f"e{j}", _lit(_BODY[True][0])
    canon = f"replace({e}.body, ',', '')"
    if dc:
        canon = f"CASE WHEN regexp_full_match({e}.body, {simple}) THEN {e}.body"
        canon += f" ELSE replace(replace({e}.body, '.', ''), ',', '.') END"
    return f"(CASE WHEN ({e}.po = '(') != ({e}.sign = '-') THEN '-' ELSE '' END || {canon})"


def _batches(width: int) -> list[list[int]]:
    js = list(range(1, width + 1))
    return [js[i : i + _BATCH] for i in range(0, width, _BATCH)]


def _one_row(stage: Path, sql: str, deadline: float) -> tuple[Any, ...]:
    """Run one statement of a load on its own DuckDB database, and close it.

    One database keeps memory from each wide statement, outside its
    ``memory_limit`` (which counted 4 MB). Measured on DuckDB 1.5.6 in the
    image on 2026-10-10 (H-290): a load of 400 columns grew to 500 MB
    resident over its 41 statements, and to 771 MB on a CI runner, past the
    watch of ``_isolated``. A fresh database for each statement stays flat.
    """
    con = _load_connection(stage)
    try:
        work = lambda: con.execute(sql).fetchone()  # noqa: E731
        return tuple(_timed(con, max(1, int(deadline - time.monotonic())), work, "load") or ())
    finally:
        con.close()


def _decimal_comma(stage: Path, source: str, width: int, deadline: float) -> bool:
    """A ";" file with 12,5 or 1.234,5 reads its numbers with a decimal comma."""
    for js in _batches(width):
        tests = [f"count_if(c{j} LIKE '%,%' AND NOT vw{j} AND vd{j})" for j in js]
        if sum(
            _one_row(
                stage, f"SELECT {', '.join(tests)} FROM {_parts(source, js, ('w', 'd'))}", deadline
            )
        ):
            return True
    return False


#: The places of the counts in ``_column_stats``. The others are a minimum or a maximum.
_COUNTS = frozenset({0, 1, 2, 3, 4, 5, 8, 11, 12})


def _column_stats(j: int, m: str) -> list[str]:
    """Twelve counts of column ``c{j}`` in mode *m*, in the order that ``_types`` reads them."""
    v, k = f"v{m}{j}", f"k{m}{j}"
    return [
        f"count(c{j})",
        f"count_if(lower(c{j}) IN {_BOOLS})",
        f"count_if({v})",
        f"count_if({v} AND NOT p{m}{j})",
        f"count_if({v} AND e{j}.pct = '%')",
        f"count_if({v} AND regexp_full_match(c{j}, {_lit(_DOTTED)}))",
        f"max(CASE WHEN {v} THEN length(regexp_extract({k}, '\\.(\\d+)', 1)) END)",
        f"max(CASE WHEN {v} THEN length(regexp_extract({k}, '^(\\d*)', 1)) END)",
        f"count_if({v} AND regexp_matches({k}, '[eE]'))",
        f"min(CASE WHEN {v} THEN try_cast(s{m}{j} AS DOUBLE) END)",
        f"max(CASE WHEN {v} THEN try_cast(s{m}{j} AS DOUBLE) END)",
        f"count_if(regexp_full_match(c{j}, {_lit(_DATEISH)}))",
        f"count_if(regexp_full_match(c{j}, {_lit(_DATE_SHAPE)}) AND regexp_matches(c{j}, '\\d'))",
    ]


def _date_formats(
    stage: Path, source: str, counts: list[tuple[Any, ...]], deadline: float
) -> dict[int, list[str]]:
    """The date formats that read every value of each column, in the order of ``_FORMATS``.

    Only a column that is no boolean and no number, and whose every value has
    the shape of a date, tries the formats. The first 2000 rows pick the
    formats, and only those formats then read every row.
    """
    dated = [j for j, s in enumerate(counts, 1) if s[0] and max(s[1], s[2]) < s[0] == s[12]]
    out: dict[int, list[str]] = {}
    for i in range(0, len(dated), _BATCH):
        out |= _date_batch(stage, source, counts, dated[i : i + _BATCH], deadline)
    return out


def _date_batch(
    stage: Path,
    source: str,
    counts: list[tuple[Any, ...]],
    dated: list[int],
    deadline: float,
) -> dict[int, list[str]]:
    """``_date_formats`` for one batch of columns."""
    tries = [
        f"count_if(try_strptime(c{j}, {_lit(f)}) IS NOT NULL)" for j in dated for f in _FORMATS
    ]
    head = f"SELECT count(*), {', '.join(f'count(c{j})' for j in dated)}, {', '.join(tries)}"
    cols = ", ".join(f"c{j}" for j in dated)
    found = _one_row(stage, f"{head} FROM (SELECT {cols} FROM {source} LIMIT 2000)", deadline)
    n = len(_FORMATS)
    picked = {
        j: [
            f
            for i_f, f in enumerate(_FORMATS)
            if found[1 + len(dated) + i * n + i_f] == found[1 + i]
        ]
        for i, j in enumerate(dated)
    }
    tries = [
        f"count_if(try_strptime(c{j}, {_lit(f)}) IS NOT NULL)" for j in dated for f in picked[j]
    ]
    found = _one_row(stage, f"SELECT {', '.join(tries)} FROM {source}", deadline) if tries else ()
    out: dict[int, list[str]] = {}
    at = 0
    for j in dated:
        hits = found[at : at + len(picked[j])]
        at += len(picked[j])
        out[j] = [f for f, hit in zip(picked[j], hits, strict=True) if hit == counts[j - 1][0]]
    return out


def _types(
    stage: Path,
    source: str,
    seen: dict[str, Any],
    semicolon: bool,
    deadline: float,
) -> tuple[bool, list[dict[str, Any]], list[str]]:
    """Give each column one type. Return the decimal comma, the manifest
    entries and the SQL that casts each column."""
    width = len(seen["names"])
    dc = semicolon and _decimal_comma(stage, source, width, deadline)
    mode = "d" if dc else "w"
    found: tuple[Any, ...] = ()
    for js in _batches(width):
        stats = [s for j in js for s in _column_stats(j, mode)]
        found += _one_row(
            stage, f"SELECT {', '.join(stats)} FROM {_parts(source, js, (mode,))}", deadline
        )
    # Over a column with no value, DuckDB gives NULL for a count_if, and a
    # count is a number (WS-43y1b: an empty column failed the load before).
    counts = [
        tuple(0 if v is None and k in _COUNTS else v for k, v in enumerate(found[i : i + 13]))
        for i in range(0, len(found), 13)
    ]
    fits = _date_formats(stage, source, counts, deadline)
    cols, casts = [], []
    for j, (s, name) in enumerate(zip(counts, seen["names"], strict=True), 1):
        filled, bools, nums, loose, pct, dotted, scale, whole, power, low, high, dateish, _ = s
        col: dict[str, Any] = {"name": name, "type": "text", "empty": seen["rows"] - filled}
        col.update(parsed_from_text=0, findings=[], samples=seen["samples"][j - 1])
        cast = f"c{j}"
        if filled and bools == filled:
            col["type"] = "boolean"
            cast = f"lower(c{j}) IN ('true', 'yes')"
        elif filled and nums == filled:
            scale, whole = scale or 0, whole or 0
            if scale == 0 and not power and whole <= 18:
                col["type"], duck = "integer", "BIGINT"
            else:
                fit = not power and scale <= 18 and whole + scale <= 38
                col["type"], duck = "decimal", f"DECIMAL(38,{scale})" if fit else "DOUBLE"
            col["parsed_from_text"] = loose
            serial = "date" in name.lower() and 20000 <= low <= high <= 80000
            flags = {"percent_values": pct, "possible_excel_serial_date": serial}
            flags["ambiguous_thousands"] = semicolon and not dc and dotted
            col["findings"] = [f for f, on in flags.items() if on]
            cast = f"CAST({_signed(j, dc)} AS {duck})"
        elif fits.get(j):
            fmt = fits[j][0]
            other = fmt.replace("%d", "%_").replace("%m", "%d").replace("%_", "%m")
            col["findings"] += ["ambiguous_day_month"] if other != fmt and other in fits[j] else []
            col["type"] = "date" if fmt in _DATE_FORMATS else "datetime"
            cast = f"strptime(c{j}, {_lit(fmt)})" + ("::DATE" if col["type"] == "date" else "")
        else:
            kinds = {"number": nums, "boolean": bools, "date": dateish}
            kinds["text"] = filled - sum(kinds.values())
            # Each value has the shape of a date, and no one format reads them all.
            mixed = j in fits and not fits[j]
            col["findings"] += ["mixed_date_formats"] if mixed else []
            if mixed or sum(1 for n in kinds.values() if n) > 1:
                col["type_counts"] = {k: n for k, n in kinds.items() if n}
        cols.append(col)
        casts.append(f"{cast} AS {_q(name)}")
    return dc, cols, casts


def _parquet_options(width: int) -> str:
    """The Parquet writer's options. A wide table writes no dictionary and row
    groups of 2,048 rows.

    Measured on DuckDB 1.5.6 at a 390 MB ``memory_limit`` (round 2): with the
    default options, 1,000 columns of 2,000 rows fail. With no dictionary
    alone, 400 columns of one row fail. With no dictionary, no string
    dictionary page and row groups of 2,048 rows, each shape loads, and
    1,000 columns of 20,000 rows load in 2.2 s.
    """
    if width <= 64:
        return "(FORMAT parquet)"
    return (
        "(FORMAT parquet, STRING_DICTIONARY_PAGE_SIZE_LIMIT 1, DICTIONARY_SIZE_LIMIT 1,"
        " ROW_GROUP_SIZE 2048)"
    )


def _load_connection(stage: Path) -> duckdb.DuckDBPyConnection:
    """The DuckDB connection that types the tables of one load."""
    con = duckdb.connect(":memory:", config={"threads": "1"})
    con.execute(f"SET memory_limit = '{_duckdb_memory_mb()}MB'")
    con.execute("SET threads = 1")
    con.execute(f"SET temp_directory = {_lit((stage / 'spill').as_posix())}")
    return con


def _table_name(con: duckdb.DuckDBPyConnection, text: str, taken: set[str]) -> str:
    """A table name from *text*: lower case, digits and ``_``, and no reserved word.

    A second table of the same name gets ``_2``. No name holds ``__``, so the
    side table ``<table>__totals`` never meets another name.
    """
    table = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60] or "data"
    table = "t_" + table if table[0].isdigit() else table
    keyword = "SELECT count(*) FROM duckdb_keywords() WHERE keyword_name = ?"
    keyword += " AND keyword_category <> 'unreserved'"
    table += "_data" if con.execute(keyword, [table]).fetchone() != (0,) else ""
    name, n = table, 1
    while name in taken:
        n += 1
        name = f"{table}_{n}"
    taken.add(name)
    return name


def _write_dataset(
    stage: Path,
    raw: Path,
    ds_id: str,
    label: str,
    delim: str,
    seen: dict[str, Any],
    deadline: float,
) -> dict[str, Any]:
    """DuckDB types the rows of *raw* and writes them as one Parquet file."""
    con = _load_connection(stage)
    table = _table_name(con, Path(label).stem, set())
    con.close()
    dc, cols = _typed(stage, raw, table, seen, delim == ";", deadline)
    width = len(seen["names"])
    last, header = _letter(width - 1), seen["header"]
    left_out: list[dict[str, Any]] = [
        {"what": "header rows", "range": f"{table}!A{header[0]}:{last}{header[-1]}"}
    ]
    if seen["blank"]:
        left_out.append({"what": "empty rows", "count": seen["blank"], "rows": seen["empty_rows"]})
    return {
        "dataset_id": ds_id, "source": label, "delimiter": delim,
        "findings": ["decimal_comma"] if dc else [],
        "tables": [{"name": table, "sheet": table, "file": f"{table}.parquet", "rows": seen["rows"],
                    "range": f"{table}!A{seen['first']}:{last}{seen['last']}", "columns": cols,
                    "left_out": left_out}],
    }  # fmt: skip


def _typed(
    stage: Path,
    raw: Path,
    table: str,
    seen: dict[str, Any],
    semicolon: bool,
    deadline: float,
) -> tuple[bool, list[dict[str, Any]]]:
    """Type the rows of *raw* and write them as ``<table>.parquet``.

    Return the decimal comma and the manifest entry of each column. A CSV
    file and a workbook both come here, so both get the same types.
    """
    width = len(seen["names"])
    types = ", ".join(f"'c{j}': 'VARCHAR'" for j in range(width + 1))
    source = (
        f"read_csv({_lit(raw.as_posix())}, header = false, auto_detect = false, delim = ',',"
        f" quote = '\"', escape = '\"', max_line_size = {_LINE}, buffer_size = {2 * _LINE},"
        # Each row ends in LF. With the newline named, a quoted CR or CRLF is data.
        f" new_line = '\\n', columns = {{{types}}})"
    )
    # One pass reads every column of the CSV file, and Parquet then
    # serves each batch only its own columns.
    staged = (stage / "rows.parquet").as_posix()
    options = _parquet_options(width)
    _one_row(stage, f"COPY (SELECT * FROM {source}) TO {_lit(staged)} {options}", deadline)
    raw.unlink()
    source = f"read_parquet({_lit(staged)})"
    dc, cols, casts = _types(stage, source, seen, semicolon, deadline)
    parquet = (stage / f"{table}.parquet").as_posix()
    # Each batch of columns writes its own typed file, and one positional join
    # then makes the dataset's file. One cast of every column at once took
    # 900 MB resident for 1,000 columns (round 2).
    numbers = {j for j, c in enumerate(cols, 1) if c["type"] in ("integer", "decimal")}
    parts = []
    for k, js in enumerate(_batches(width)):
        inner = ", ".join(
            f"regexp_extract(c{j}, {_lit(_NUMBER_PARTS)}, {_PART_NAMES}) AS e{j}"
            if j in numbers
            else f"c{j}"
            for j in js
        )
        lead = "CAST(c0 AS BIGINT) AS _src_row, " if k == 0 else ""
        select = lead + ", ".join(casts[j - 1] for j in js)
        part = (stage / f"typed{k}.parquet").as_posix()
        copy = f"COPY (SELECT {select} FROM (SELECT c0, {inner} FROM {source}))"
        _one_row(stage, f"{copy} TO {_lit(part)} {options}", deadline)
        parts.append(part)
    if len(parts) == 1:
        os.replace(parts[0], parquet)
    else:
        joined = " POSITIONAL JOIN ".join(f"read_parquet({_lit(p)})" for p in parts)
        _one_row(stage, f"COPY (SELECT * FROM {joined}) TO {_lit(parquet)} {options}", deadline)
        for part in parts:
            Path(part).unlink()
    Path(staged).unlink()
    found: tuple[Any, ...] = ()
    for js in _batches(width):
        bounds = ", ".join(
            f"min({_q(cols[j - 1]['name'])}), max({_q(cols[j - 1]['name'])})" for j in js
        )
        found += _one_row(stage, f"SELECT {bounds} FROM read_parquet({_lit(parquet)})", deadline)
    for j, col in enumerate(cols):
        if found[2 * j] is not None:
            col["min"], col["max"] = _cell(found[2 * j])[0], _cell(found[2 * j + 1])[0]
    return dc, cols


# --------------------------------------------------------------------------
# Reading an .xlsx or .xlsm workbook (WS-43y1b, §7.10 "Reading a messy file").
# zipfile and expat stream each part, a chunk at a time, and openpyxl never
# opens the file: the engine takes only its rules for a date format. Pass 1 of
# a sheet finds its used columns, its merged ranges and its hidden columns.
# Pass 2 streams the rows through the layout rules. Each table goes to its own
# clean CSV copy, so the type rules of a CSV file then run unchanged.
# --------------------------------------------------------------------------

#: The checks of a workbook's zip (§7.10 "Isolation"). The shared reader of
#: EM-T11b (``acb_skills/attachment_text.py``) holds checks of the same kind.
#: The image cannot import that package, so this file copies its rules and
#: not its code. The numbers are those of §7.10, because this engine reads
#: every row of a sheet, and that reader stops at 5,000.
ZIP_MAX_ENTRIES = 10_000
ZIP_MAX_PART_BYTES = 200 * 2**20
ZIP_MAX_RATIO = 100
#: A part smaller than this cannot be a bomb, so the ratio skips it.
ZIP_RATIO_FLOOR = 2**20
#: The workbook part, a rels part and the styles part.
XML_SMALL_PART_BYTES = 20 * 2**20
XML_MAX_DEPTH = 64
MAX_SHEETS = 50
MAX_TABLES = 200
MAX_MERGES = 100_000
#: Excel itself holds at most 64,000 cell styles.
MAX_STYLES = 65_536
#: The limits of Excel itself. Only a crafted file holds a longer sheet name or
#: number format code. A long name would go into each range and each cell
#: reference of an answer, and a long code costs openpyxl's date rule more than
#: linear time.
SHEET_NAME_CHARS = 31
FORMAT_CODE_CHARS = 255
EXCEL_MAX_COLUMN = 16_384
#: A block of rows of one cell each waits for a wider row this long. Past it,
#: the rows are a table of one column.
TEXT_BLOCK_ROWS = 50
_XML_CHUNK = 2**20
_STRINGS_CHUNK = 4096
_NOT_BOOK = "The file is not a readable .xlsx workbook. If it has a password, remove it."
_NO_DTD = "A part of the workbook declares a DTD or an entity, so the engine does not read it."
_DEEP = "A part of the workbook nests its XML too deeply."
_OUTSIDE = "The workbook names a part outside xl/, so the engine does not read it."
#: Rule 4: the first text cell of a total row.
_TOTAL_RE = re.compile(r"\s*(?:grand\s+total|sub-?\s?total|total|sum)s?\s*(?::|$|\s)", re.I)
_ESCAPE_RE = re.compile(r"_x([0-9A-Fa-f]{4})_")
_ENCODING_RE = re.compile(rb"""encoding\s*=\s*["']([^"']*)["']""")
#: Excel keeps 15 significant digits, and a cell shows no more.
_EXCEL_DIGITS = Context(prec=15)
#: A number as ``_excel_number`` writes it: no zero first, and no zero last after the point.
_PLAIN = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d*[1-9])?")
#: The built-in date formats of an East Asian locale. openpyxl does not list them.
_EAST_ASIAN_DATES = frozenset([*range(27, 32), *range(34, 37), *range(50, 59)])
_EAST_ASIAN_TIMES = frozenset({32, 33})
_EPOCH_1900 = datetime(1899, 12, 30)
_EPOCH_1904 = datetime(1904, 1, 1)
#: The first day past 9999-12-31 in the 1900 system.
_SERIAL_END = 2_958_466

#: One row of a sheet: its number, whether it is hidden, and its cells as
#: ``{column: (kind, text)}``. ``_Rows`` names the kinds.
_Row = tuple[int, bool, dict[int, tuple[str, str]]]


class _Stop(Exception):
    """A handler ends the parse of a part early. It is no error."""


def _zip_entries(path: Path) -> int:
    """The entries that the end record of the zip names.

    The engine reads the count before zipfile reads the directory. zipfile
    makes an object for each entry first, so a directory of 500,000 entries
    would cost it hundreds of MB before ``infolist()`` could count them.
    """
    with open(path, "rb") as fh:
        fh.seek(max(0, path.stat().st_size - 22 - 65_535))
        tail = fh.read()
        at = tail.rfind(b"PK\x05\x06")
        if at < 0 or len(tail) - at < 22:
            raise Refused("bad_file", _NOT_BOOK)
        count = int.from_bytes(tail[at + 10 : at + 12], "little")
        if count != 0xFFFF:
            return count
        # A zip64 file names its count in the zip64 end record.
        locator = tail.rfind(b"PK\x06\x07", 0, at)
        if locator < 0:
            raise Refused("bad_file", _NOT_BOOK)
        fh.seek(int.from_bytes(tail[locator + 8 : locator + 16], "little"))
        record = fh.read(56)
    if record[:4] != b"PK\x06\x06" or len(record) < 40:
        raise Refused("bad_file", _NOT_BOOK)
    return int.from_bytes(record[32:40], "little")


def _open_book(path: Path) -> zipfile.ZipFile:
    """Open the zip of a workbook after the checks of §7.10. It unpacks no part."""
    too_many = f"The workbook holds more than {ZIP_MAX_ENTRIES} zip entries."
    if _zip_entries(path) > ZIP_MAX_ENTRIES:
        raise Refused("bad_file", too_many)
    try:
        book = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError) as exc:
        raise Refused("bad_file", _NOT_BOOK) from exc
    try:
        if len(book.infolist()) > ZIP_MAX_ENTRIES:
            raise Refused("bad_file", too_many)
        for info in book.infolist():
            _check_entry(info)
    except Refused:
        book.close()
        raise
    return book


def _check_entry(info: zipfile.ZipInfo) -> None:
    """One entry of the zip: no password, no part over its cap and no bomb."""
    if info.flag_bits & 0x1:
        raise Refused("bad_file", _NOT_BOOK)
    if info.file_size > ZIP_MAX_PART_BYTES:
        mb = ZIP_MAX_PART_BYTES // 2**20
        raise Refused("too_large", f"A part of the workbook unpacks to more than {mb} MB.")
    if info.file_size > ZIP_RATIO_FLOOR and info.file_size > ZIP_MAX_RATIO * max(
        1, info.compress_size
    ):
        message = f"A part of the workbook unpacks to more than {ZIP_MAX_RATIO} times its size"
        raise Refused("bad_file", message + " in the zip. A real workbook does not.")


def _require_utf8(head: bytes) -> None:
    """A part is UTF-8 (the rule of EM-T11b). A UTF-16 part could hide a DTD
    from a byte search, so any other encoding is refused before the parse."""
    if head.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in head[:4]:
        raise Refused("bad_file", _NOT_BOOK)
    body = head[3:] if head.startswith(codecs.BOM_UTF8) else head
    if body.startswith(b"<?xml"):
        end = body.find(b"?>", 0, 512)
        found = _ENCODING_RE.search(body[: end if end != -1 else 512])
        if found and found.group(1).strip().lower() not in (b"utf-8", b"utf8"):
            raise Refused("bad_file", _NOT_BOOK)


def _no_dtd(*_args: Any) -> None:
    raise Refused("bad_file", _NO_DTD)


def _parse_part(
    book: zipfile.ZipFile, name: str, handler: Any, deadline: float, cap: int = ZIP_MAX_PART_BYTES
) -> None:
    """Stream one part through expat, a chunk at a time. The part is never whole in memory.

    The parser refuses a DTD and an entity declaration at the first event, so
    no entity is ever expanded. Each handler caps the depth of the XML.
    """
    try:
        info = book.getinfo(name)
    except KeyError:
        raise Refused("bad_file", _NOT_BOOK) from None
    too_large = f"A part of the workbook unpacks to more than {cap // 2**20} MB."
    if info.file_size > cap:
        raise Refused("too_large", too_large)
    parser = expat.ParserCreate(encoding="UTF-8")
    parser.StartDoctypeDeclHandler = _no_dtd
    parser.EntityDeclHandler = _no_dtd
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.buffer_text, parser.buffer_size = True, 2**16
    parser.StartElementHandler, parser.EndElementHandler = handler.start, handler.end
    if hasattr(handler, "text"):
        parser.CharacterDataHandler = handler.text
    read = 0
    try:
        with book.open(info) as fh:
            while chunk := fh.read(_XML_CHUNK):
                _check_time(deadline)
                if not read:
                    _require_utf8(chunk)
                read += len(chunk)
                if read > cap:
                    raise Refused("too_large", too_large)
                parser.Parse(chunk, False)
            parser.Parse(b"", True)
    except _Stop:
        return
    except expat.ExpatError as exc:
        message = f"A part of the workbook is not well-formed XML: {expat.ErrorString(exc.code)}."
        raise Refused("bad_file", message) from None
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError) as exc:
        raise Refused("bad_file", _NOT_BOOK) from exc


#: Caches of the two parses of a sheet, where each cell costs a handler call.
#: Each holds at most ``_CACHE`` keys, so a file of odd names cannot grow it.
_CACHE = 20_000
_LOCAL_NAMES: dict[str, str] = {}
_COLUMNS: dict[str, int] = {}


def _local(name: str) -> str:
    """An element name without its prefix: ``x:row`` is ``row``."""
    local = _LOCAL_NAMES.get(name)
    if local is None:
        local = name[name.rfind(":") + 1 :]
        if len(_LOCAL_NAMES) < _CACHE:
            _LOCAL_NAMES[name] = local
    return local


def _attr(attrs: dict[str, str], local: str) -> str:
    """The attribute *local*, matched by its local name, such as ``r:id``."""
    return next((v for k, v in attrs.items() if _local(k) == local), "")


def _digits(text: str, most: int) -> int | None:
    """*text* as a whole number of at most *most* ASCII digits, else None."""
    return int(text) if text.isascii() and text.isdigit() and len(text) <= most else None


def _column(ref: str) -> int:
    """The column of a reference such as ``AB12``: 28. 0 for a bad reference."""
    letters = ref.rstrip("0123456789")
    col = _COLUMNS.get(letters)
    if col is None:
        col = 0
        if 0 < len(letters) <= 3 and letters.isascii() and letters.isalpha():
            for ch in letters:
                col = col * 26 + (ord(ch) & 31)
        col = col if col <= EXCEL_MAX_COLUMN else 0
        if len(_COLUMNS) < _CACHE:
            _COLUMNS[letters] = col
    return col


def _ref(ref: str) -> tuple[int, int] | None:
    """``(column, row)`` of a reference such as ``B3``, else None."""
    col = _column(ref)
    row = _digits(ref[len(ref.rstrip("0123456789")) :], 7)
    return (col, row) if col and row else None


class _Part:
    """A handler of a small part. Each element counts toward the depth cap."""

    depth = 0

    def start(self, name: str, attrs: dict[str, str]) -> None:
        self.depth += 1
        if self.depth > XML_MAX_DEPTH:
            raise Refused("bad_file", _DEEP)
        self.on_start(_local(name), attrs)

    def end(self, name: str) -> None:
        self.depth -= 1
        self.on_end(_local(name))

    def on_start(self, local: str, attrs: dict[str, str]) -> None:
        return None

    def on_end(self, local: str) -> None:
        return None


class _Rels(_Part):
    """The relationships of a rels part: ``(Id, type, Target)``. An external
    target is never a part, so it is skipped."""

    def __init__(self) -> None:
        self.found: list[tuple[str, str, str]] = []

    def on_start(self, local: str, attrs: dict[str, str]) -> None:
        if local != "Relationship" or attrs.get("TargetMode", "").lower() == "external":
            return
        if len(self.found) >= ZIP_MAX_ENTRIES:
            raise Refused("bad_file", _NOT_BOOK)
        kind = attrs.get("Type", "").rsplit("/", 1)[-1]
        self.found.append((attrs.get("Id", ""), kind, attrs.get("Target", "")))


class _Workbook(_Part):
    """The sheets of ``workbook.xml`` in order, and its date system.

    It keeps an entry only when its rel id names a worksheet part, and only
    the first entry for each part, so no part is read twice.
    """

    def __init__(self, parts: dict[str, str]) -> None:
        self.parts = parts
        self.sheets: list[tuple[str, bool, str]] = []
        self.date1904 = False

    def on_start(self, local: str, attrs: dict[str, str]) -> None:
        if local == "workbookPr":
            self.date1904 = attrs.get("date1904", "").lower() in ("1", "true")
        elif local == "sheet":
            part = self.parts.pop(_attr(attrs, "id"), None)
            if part is None or any(part == p for _, _, p in self.sheets):
                return
            hidden = attrs.get("state", "").lower() in ("hidden", "veryhidden")
            self.sheets.append((attrs.get("name") or f"Sheet{len(self.sheets) + 1}", hidden, part))

    def cut_names(self) -> None:
        """Cut each name past Excel's 31 characters to 31 characters.

        Excel compares sheet names with no regard to case, so a cut name that
        meets another name ends in ``~2``, ``~3`` and on until it is unique. One
        count serves all the cut names, so the loop runs in linear time.
        """
        taken = {n.casefold() for n, _, _ in self.sheets if len(n) <= SHEET_NAME_CHARS}
        count, kept = 1, []
        for name, hidden, part in self.sheets:
            if len(name) > SHEET_NAME_CHARS:
                cut = name[:SHEET_NAME_CHARS]
                while cut.casefold() in taken:
                    count += 1
                    cut = name[: SHEET_NAME_CHARS - len(f"~{count}")] + f"~{count}"
                taken.add(cut.casefold())
                name = cut
            kept.append((name, hidden, part))
        self.sheets = kept


class _Strings(_Part):
    """The shared strings, packed: each 4,096 strings are one text and an array
    of ends. So a string costs its characters and 4 bytes, and not the 50
    bytes or more of a Python string. A phonetic run (``rPh``) is no text."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.ends: list[array[int]] = []
        self.buf: list[str] = []
        self.parts: list[str] | None = None
        self.in_t = False
        self.phonetic = self.size = 0

    def on_start(self, local: str, attrs: dict[str, str]) -> None:
        if local == "si":
            self.parts, self.size = [], 0
        elif local == "rPh":
            self.phonetic += 1
        elif local == "t":
            self.in_t = self.parts is not None and not self.phonetic

    def text(self, data: str) -> None:
        if self.in_t and self.parts is not None:
            self.parts.append(data)
            self.size += len(data)
            if self.size > MAX_ROW_CHARS:
                raise Refused("bad_file", f"A cell holds more than {MAX_ROW_CHARS} characters.")

    def on_end(self, local: str) -> None:
        if local == "t":
            self.in_t = False
        elif local == "rPh":
            self.phonetic = max(0, self.phonetic - 1)
        elif local == "si" and self.parts is not None:
            self.buf.append(_unescape("".join(self.parts)).strip())
            self.parts = None
            if len(self.buf) == _STRINGS_CHUNK:
                self.texts.append("".join(self.buf))
                self.ends.append(array("I", accumulate(map(len, self.buf))))
                self.buf = []

    def get(self, index: int) -> str:
        chunk, at = divmod(index, _STRINGS_CHUNK)
        if chunk < len(self.texts):
            ends = self.ends[chunk]
            return self.texts[chunk][ends[at - 1] if at else 0 : ends[at]]
        return self.buf[at] if chunk == len(self.texts) and at < len(self.buf) else ""


class _Styles(_Part):
    """The number format of each cell style, from ``cellXfs``."""

    def __init__(self) -> None:
        self.formats: dict[int, str] = {}
        self.xfs: list[int] = []
        self.in_xfs = False

    def on_start(self, local: str, attrs: dict[str, str]) -> None:
        fid = _digits(attrs.get("numFmtId", ""), 6)
        if local == "numFmt" and fid is not None and len(self.formats) < ZIP_MAX_ENTRIES:
            self.formats[fid] = attrs.get("formatCode", "")
        elif local == "cellXfs":
            self.in_xfs = True
        elif local == "xf" and self.in_xfs:
            if len(self.xfs) >= MAX_STYLES:
                raise Refused("bad_file", "The workbook holds too many cell styles.")
            self.xfs.append(fid or 0)

    def on_end(self, local: str) -> None:
        if local == "cellXfs":
            self.in_xfs = False

    def kinds(self) -> list[str | None]:
        """The date kind of each style (``_date_kind``)."""
        known: dict[int, str | None] = {}
        for fid in set(self.xfs):
            known[fid] = _date_kind(fid, self.formats.get(fid))
        return [known[fid] for fid in self.xfs]


def _date_kind(fid: int, code: str | None) -> str | None:
    """``date``, ``time``, ``datetime`` or ``duration`` for a number format,
    else None. openpyxl's rules decide a date format.

    A code past Excel's 255 characters is no date format, and its number
    stays a number. The engine skips other bad style data in the same way,
    as a ``numFmtId`` that is not a number. A style changes only how a number
    reads, so it is no cause to refuse the whole file. openpyxl's rules cost
    more than linear time on a long code, and this check stops that cost.
    """
    from openpyxl.styles.numbers import (  # type: ignore[import-untyped]
        BUILTIN_FORMATS,
        STRIP_RE,
        is_date_format,
        is_timedelta_format,
    )

    code = code if code is not None else BUILTIN_FORMATS.get(fid)
    if code is None:
        return "date" if fid in _EAST_ASIAN_DATES else "time" if fid in _EAST_ASIAN_TIMES else None
    if len(code) > FORMAT_CODE_CHARS:
        return None
    if is_timedelta_format(code):
        return "duration"
    if not is_date_format(code):
        return None
    bare = re.sub(r"[_\\].", "", STRIP_RE.sub("", code.split(";")[0])).lower()
    clock = any(c in bare for c in "hs")
    day = any(c in bare for c in "dy") or ("m" in bare and not clock)
    return "datetime" if day and clock else "date" if day else "time"


def _unescape(text: str) -> str:
    """OOXML writes a control character in a string as ``_x000D_``."""
    if "_x" not in text:
        return text
    return _ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), text)


def _excel_date(serial: float, kind: str, date1904: bool) -> str | None:
    """The text of an Excel serial date, or None when it names no date.

    The 1900 system keeps Excel's leap-year bug. Excel counts 29 February
    1900, a day that did not exist, as serial 60. So serials 1 to 59 count
    from 31 December 1899, serial 60 reads as the text ``1900-02-29``, and
    later serials count from 30 December 1899. A serial under 1 is a time
    of day. The 1904 system counts from 1 January 1904, with no such day.
    A date format keeps a time that its cell holds, so no part of the value
    is lost.
    """
    if not 0 <= serial < _SERIAL_END:
        return None
    days = int(serial)
    seconds = round((serial - days) * 86_400)
    if seconds == 86_400:
        days, seconds = days + 1, 0
    clock = f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
    if kind == "time" or (days == 0 and not date1904):
        return clock
    if date1904:
        text = (_EPOCH_1904 + timedelta(days=days)).date().isoformat()
    elif days == 60:
        text = "1900-02-29"
    else:
        text = (_EPOCH_1900 + timedelta(days=days + (days < 60))).date().isoformat()
    return text if kind == "date" and not seconds else f"{text} {clock}"


def _excel_number(raw: str, kind: str | None, date1904: bool) -> tuple[str, str] | None:
    """A number cell as ``(kind, text)``. A date style makes it a date (§7.10
    "Excel dates"). A number keeps 15 significant digits, as Excel shows it."""
    raw = raw.strip()
    if kind is None and len(raw) <= 15 and raw != "-0" and _PLAIN.fullmatch(raw):
        return "n", raw  # Most cells: no rounding and no trailing zero to drop.
    try:
        value = _EXCEL_DIGITS.create_decimal(raw)
    except (ArithmeticError, ValueError):
        return ("t", raw) if raw else None
    if not value.is_finite() or not -30 <= value.adjusted() <= 30:
        return "t", raw
    if kind in ("date", "time", "datetime"):
        text = _excel_date(float(value), kind, date1904)
        if text:
            return "d", text
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "n", "0" if text == "-0" else text


def _iso_cell(raw: str) -> tuple[str, str] | None:
    """A cell of type ``d``: an ISO 8601 date, with or without a time."""
    text = raw.strip().replace("T", " ").removesuffix("Z").split(".")[0]
    return ("d", text[:19]) if text else None


class _Survey:
    """Pass 1 of a sheet: its used columns, its merged ranges and its hidden columns.

    A column is used when a cell in it holds a value, an inline string or a
    formula. ``<mergeCells>`` comes after the rows, so pass 2 needs this pass.
    """

    def __init__(self) -> None:
        self.depth = self.col = self.merged = self.cols = 0
        self.used = bytearray(EXCEL_MAX_COLUMN + 2)
        self.hidden = bytearray(EXCEL_MAX_COLUMN + 2)
        self.merges: dict[int, list[tuple[int, int, int, int]]] = {}

    def start(self, name: str, attrs: dict[str, str]) -> None:
        self.depth += 1
        if self.depth > XML_MAX_DEPTH:
            raise Refused("bad_file", _DEEP)
        local = _LOCAL_NAMES.get(name) or _local(name)
        if local == "c":
            ref = attrs.get("r")
            self.col = _column(ref) if ref else self.col + 1
        elif local in ("v", "f", "is"):
            if 0 < self.col <= EXCEL_MAX_COLUMN:
                self.used[self.col] = 1
        elif local == "row":
            self.col = 0
        elif local == "mergeCell":
            self._merge(attrs.get("ref", ""))
        elif local == "col" and attrs.get("hidden", "") in ("1", "true"):
            self._hide(attrs)

    def end(self, _name: str) -> None:
        self.depth -= 1

    def _merge(self, ref: str) -> None:
        first, _, last = ref.partition(":")
        a, b = _ref(first), _ref(last)
        if not (a and b) or a == b:
            return
        self.merged += 1
        if self.merged > MAX_MERGES:
            raise Refused("bad_file", f"The sheet holds more than {MAX_MERGES} merged ranges.")
        (c1, r1), (c2, r2) = a, b
        top = min(r1, r2)
        self.merges.setdefault(top, []).append((top, min(c1, c2), max(r1, r2), max(c1, c2)))

    def _hide(self, attrs: dict[str, str]) -> None:
        self.cols += 1
        lo, hi = _digits(attrs.get("min", ""), 5), _digits(attrs.get("max", ""), 5)
        if self.cols <= EXCEL_MAX_COLUMN and lo and hi:
            lo, hi = max(1, lo), min(EXCEL_MAX_COLUMN, hi)
            if lo <= hi:
                self.hidden[lo : hi + 1] = b"\x01" * (hi - lo + 1)


class _Rows:
    """Pass 2 of a sheet: each row as ``{column: (kind, text)}``, one row at a time.

    A kind is ``t`` (text), ``n`` (a number), ``d`` (a date or a time), ``b``
    (a boolean), ``e`` (an error value) or ``f`` (a formula with no cached
    value). ``e`` and ``f`` hold no text: they read as empty, and the table
    counts them (rule 8). A formula gives its cached value, ``<v>``, and
    never its text, ``<f>``. A cell that does not come after the cell before
    it in its row is dropped.
    """

    def __init__(
        self,
        strings: _Strings,
        kinds: list[str | None],
        date1904: bool,
        on_row: Callable[[int, bool, dict[int, tuple[str, str]]], None],
        deadline: float,
    ) -> None:
        self.strings, self.kinds, self.date1904 = strings, kinds, date1904
        self.on_row, self.deadline = on_row, deadline
        self.depth = self.num = self.col = self.last = self.size = self.count = 0
        self.in_is = self.phonetic = 0
        self.hidden = self.in_v = self.in_t = self.has_f = self.take = False
        self.kind = self.style = ""
        self.buf: list[str] = []
        self.cells: dict[int, tuple[str, str]] = {}
        self.styles: dict[str, Any] = {}

    def start(self, name: str, attrs: dict[str, str]) -> None:
        self.depth += 1
        if self.depth > XML_MAX_DEPTH:
            raise Refused("bad_file", _DEEP)
        local = _LOCAL_NAMES.get(name) or _local(name)
        if local == "c":
            ref = attrs.get("r")
            self.col = col = _column(ref) if ref else self.col + 1
            self.take = self.last < col <= EXCEL_MAX_COLUMN
            self.kind, self.style = attrs.get("t", "n"), attrs.get("s", "")
            self.buf, self.size, self.has_f = [], 0, False
        elif local == "v":
            self.in_v = True
        elif local == "f":
            self.has_f = True
        elif local == "row":
            self._row(attrs)
        elif local == "is":
            self.in_is += 1
        elif local == "t":
            self.in_t = self.in_is > 0 and not self.phonetic
        elif local == "rPh":
            self.phonetic += 1

    def text(self, data: str) -> None:
        if self.in_v or self.in_t:
            self.buf.append(data)
            self.size += len(data)
            if self.size > MAX_ROW_CHARS:
                raise Refused("bad_file", f"A cell holds more than {MAX_ROW_CHARS} characters.")

    def end(self, name: str) -> None:
        self.depth -= 1
        local = _LOCAL_NAMES.get(name) or _local(name)
        if local == "c":
            if self.take:
                self.last = self.col
                value = self._value()
                if value is not None:
                    self.cells[self.col] = value
        elif local == "v":
            self.in_v = False
        elif local == "t":
            self.in_t = False
        elif local == "is":
            self.in_is = max(0, self.in_is - 1)
        elif local == "rPh":
            self.phonetic = max(0, self.phonetic - 1)
        elif local == "row":
            self._emit()
        elif local == "sheetData":
            raise _Stop

    def _row(self, attrs: dict[str, str]) -> None:
        num = _digits(attrs.get("r", ""), 7) or self.num + 1
        if num <= self.num:
            raise Refused("bad_file", "The rows of a sheet are out of order.")
        self.num, self.hidden = num, attrs.get("hidden", "") in ("1", "true")
        self.cells, self.col, self.last = {}, 0, 0

    def _emit(self) -> None:
        self.count += 1
        if self.count % 4096 == 0:
            _check_time(self.deadline)
        if sum(len(text) for _, text in self.cells.values()) > MAX_ROW_CHARS:
            raise Refused("bad_file", f"Row {self.num} holds more than {MAX_ROW_CHARS} characters.")
        if self.cells:
            self.on_row(self.num, self.hidden, self.cells)

    def _value(self) -> tuple[str, str] | None:
        kind, raw = self.kind, "".join(self.buf)
        if kind != "inlineStr" and not raw.strip():
            # No cached value: openpyxl writes a formula with ``<v></v>`` or none.
            return ("f", "") if self.has_f else None
        if kind == "s":
            index = _digits(raw.strip(), 9)
            text = self.strings.get(index) if index is not None else ""
            return ("t", text) if text else None
        if kind in ("str", "inlineStr"):
            text = _unescape(raw).strip()
            return ("t", text) if text else None
        if kind == "b":
            return "b", "true" if raw.strip().lower() in ("1", "true") else "false"
        if kind == "e":
            return "e", ""
        if kind == "d":
            return _iso_cell(raw)
        date = self.styles.get(self.style, False)
        if date is False:
            style = _digits(self.style, 6)
            date = self.kinds[style] if style is not None and style < len(self.kinds) else None
            if len(self.styles) < _CACHE:
                self.styles[self.style] = date
        return _excel_number(raw, date, self.date1904)


def _header_like(row: _Row) -> bool:
    """Every filled cell of the row is text."""
    return all(kind == "t" for kind, text in row[2].values() if text)


def _filled(row: _Row) -> list[int]:
    return [col for col, (_, text) in row[2].items() if text]


def _is_sum(entry: tuple[float, int] | None, value: float) -> bool:
    """*value* is the sum of two numbers or more of a column."""
    return (
        entry is not None and entry[1] >= 2 and abs(entry[0] - value) <= 1e-9 * max(1.0, abs(value))
    )


class _Book:
    """What the sheets of one load share: the stage, the caps and each table found."""

    def __init__(self, stage: Path, header_rows: int | None, caps: tuple[int, int, float]) -> None:
        self.stage, self.header_rows = stage, header_rows
        self.max_rows, self.budget, self.deadline = caps
        self.found: list[dict[str, Any]] = []
        self.sheets: list[_Sheet] = []
        self.outs: list[_Out] = []
        self.tables = self.rows = self.staged = 0

    def add_row(self) -> None:
        self.rows += 1
        if self.rows > self.max_rows:
            raise _too_many_rows(self.max_rows)

    def add_bytes(self, size: int) -> None:
        """The padded stage has the budget of a CSV file (round 3)."""
        self.staged += size
        if self.staged > self.budget:
            raise Refused("bad_file", _TOO_WIDE)

    def close(self) -> None:
        for out in self.outs:
            out.fh.close()


class _Out:
    """The clean CSV copy of a table's rows, as ``_prepare`` writes one for a
    CSV file: each row its source row number, then one field a column."""

    def __init__(self, book: _Book, path: Path, lo: int, width: int) -> None:
        self.book, self.path, self.lo, self.width = book, path, lo, width
        self.fh = open(path, "w", encoding="utf-8", newline="")  # noqa: SIM115 — _Book.close shuts it
        book.outs.append(self)
        self.writer = csv.writer(self.fh, lineterminator="\n")
        self.rows = self.first = self.last = self.hidden_count = 0
        self.hidden: list[int] = []
        self.numbers: list[int] = []
        self.samples: list[list[str]] = [[] for _ in range(width)]
        self.used = bytearray(width)
        self.uncached: Counter[int] = Counter()
        self.errors: Counter[int] = Counter()

    def write(self, row: _Row) -> None:
        num, hidden, cells = row
        values, size = [""] * self.width, 0
        for col, (kind, text) in cells.items():
            j = col - self.lo
            if text:
                values[j], self.used[j] = text, 1
                size += len(text)
                if len(self.samples[j]) < 3 and text not in self.samples[j]:
                    self.samples[j].append(text)
            elif kind == "f":
                self.uncached[j] += 1
            elif kind == "e":
                self.errors[j] += 1
        self.writer.writerow([num, *values])
        self.book.add_row()
        self.book.add_bytes(len(str(num)) + size + self.width + 1)
        self.rows, self.first, self.last = self.rows + 1, self.first or num, num
        self.numbers += [num] if len(self.numbers) < 20 else []
        if hidden:
            self.hidden_count += 1
            self.hidden += [num] if len(self.hidden) < 20 else []


class _Table:
    """One table of a sheet as it streams: its header, its rows and its totals.

    Rules 1 to 6 and 8 of §7.10 work here. A total row goes to the side table
    ``<table>__totals``. A row of column sums waits one row: when a data row
    follows it, it was data.
    """

    def __init__(self, sheet: _Sheet, lo: int, hi: int, titles: list[_Row]) -> None:
        book = sheet.book
        book.tables += 1
        if book.tables > MAX_TABLES:
            message = f"The workbook holds more than {MAX_TABLES} tables. Name one sheet."
            raise Refused("too_many_tables", message)
        self.sheet, self.book, self.lo, self.hi = sheet, book, lo, hi
        self.titles = titles
        self.head_rows: list[_Row] = []
        self.names: list[str] = []
        self.out = _Out(book, book.stage / f"t{book.tables}.csv", lo, hi - lo + 1)
        self.totals: _Out | None = None
        self.held: _Row | None = None
        self.since: dict[int, tuple[float, int]] = {}
        self.above: dict[int, tuple[float, int]] = {}
        self.blank = 0
        self.empty_rows: list[int] = []
        self.notes: list[str] = []
        self.ext = (hi + 1, lo - 1)

    def head(self, row: _Row) -> None:
        self.head_rows.append(row)
        self._extent(row)

    def _extent(self, row: _Row) -> None:
        cols = _filled(row)
        if cols:
            self.ext = (min(self.ext[0], *cols), max(self.ext[1], *cols))

    def wants_header(self, row: _Row) -> bool:
        """Rule 2: the next row is a header row too. The request can name the
        count. Else a header row of text continues under a merged cell that
        spans columns or reaches down, up to three rows."""
        if self.book.header_rows is not None:
            return len(self.head_rows) < self.book.header_rows
        if len(self.head_rows) >= 3 or not _header_like(row):
            return False
        last = self.head_rows[-1][0]
        merges = [m for num, _, _ in self.head_rows for m in self.sheet.merges.get(num, ())]
        return any(
            r1 <= last <= r2 and (c2 > c1 or r2 > last) and c1 <= self.hi and c2 >= self.lo
            for r1, c1, r2, c2 in merges
        )

    def end_header(self) -> None:
        """Rule 2: a merged header cell fills each column under it, and the
        levels join with `` / ``. A level that repeats the level above it is
        that cell, merged down, so it counts once."""
        if self.names:
            return
        lo, width = self.lo, self.hi - self.lo + 1
        grid = [[""] * width for _ in self.head_rows]
        top = self.head_rows[0][0] if self.head_rows else 0
        for i, (num, _, cells) in enumerate(self.head_rows):
            for col, (_, text) in cells.items():
                grid[i][col - lo] = text
            for _, c1, _, c2 in self.sheet.merges.get(num, ()):
                if lo <= c1 <= self.hi:
                    grid[num - top][c1 - lo + 1 : min(c2, self.hi) - lo + 1] = [
                        grid[num - top][c1 - lo]
                    ] * (min(c2, self.hi) - c1)
        for i in range(len(grid) - 1, 0, -1):
            grid[i] = ["" if v == up else v for v, up in zip(grid[i], grid[i - 1], strict=True)]
        self.names = _names(grid, width)

    def continues(self, row: _Row) -> bool:
        """After an empty gap, a data row inside the table's columns continues it."""
        cols = _filled(row)
        return not _header_like(row) and self.ext[0] <= min(cols) and max(cols) <= self.ext[1]

    def data(self, row: _Row) -> None:
        self.end_header()
        cells = row[2]
        first = min((c for c, (kind, text) in cells.items() if kind == "t" and text), default=0)
        keyword = bool(first) and _TOTAL_RE.match(cells[first][1]) is not None
        if self.held is not None:
            held, self.held = self.held, None
            if keyword:
                self._total(held)
            else:
                self._write(held)
        if keyword:
            self._total(row)
        elif self._sums(cells):
            self.held = row
        else:
            self._write(row)

    def _sums(self, cells: dict[int, tuple[str, str]]) -> bool:
        """Rule 4: a row of column sums holds no text, and each of its numbers
        is the sum of its column since the last total row, or since the top.
        One sum is not zero."""
        numbers = []
        for col, (kind, text) in cells.items():
            if kind == "n":
                numbers.append((col, float(text)))
            elif text:
                return False
        return any(v for _, v in numbers) and all(
            _is_sum(self.since.get(c), v) or _is_sum(self.above.get(c), v) for c, v in numbers
        )

    def _write(self, row: _Row) -> None:
        self.out.write(row)
        self._extent(row)
        for col, (kind, text) in row[2].items():
            if kind == "n":
                value = float(text)
                for sums in (self.since, self.above):
                    total, count = sums.get(col, (0.0, 0))
                    sums[col] = (total + value, count + 1)

    def _total(self, row: _Row) -> None:
        """Rule 5: a total row leaves the table, so a sum never counts it twice."""
        if self.totals is None:
            path = self.out.path.with_name(self.out.path.stem + "_totals.csv")
            self.totals = _Out(self.book, path, self.lo, self.hi - self.lo + 1)
        self.totals.write(row)
        self._extent(row)
        self.since = {}

    def end_run(self) -> None:
        """A held row of sums with no data row after it is a total row."""
        if self.held is not None:
            held, self.held = self.held, None
            self._total(held)

    def gap(self, count: int, rows: list[int]) -> None:
        self.end_run()
        self.blank += count
        self.empty_rows += rows[: 20 - len(self.empty_rows)]

    def note(self, rows: list[_Row]) -> None:
        """Rule 6: text under the table after an empty gap is left out."""
        self.end_run()
        self.notes.append(self.sheet.range_of(rows))

    def feed(self, rows: list[_Row]) -> str:
        """Feed rows with no gap. Return the mode of the group after them."""
        for row in rows:
            if not self.head_rows or (not self.names and self.wants_header(row)):
                self.head(row)
            else:
                self.data(row)
        return "data" if self.names else "header"

    def close(self) -> dict[str, Any] | None:
        """The table as ``_load_workbook`` types it, or None when no data row came."""
        self.end_run()
        self.end_header()
        for out in (self.out, self.totals):
            if out is not None:
                out.fh.close()
        if not self.out.rows:
            self.out.path.unlink(missing_ok=True)
            if self.totals is not None:
                self.totals.path.unlink(missing_ok=True)
            self.sheet.text([*self.titles, *self.head_rows], "rows with no data under them")
            return None
        used = {j for j, u in enumerate(self.out.used) if u}
        used |= {c - self.lo for row in self.head_rows for c in _filled(row)}
        if self.totals is not None:
            used |= {j for j, u in enumerate(self.totals.used) if u}
        return self._found(min(used), max(used))

    def _found(self, a: int, b: int) -> dict[str, Any]:
        """Keep the columns *a* to *b*, and drop the empty columns at each edge."""
        outs = [o for o in (self.out, self.totals) if o is not None]
        if (a, b) != (0, self.hi - self.lo):
            for out in outs:
                _keep_columns(out.path, a, b, self.book.deadline)
        sheet, lo = self.sheet, self.lo + a
        head = [r[0] for r in self.head_rows]
        left_out = []
        if self.titles:
            left_out.append({"what": "title rows", "range": sheet.range_of(self.titles)})
        left_out.append({"what": "header rows", "range": sheet.span(lo, lo + b - a, head)})
        if self.blank:
            left_out.append({"what": "empty rows", "count": self.blank, "rows": self.empty_rows})
        left_out += [{"what": "notes", "range": r} for r in self.notes[:20]]
        found = {
            "sheet": sheet, "first_col": lo, "names": self.names[a : b + 1],
            "left_out": left_out, "outs": [(o, o.samples[a : b + 1]) for o in outs],
            "uncached": [self.out.uncached[j] for j in range(a, b + 1)],
            "errors": [self.out.errors[j] for j in range(a, b + 1)],
        }  # fmt: skip
        return found


def _keep_columns(path: Path, a: int, b: int, deadline: float) -> None:
    """Keep the source row number and the columns *a* to *b* of a clean copy."""
    kept = path.with_suffix(".kept")
    with open(kept, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        for _, cells in _records(path, ",", deadline):
            writer.writerow([cells[0], *cells[a + 1 : b + 2]])
    os.replace(kept, path)


class _Group:
    """The rows of one run of used columns, where each table of rule 7 forms.

    A block is the rows between two empty gaps. Rows of one cell each wait in
    ``block`` until a wider row decides them. Above a header, they are title
    rows (rule 1). Under a table, after a gap, they are notes (rule 6). A
    wider row after a gap continues the table above it when it is a data row
    inside the table's columns, so a blank separator row splits no table.
    Any other wider row starts a new table (rule 7).
    """

    def __init__(self, sheet: _Sheet, lo: int, hi: int) -> None:
        self.sheet, self.lo, self.hi = sheet, lo, hi
        self.table: _Table | None = None
        self.mode = "text"  # What the current block feeds: text, header or data.
        self.block: list[_Row] = []
        self.loose: list[list[_Row]] = []  # Earlier blocks of text, not placed yet.
        self.next = 1
        self.gap = self.gap_total = 0
        self.gap_rows: list[int] = []

    def row(self, row: _Row) -> None:
        num = row[0]
        if num > self.next:
            self._empty(self.next, num - 1)
        self.next = num + 1
        filled = len(_filled(row))
        if not filled and not (self.mode == "data" and not self.gap):
            self._empty(num, num)
            return
        self.gap = 0
        table = self.table
        if self.mode == "data" and table is not None:
            table.data(row)
        elif self.mode == "header" and table is not None:
            if table.wants_header(row):
                table.head(row)
            else:
                self.mode = "data"
                table.data(row)
        elif filled < 2:
            self.block.append(row)
            if len(self.block) > TEXT_BLOCK_ROWS:
                self._long_block()
        elif table is not None and table.continues(row):
            self._continue([*self.block, row])
        else:
            self._new_table(row)

    def _empty(self, a: int, b: int) -> None:
        if not self.gap:
            self._end_block()
        self.gap += b - a + 1
        self.gap_total += b - a + 1
        self.gap_rows += range(a, min(b + 1, a + max(0, 20 - len(self.gap_rows))))

    def _end_block(self) -> None:
        if self.table is not None and self.mode == "header":
            self.table.end_header()
        elif self.table is not None and self.mode == "data":
            self.table.end_run()
        elif self.block:
            self.loose.append(self.block)
            self.block = []
            while sum(map(len, self.loose)) > TEXT_BLOCK_ROWS:
                self._place(self.loose.pop(0))
        self.mode = "text"

    def _place(self, block: list[_Row]) -> None:
        """A block of text that no table above it takes is left out."""
        if self.table is not None:
            self.table.note(block)
        else:
            self.sheet.text(block, "text outside a table")

    def _continue(self, rows: list[_Row]) -> None:
        table = self.table
        assert table is not None
        for block in self.loose:
            table.note(block)
        table.gap(self.gap_total, self.gap_rows)
        self.loose, self.block, self.gap_total, self.gap_rows = [], [], 0, []
        self.mode = table.feed(rows)

    def _new_table(self, row: _Row) -> None:
        """Rule 7: a new table. The text just above it is its title rows."""
        if self.table is None:
            titles = [r for block in self.loose for r in block] + self.block
        else:
            notes = self.loose if self.block else self.loose[:-1]
            titles = self.block or (self.loose[-1] if self.loose else [])
            for block in notes:
                self.table.note(block)
            self.sheet.close(self.table)
        self.table = _Table(self.sheet, self.lo, self.hi, titles)
        self.loose, self.block, self.gap_total, self.gap_rows = [], [], 0, []
        self.mode = self.table.feed([row])

    def _long_block(self) -> None:
        """A long block of rows of one cell each: the rows of the table above
        it, or else a table of one column."""
        rows, self.block = self.block, []
        if self.table is not None:
            self._continue(rows)
        else:
            self._one_column(self.loose, rows, [])

    def _one_column(
        self, before: list[list[_Row]], rows: list[_Row], after: list[list[_Row]]
    ) -> None:
        self.table = _Table(self.sheet, self.lo, self.hi, [r for block in before for r in block])
        self.loose, self.gap_total, self.gap_rows = [], 0, []
        self.mode = self.table.feed(rows)
        for block in after:
            self.table.note(block)

    def finish(self) -> None:
        self._end_block()
        if self.table is None and self.loose:
            # No wider row came. The last block of two rows or more is a table of one column.
            tall = [i for i, block in enumerate(self.loose) if len(block) >= 2]
            if not tall:
                for block in self.loose:
                    self.sheet.text(block, "text outside a table")
                return
            i, loose = tall[-1], self.loose
            self._one_column(loose[:i], loose[i], loose[i + 1 :])
        if self.table is not None:
            for block in self.loose:
                self.table.note(block)
            self.sheet.close(self.table)


class _Sheet:
    """One sheet. Its used columns split it into groups, and each group finds
    its own tables (rule 7). Hidden rows, columns and sheets are read, and
    the manifest marks them (rule 9)."""

    def __init__(self, book: _Book, name: str, hidden: bool, survey: _Survey) -> None:
        self.book, self.name, self.hidden = book, name, hidden
        self.ref = _sheet_ref(name)
        self.merges, self.hidden_cols = survey.merges, survey.hidden
        self.left_out: list[dict[str, Any]] = []
        self.table_names: list[str] = []
        self.groups: list[_Group] = []
        self.group_of = array("i", [-1]) * (EXCEL_MAX_COLUMN + 2)
        used, col = survey.used, 1
        while (lo := used.find(1, col)) != -1:
            hi = used.find(0, lo) - 1
            if hi - lo + 1 > MAX_COLUMNS:
                raise Refused("too_many_columns", f"A table has more than {MAX_COLUMNS} columns.")
            self.group_of[lo : hi + 1] = array("i", [len(self.groups)]) * (hi - lo + 1)
            self.groups.append(_Group(self, lo, hi))
            col = hi + 1

    def row(self, num: int, hidden: bool, cells: dict[int, tuple[str, str]]) -> None:
        if len(self.groups) == 1:
            self.groups[0].row((num, hidden, cells))
            return
        parts: dict[int, dict[int, tuple[str, str]]] = {}
        for col, value in cells.items():
            at = self.group_of[col]
            if at >= 0:
                parts.setdefault(at, {})[col] = value
        for at, part in parts.items():
            self.groups[at].row((num, hidden, part))

    def finish(self) -> None:
        for group in self.groups:
            group.finish()

    def close(self, table: _Table) -> None:
        found = table.close()
        if found is not None:
            self.book.found.append(found)

    def span(self, lo: int, hi: int, rows: list[int]) -> str:
        return f"{self.ref}!{_letter(lo - 1)}{rows[0]}:{_letter(hi - 1)}{rows[-1]}"

    def range_of(self, rows: list[_Row]) -> str:
        cols = [c for row in rows for c in _filled(row)] or [1]
        return self.span(min(cols), max(cols), [rows[0][0], rows[-1][0]])

    def text(self, rows: list[_Row], what: str) -> None:
        if rows and len(self.left_out) < 20:
            self.left_out.append({"what": what, "range": self.range_of(rows)})


def _book_parts(book: zipfile.ZipFile, deadline: float) -> tuple[_Workbook, str | None, str | None]:
    """The workbook part, with its sheets, and the parts of the shared strings
    and the styles. Each part must be a normal path inside ``xl/``."""
    main = "xl/workbook.xml"
    if _has(book, "_rels/.rels"):
        rels = _Rels()
        _parse_part(book, "_rels/.rels", rels, deadline, XML_SMALL_PART_BYTES)
        main = next((t.lstrip("/") for _, k, t in rels.found if k == "officeDocument"), main)
    main = _xl_part(main)
    folder = posixpath.dirname(main)
    links = _Rels()
    rels_name = posixpath.join(folder, "_rels", posixpath.basename(main) + ".rels")
    if _has(book, rels_name):
        _parse_part(book, rels_name, links, deadline, XML_SMALL_PART_BYTES)
    sheets: dict[str, str] = {}
    named: dict[str, str] = {}
    for rid, kind, target in links.found:
        if kind == "worksheet":
            sheets.setdefault(rid, _target(folder, target))
        elif kind in ("sharedStrings", "styles"):
            named.setdefault(kind, _target(folder, target))
    for kind, default in (("sharedStrings", "xl/sharedStrings.xml"), ("styles", "xl/styles.xml")):
        if kind not in named and _has(book, default):
            named[kind] = default
    # No sheet part is the workbook part, the strings or the styles.
    others = {main, *named.values()}
    workbook = _Workbook({rid: p for rid, p in sheets.items() if p not in others})
    _parse_part(book, main, workbook, deadline, XML_SMALL_PART_BYTES)
    workbook.cut_names()
    return workbook, named.get("sharedStrings"), named.get("styles")


def _has(book: zipfile.ZipFile, name: str) -> bool:
    try:
        book.getinfo(name)
    except KeyError:
        return False
    return True


def _xl_part(name: str) -> str:
    """*name* when it is a normal path inside ``xl/``, else a refusal (the rule of EM-T11b)."""
    norm = posixpath.normpath(name)
    if norm != name or not norm.startswith("xl/"):
        raise Refused("bad_file", _OUTSIDE)
    return norm


def _target(folder: str, target: str) -> str:
    """The part that a relationship names. A target that starts with ``/``
    starts at the root of the zip, and any other at the workbook's folder."""
    joined = target.lstrip("/") if target.startswith("/") else posixpath.join(folder, target)
    return _xl_part(posixpath.normpath(joined))


def _pick_sheets(
    sheets: list[tuple[str, bool, str]], wanted: str | None
) -> list[tuple[str, bool, str]]:
    if wanted is None:
        if len(sheets) > MAX_SHEETS:
            message = f"The workbook has more than {MAX_SHEETS} sheets. Name one sheet to load."
            raise Refused("too_many_sheets", message)
        return sheets
    found = [s for s in sheets if s[0] == wanted] or [
        s for s in sheets if s[0].casefold() == wanted.casefold()
    ]
    if not found:
        names = ", ".join(_short(s[0]) for s in sheets[:MAX_SHEETS])
        raise Refused("not_found", f"The workbook has no sheet of that name. Its sheets: {names}")
    return found[:1]


def _load_workbook(
    path: Path,
    stage: Path,
    ds_id: str,
    label: str,
    options: dict[str, Any],
    caps: tuple[int, int, float],
) -> dict[str, Any]:
    """Load each table of each sheet of a workbook (WS-43y1b)."""
    deadline = caps[2]
    header_rows = options["header_rows"] if options["header_rows"] != "auto" else None
    book = _Book(stage, header_rows, caps)
    try:
        with _open_book(path) as zf:
            workbook, strings_part, styles_part = _book_parts(zf, deadline)
            sheets = _pick_sheets(workbook.sheets, options["sheet"])
            strings, styles = _Strings(), _Styles()
            if strings_part:
                _parse_part(zf, strings_part, strings, deadline)
            if styles_part:
                _parse_part(zf, styles_part, styles, deadline, XML_SMALL_PART_BYTES)
            kinds = styles.kinds()
            for name, hidden, part in sheets:
                survey = _Survey()
                _parse_part(zf, part, survey, deadline)
                sheet = _Sheet(book, name, hidden, survey)
                book.sheets.append(sheet)
                _parse_part(
                    zf,
                    part,
                    _Rows(strings, kinds, workbook.date1904, sheet.row, deadline),
                    deadline,
                )
                sheet.finish()
            del strings
    finally:
        book.close()
    if not book.found:
        raise Refused("empty", "The workbook holds no table with data rows under a header.")
    tables = _type_tables(book, stage, deadline)
    sheet_list = [
        {"name": s.name, "hidden": s.hidden, "tables": s.table_names, "left_out": s.left_out}
        for s in book.sheets
    ]
    return {
        "dataset_id": ds_id, "source": label, "findings": [], "tables": tables,
        "sheets": sheet_list, "date_system": 1904 if workbook.date1904 else 1900,
    }  # fmt: skip


def _type_tables(book: _Book, stage: Path, deadline: float) -> list[dict[str, Any]]:
    """Type each table and its side table of totals, as a CSV file's table is typed."""
    con = _load_connection(stage)
    taken: set[str] = set()
    tables: list[dict[str, Any]] = []
    for found in book.found:
        sheet: _Sheet = found["sheet"]
        name = _table_name(con, sheet.name, taken)
        for k, (out, samples) in enumerate(found["outs"]):
            table = name if k == 0 else f"{name}__totals"
            taken.add(table)
            seen = {"names": found["names"], "samples": samples, "rows": out.rows}
            _, cols = _typed(stage, out.path, table, seen, False, deadline)
            entry = _table_entry(found, out, table, cols)
            if k == 0:
                sheet.table_names.append(table)
                tables.append(entry)
                continue
            entry["totals_of"] = name
            total_rows = {"what": "total rows", "table": table, "count": out.rows}
            tables[-1]["left_out"].append(total_rows | {"rows": out.numbers})
            tables.append(entry)
    con.close()
    return tables


def _table_entry(
    found: dict[str, Any], out: _Out, table: str, cols: list[dict[str, Any]]
) -> dict[str, Any]:
    """The manifest entry of one table of a workbook."""
    sheet: _Sheet = found["sheet"]
    lo, main = found["first_col"], not table.endswith("__totals")
    for j, col in enumerate(cols):
        if sheet.hidden_cols[lo + j]:
            col["hidden"] = True
        if main and found["uncached"][j]:
            col["uncached_formulas"] = found["uncached"][j]
        if main and found["errors"][j]:
            col["error_values"] = found["errors"][j]
    entry: dict[str, Any] = {
        "name": table, "sheet": sheet.name, "file": f"{table}.parquet", "rows": out.rows,
        "range": sheet.span(lo, lo + len(cols) - 1, [out.first, out.last]), "columns": cols,
        "left_out": found["left_out"] if main else [], "first_col": lo, "hidden": sheet.hidden,
    }  # fmt: skip
    if out.hidden_count:
        entry["hidden_rows"] = {"count": out.hidden_count, "rows": out.hidden}
    return entry


# --------------------------------------------------------------------------
# The verbs.
# --------------------------------------------------------------------------


def _load(dirs: Dirs, req: dict[str, Any], rid: str) -> dict[str, Any]:
    source = req.get("source")
    if not isinstance(source, str) or not source or ".." in Path(source).parts:
        raise Refused("bad_request", "source must name a file in the run-data dir.")
    path = (dirs.run / source).resolve()
    if not path.is_relative_to(dirs.run.resolve()) or not path.is_file():
        raise Refused("not_found", "The source file is not in the run-data dir.")
    label = str(req.get("name") or path.name)
    ext = Path(label).suffix.lower()
    if ext in _REFUSED_KINDS:
        raise Refused("unsupported_kind", _REFUSED_KINDS[ext])
    if ext not in _KINDS:
        message = f"The engine reads .csv, .tsv, .xlsx and .xlsm, not {ext or 'this'}."
        raise Refused("unsupported_kind", message)
    max_bytes, options = _limit(req, "max_file_bytes"), _load_options(req, ext)
    if path.stat().st_size > max_bytes:
        raise Refused("too_large", f"The file is larger than {max_bytes // 2**20} MB.")
    deadline = time.monotonic() + _timeout(req, "load")
    # The id is a SHA-256 of the bytes and the load options. A second load reads nothing.
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(2**20):
            digest.update(chunk)
    digest.update(b"\0" + json.dumps(options, sort_keys=True).encode())
    ds_id = "ds_" + digest.hexdigest()[:32]
    if (dirs.data / ds_id / "manifest.json").is_file():
        return {"reused": True, **_summary(_manifest(dirs, ds_id))}
    dirs.data.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".stage-{rid}-", dir=dirs.data))
    caps = (_limit(req, "max_rows"), PAD_FACTOR * max_bytes, deadline)
    try:
        if ext in (".xlsx", ".xlsm"):
            manifest = _load_workbook(path, stage, ds_id, label, options, caps)
        else:
            manifest = _load_csv(path, stage, ds_id, label, options["header_rows"], caps)
        manifest.update(kind=ext[1:], header_rows=options["header_rows"])
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        shutil.rmtree(stage / "spill", ignore_errors=True)
        for file in stage.iterdir():
            file.chmod(0o444)
        os.replace(stage, dirs.data / ds_id)
        # Rule 4: a query may not write the dataset. Root owns no part of it, so
        # the files and the dir are read-only for the uid of the container.
        (dirs.data / ds_id).chmod(0o555)
    finally:
        _remove(stage)
    return {"reused": False, **_summary(manifest)}


def _load_options(req: dict[str, Any], ext: str) -> dict[str, Any]:
    """The options of a load, which its id hashes. A CSV file keeps the one
    option of WS-43y1a, so its id does not change.

    A workbook finds its header rows itself (``auto``) unless the request
    names them, and it reads every sheet unless the request names one.
    """
    sheet = req.get("sheet")
    if ext not in (".xlsx", ".xlsm"):
        if sheet is not None:
            raise Refused(
                "bad_request", "sheet names a sheet of a workbook, and not of a CSV file."
            )
        return {"header_rows": _limit(req, "header_rows")}
    if sheet is not None and not (isinstance(sheet, str) and 0 < len(sheet) <= MAX_NAME_CHARS):
        raise Refused(
            "bad_request", f"sheet must be a sheet name of 1 to {MAX_NAME_CHARS} characters."
        )
    named = req.get("header_rows") is not None
    return {"header_rows": _limit(req, "header_rows") if named else "auto", "sheet": sheet}


def _load_csv(
    path: Path,
    stage: Path,
    ds_id: str,
    label: str,
    header_rows: int,
    caps: tuple[int, int, float],
) -> dict[str, Any]:
    """Load a CSV or TSV file (WS-43y1a)."""
    max_rows, budget, deadline = caps
    text, raw = stage / "source.csv", stage / "rows.csv"
    encoding = _transcode(path, text, deadline)
    with open(text, encoding="utf-8", newline="") as fh:
        delim = _delimiter(fh.read(2**16), Path(label).suffix.lower())
    seen = _prepare(text, raw, delim, header_rows, max_rows, budget, deadline)
    text.unlink()
    manifest = _write_dataset(stage, raw, ds_id, label, delim, seen, deadline)
    manifest["encoding"] = encoding
    return manifest


def _remove(path: Path) -> None:
    if path.exists():
        path.chmod(stat.S_IRWXU)
        for child in path.iterdir():
            child.chmod(stat.S_IRUSR | stat.S_IWUSR)
        shutil.rmtree(path, ignore_errors=True)


#: The keys of a table that a load answer carries. A workbook adds the last four.
_SUMMARY_KEYS = ("name", "rows", "range", "left_out", "sheet", "hidden", "hidden_rows", "totals_of")


def _summary(manifest: dict[str, Any]) -> dict[str, Any]:
    tables = [
        {key: t[key] for key in _SUMMARY_KEYS if key in t}
        | {"columns": [{"name": _short(c["name"]), "type": c["type"]} for c in t["columns"]]}
        for t in manifest["tables"]
    ]
    answer = {
        "dataset_id": manifest["dataset_id"],
        "findings": manifest["findings"],
        "tables": tables,
    }
    if "sheets" in manifest:
        answer["sheets"] = manifest["sheets"]
    return answer


def _manifest(dirs: Dirs, ds_id: Any) -> dict[str, Any]:
    if not isinstance(ds_id, str) or not _DATASET_RE.fullmatch(ds_id):
        raise Refused("bad_request", "dataset_id is not a dataset id.")
    path = dirs.data / ds_id / "manifest.json"
    if not path.is_file():
        raise Refused("not_found", "No dataset has this id in this thread. Load the file again.")
    manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return manifest


def _open_locked(dirs: Dirs, ds_id: Any) -> tuple[duckdb.DuckDBPyConnection, dict[str, Any]]:
    """Open the dataset as views, then lock the connection (§7.10 SQL rules 2 to 5).

    The pinned DuckDB has ``allowed_paths``, so rule 5's fallback has no code.
    WS43-F26 fails when a pin move takes the setting away.
    """
    manifest = _manifest(dirs, ds_id)
    con = duckdb.connect(":memory:", config={"threads": "1"})
    paths = [(dirs.data / ds_id / t["file"]).resolve().as_posix() for t in manifest["tables"]]
    for table, path in zip(manifest["tables"], paths, strict=True):
        con.execute(f"CREATE VIEW {_q(table['name'])} AS SELECT * FROM read_parquet({_lit(path)})")
    spill = _spill_dir().as_posix()
    profile = {
        "OPERATOR_ROWS_SCANNED": "true",
        "OPERATOR_CARDINALITY": "true",
        "EXTRA_INFO": "true",
    }
    for statement in (
        f"SET memory_limit = '{_duckdb_memory_mb()}MB'",
        "SET threads = 1",
        f"SET temp_directory = {_lit(spill)}",
        "SET max_temp_directory_size = '128MB'",
        "SET enable_profiling = 'no_output'",
        f"SET custom_profiling_settings = {_lit(json.dumps(profile))}",
        # Rule 3, in this order. A dir, and not the files, would let COPY write into it.
        f"SET allowed_paths = [{', '.join(_lit(p) for p in paths)}]",
        "SET autoinstall_known_extensions = false",
        "SET autoload_known_extensions = false",
        "SET enable_external_access = false",
        "SET lock_configuration = true",
    ):
        con.execute(statement)
    return con, manifest


def _check_sql(sql: Any) -> str:
    """Rule 1: one statement of type SELECT, that starts with a query word."""
    if not isinstance(sql, str) or not sql.strip():
        raise Refused("bad_request", "sql must be one SELECT query.")
    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error as exc:
        raise Refused("sql_error", str(exc)[:500]) from exc
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise Refused("one_select", _ONE_SELECT)
    # The tokenizer gives byte offsets, so the slice is of the UTF-8 bytes.
    tokens = duckdb.tokenize(sql)
    word = re.match(rb"\(|\w*", sql.encode("utf-8")[tokens[0][0] :]) if tokens else None
    if not word or word.group(0).decode("ascii").lower() not in _FIRST_WORDS:
        raise Refused("one_select", _ONE_SELECT)
    return str(statements[0].query).strip().rstrip(";")


def _timed(
    con: duckdb.DuckDBPyConnection, timeout: int, work: Callable[[], Any], what: str = "query"
) -> Any:
    """Rule 7: a timer interrupts the work at the time cap.

    An interrupt during a fetch can surface as another DuckDB error, so any
    error after the timer fired is the time cap (round 2).
    """
    fired = threading.Event()

    def stop() -> None:
        fired.set()
        con.interrupt()

    timer = threading.Timer(timeout, stop)
    timer.start()
    try:
        return work()
    except duckdb.OutOfMemoryException:
        if fired.is_set():
            raise Refused("time", f"The {what} passed the time cap of {timeout} s.") from None
        raise
    except duckdb.Error as exc:
        if fired.is_set() or isinstance(exc, duckdb.InterruptException):
            raise Refused("time", f"The {what} passed the time cap of {timeout} s.") from exc
        raise Refused("sql_error", str(exc)[:500]) from exc
    finally:
        timer.cancel()


def _cell(value: Any) -> tuple[Any, bool]:
    """Return a JSON value for one cell, and whether the cell cap cut it."""
    if isinstance(value, (bool, int)) or value is None:
        return value, False
    if isinstance(value, float):
        return (value if value == value and abs(value) != float("inf") else str(value)), False
    text = value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    return text[:MAX_CELL_CHARS], len(text) > MAX_CELL_CHARS


def _scanned(con: duckdb.DuckDBPyConnection, manifest: dict[str, Any], sql: str) -> dict[str, int]:
    """The rows of each table that the last query read, from its profile.

    A late-materialized plan scans a table twice, so each table counts its
    largest scan, and not the sum. A LIMIT can stop a scan early, and then
    the profile reads 0 rows scanned, so the rows that a scan gave out are a
    floor. A plan that answers from the Parquet statistics, such as
    ``count(*)``, has no scan. It read the whole table, so it counts the rows
    of the manifest.
    """
    tables = {t["name"].lower(): t for t in manifest["tables"]}
    files = {t["file"]: t["name"] for t in manifest["tables"]}
    out: dict[str, int] = {}
    stack = [json.loads(con.get_profiling_information())]
    while stack:
        node = stack.pop()
        stack.extend(node.get("children", []))
        name = Path(str((node.get("extra_info") or {}).get("Filename(s)", ""))).name
        if node.get("operator_type") == "TABLE_SCAN" and name in files:
            seen = node.get("operator_rows_scanned") or 0, node.get("operator_cardinality") or 0
            out[files[name]] = max(out.get(files[name], 0), *(int(n) for n in seen))
    named = _named_tables(sql)
    for key in named & tables.keys():
        out.setdefault(tables[key]["name"], tables[key]["rows"])
    return out


def _named_tables(sql: str) -> set[str]:
    """The tables that *sql* names, read by a parser that can reach no file.

    ``get_table_names`` binds the query. On the dataset's connection it binds
    the views to their files and answers nothing, and on the default
    connection it could read a file. So an empty connection with no external
    access reads the names.
    """
    con = duckdb.connect(":memory:", config={"threads": "1"})
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    try:
        return {n.lower() for n in con.get_table_names(sql)}
    except duckdb.Error:
        return set()
    finally:
        con.close()


def _spill_dir() -> Path:
    """DuckDB's spill dir for this process. DuckDB adds it to the allowed dirs,
    so it is new for each process, and ``_serve`` removes it."""
    return Path(tempfile.gettempdir(), f"data-engine-spill-{os.getpid()}")


def _head(
    manifest: dict[str, Any],
    table: str | None,
    sql: str,
    scanned: dict[str, int],
    desc: Any,
    count: int,
    reasons: list[str],
    elapsed: int,
) -> dict[str, Any]:
    """Every field of the result envelope (§7.10), with no rows yet."""
    tables = {t["name"]: t for t in manifest["tables"]}
    ranges = [{"table": n, "range": tables[n]["range"], "left_out": tables[n]["left_out"]}
              for n in sorted(scanned)]  # fmt: skip
    return {
        "dataset_id": manifest["dataset_id"], "table": table, "query": sql,
        "rows_scanned": scanned, "rows_returned": count,
        "truncated": bool(reasons), "truncated_reason": reasons,
        "columns": [{"name": _short(d[0]), "type": str(d[1])} for d in desc],
        "rows": [], "source_ranges": ranges, "elapsed_ms": elapsed,
    }  # fmt: skip


def _envelope(
    con: duckdb.DuckDBPyConnection,
    manifest: dict[str, Any],
    inner: str,
    cap: int,
    timeout: int,
    table: str | None = None,
) -> dict[str, Any]:
    """Run *inner* under the row cap, with each long value cut in SQL (rules 6, 7)."""
    started = time.monotonic()

    def work() -> tuple[Any, str, list[Any]]:
        desc = con.execute(f"SELECT * FROM (\n{inner}\n) LIMIT 0").description
        cols = [
            f"c{i}"
            if _SHORT_TYPE.fullmatch(str(d[1]))
            else f"left(CAST(c{i} AS VARCHAR), {MAX_CELL_CHARS + 1})"
            for i, d in enumerate(desc)
        ]
        aliases = ", ".join(f"c{i}" for i in range(len(desc)))
        # The newlines keep a trailing comment off the cap.
        sql = f"SELECT {', '.join(cols)} FROM (\n{inner}\n) AS _q({aliases}) LIMIT {cap + 1}"
        return desc, sql, con.execute(sql).fetchmany(cap + 1)

    desc, sql, rows = _timed(con, timeout, work)
    elapsed = int((time.monotonic() - started) * 1000)
    reasons = ["rows"] if len(rows) > cap else []
    cells = [[_cell(v) for v in row] for row in rows[:cap]]
    if any(cut for row in cells for _, cut in row):
        reasons.append("cells")
    scanned = _scanned(con, manifest, inner)
    answer = _head(manifest, table, sql, scanned, desc, len(cells), reasons, elapsed)
    answer["rows"] = [[v for v, _ in row] for row in cells]
    tables, names = {t["name"]: t for t in manifest["tables"]}, [d[0] for d in desc]
    only = table or (next(iter(scanned)) if len(scanned) == 1 else None)
    if "_src_row" in names and only:
        # A row that carries its source row gets its cell reference. A table of a
        # workbook can start in any column, and the CSV tables start in A.
        t, at = tables[only], names.index("_src_row")
        lo = t.get("first_col", 1) - 1
        first, last = _letter(lo), _letter(lo + len(t["columns"]) - 1)
        sheet = _sheet_ref(t["sheet"])
        answer["cells"] = [f"{sheet}!{first}{r[at]}:{last}{r[at]}" for r in rows[:cap]]
    return answer


def _sheet_ref(name: str) -> str:
    """A sheet name as a cell reference writes it: quoted when it holds more
    than letters, digits and ``_``, as ``'Q1 Sales'!A3``."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return name
    return "'" + name.replace("'", "''") + "'"


def _table(manifest: dict[str, Any], name: Any) -> dict[str, Any]:
    tables: list[dict[str, Any]] = manifest["tables"]
    for table in tables:
        if table["name"] == name:
            return table
    raise Refused("not_found", f"The dataset has no table {name!r}.")


def _query(dirs: Dirs, req: dict[str, Any], rid: str) -> dict[str, Any]:
    sql = _check_sql(req.get("sql"))
    cap, timeout = _limit(req, "max_result_rows"), _timeout(req, "query")
    con, manifest = _open_locked(dirs, req.get("dataset_id"))
    return _envelope(con, manifest, sql, cap, timeout)


def _preview(dirs: Dirs, req: dict[str, Any], rid: str) -> dict[str, Any]:
    con, manifest = _open_locked(dirs, req.get("dataset_id"))
    table = _table(manifest, req.get("table"))
    limit, offset = _limit(req, "limit"), req.get("offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise Refused("bad_request", "offset must be a whole number of 0 or more.")
    sql = f"SELECT * FROM {_q(table['name'])} ORDER BY _src_row LIMIT {limit + 1} OFFSET {offset}"
    return _envelope(con, manifest, sql, limit, _timeout(req, "preview"), table["name"])


def _profile(dirs: Dirs, req: dict[str, Any], rid: str) -> dict[str, Any]:
    con, manifest = _open_locked(dirs, req.get("dataset_id"))
    table = _table(manifest, req.get("table"))
    col = next((c for c in table["columns"] if c["name"] == req.get("column")), None)
    if col is None:
        raise Refused("not_found", "The table has no column of that name.")
    c, t, timeout = _q(col["name"]), _q(table["name"]), _timeout(req, "profile")
    stats = f"count(*) AS rows, count(*) - count({c}) AS empty, count(DISTINCT {c}) AS distinct"
    if col["type"] in ("integer", "decimal"):
        stats += f", min({c}) AS minimum, max({c}) AS maximum, avg({c}) AS mean"
        stats += f", median({c}) AS median, quantile_cont({c}, [0.05, 0.95]) AS p5_p95"
    answer = _envelope(con, manifest, f"SELECT {stats} FROM {t}", 1, timeout, table["name"])
    top = f"SELECT left(CAST({c} AS VARCHAR), {MAX_CELL_CHARS + 1}), count(*) FROM {t}"
    top += f" WHERE {c} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 20"
    rows = _timed(con, timeout, lambda: con.execute(top).fetchall())
    answer["top_values"] = [[_cell(v)[0], n] for v, n in rows]
    answer["query"] += ";\n" + top
    return answer


def _export(dirs: Dirs, req: dict[str, Any], rid: str) -> dict[str, Any]:
    sql, fmt = _check_sql(req.get("sql")), req.get("format")
    if fmt not in ("csv", "xlsx"):
        raise Refused("bad_request", "format must be csv or xlsx.")
    stem = str(req.get("name") or "export").removesuffix(f".{fmt}")
    if not _EXPORT_NAME_RE.fullmatch(stem):
        raise Refused("bad_request", "name may hold letters, digits, spaces, '.', '_' and '-'.")
    cap = min(_limit(req, "max_rows"), XLSX_MAX_ROWS if fmt == "xlsx" else 10**8)
    quota = _limit(req, "workspace_quota_mb") * 2**20
    con, manifest = _open_locked(dirs, req.get("dataset_id"))
    dirs.outputs.mkdir(parents=True, exist_ok=True)
    # The file grows under a part name, and takes its own name only when it is whole.
    part = dirs.outputs / f".export-{rid}.part"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    # openpyxl spools each sheet to the temp dir, which is the child's own dir.
    spool = tempfile.gettempdir() if fmt == "xlsx" else dirs.outputs
    room = min(quota, int(shutil.disk_usage(spool).free * 0.8))
    wrapped, timeout = f"SELECT * FROM (\n{sql}\n) LIMIT {cap + 1}", _timeout(req, "export")
    started = time.monotonic()
    try:
        with os.fdopen(os.open(part, flags, 0o644), "wb") as fh:
            desc, count, cut = _timed(
                con, timeout, lambda: _rows_to(con.execute(wrapped), fh, fmt, cap, room)
            )
        names = [f"{stem}.{fmt}"] + [f"{stem}-{k}.{fmt}" for k in range(2, 100)]
        name = next((n for n in names if not (dirs.outputs / n).exists()), "")
        if not name:
            raise Refused("exists", "Too many exports have this name. Give another name.")
        os.replace(part, dirs.outputs / name)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    reasons = ["rows"] if count > cap else []
    reasons += ["cells"] if cut else []
    elapsed = int((time.monotonic() - started) * 1000)
    scanned = _scanned(con, manifest, sql)
    answer = _head(manifest, None, wrapped, scanned, desc, min(count, cap), reasons, elapsed)
    answer["file"] = f"outputs/{name}"
    if fmt == "xlsx":
        answer.update(row_ceiling=XLSX_MAX_ROWS, cells_cut=cut)
    return answer


def _xlsx_value(value: Any) -> Any:
    """A value that openpyxl writes as itself. Any other type becomes its text.

    A string keeps at most ``XLSX_MAX_CELL_CHARS``, the cell limit of Excel.
    """
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE  # type: ignore[import-untyped]

    if isinstance(value, str):
        return ILLEGAL_CHARACTERS_RE.sub("", value)[:XLSX_MAX_CELL_CHARS]
    if isinstance(value, float) and (value != value or abs(value) == float("inf")):
        return str(value)
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float, Decimal, date)):
        return value
    text = value.hex() if isinstance(value, bytes) else str(value)
    return text[:XLSX_MAX_CELL_CHARS]


def _xml_bytes(value: Any) -> int:
    """The bytes that one cell takes in the sheet's XML, with its escapes."""
    text = str(value)
    escapes = 4 * text.count("&") + 3 * (text.count("<") + text.count(">"))
    return len(text.encode("utf-8")) + escapes + 48


def _rows_to(cursor: Any, fh: Any, fmt: str, cap: int, room: int) -> tuple[Any, int, int]:
    """Stream the rows to the export file.

    Return the columns, the rows that the query gave and the ``.xlsx`` cells
    that the cell limit cut. Each header cell and each data cell is guarded.
    A CSV cell that starts with a formula mark gets a ``'`` first. An
    ``.xlsx`` string is a string, and never a formula. *room* bounds the
    bytes that the export writes, XML escapes included.
    """
    from openpyxl import Workbook  # type: ignore[import-untyped]
    from openpyxl.cell import WriteOnlyCell  # type: ignore[import-untyped]

    text = io.TextIOWrapper(fh, encoding="utf-8", newline="")
    writer, book, count, used, cut = csv.writer(text), Workbook(write_only=True), 0, 0, 0
    sheet = book.create_sheet("export")

    def write(row: Any) -> None:
        nonlocal used, cut
        used += sum(_xml_bytes(v) for v in row)
        if used > room:
            raise Refused("too_large", f"The export passed the space for it ({room // 2**20} MB).")
        if fmt == "csv":
            writer.writerow(
                ["'" + v if isinstance(v, str) and v.startswith(_FORMULA_FIRST) else v for v in row]
            )
            return
        cells = []
        for v in row:
            cell = WriteOnlyCell(sheet, _xlsx_value(v))
            if isinstance(cell.value, str):
                cell.data_type = "s"
                cut += len(str(v)) > XLSX_MAX_CELL_CHARS
            cells.append(cell)
        sheet.append(cells)

    desc = cursor.description
    write([d[0] for d in desc])
    while batch := cursor.fetchmany(1000):
        for row in batch[: max(0, cap - count)]:
            write(row)
        count += len(batch)
    text.flush()
    text.detach()
    if fmt == "xlsx":
        book.save(fh)
    return desc, count, cut


VERBS = dict(load=_load, query=_query, preview=_preview, profile=_profile, export=_export)


def _serve(dirs: Dirs, verb: str, req: dict[str, Any], rid: str) -> dict[str, Any]:
    """Run one verb, and turn every outcome into an answer."""
    try:
        return _serve_verb(dirs, verb, req, rid)
    finally:
        shutil.rmtree(_spill_dir(), ignore_errors=True)


def _serve_verb(dirs: Dirs, verb: str, req: dict[str, Any], rid: str) -> dict[str, Any]:
    try:
        return {"ok": True, "verb": verb, **VERBS[verb](dirs, req, rid)}
    except Refused as exc:
        answer = {"ok": False, "verb": verb, "error": exc.code, "message": exc.message}
        if exc.code == "time":
            answer.update(truncated=True, truncated_reason=["time"], rows=[])
        return answer
    except (MemoryError, duckdb.OutOfMemoryException):
        return _memory_answer(verb)
    except Exception as exc:  # lxml, openpyxl and the file system fail in many types.
        message = f"{type(exc).__name__}: {exc}"[:500]
        return {"ok": False, "verb": verb, "error": "engine_error", "message": message}


def _memory_answer(verb: str) -> dict[str, Any]:
    if verb == "load":
        message = "The load passed the memory cap of the engine. The file is too wide or too"
        message += " large for one dataset. Split it, or save fewer columns, and load it again."
    else:
        message = "The work passed the memory cap of the engine. Ask for less, or add a filter."
    return {"ok": False, "verb": verb, "error": "memory", "message": message}


def _limit_arenas() -> None:
    """Cap glibc at two malloc arenas, before DuckDB starts its threads.

    Each thread that allocates gets an arena that reserves 64 MB of address
    space. DuckDB's import starts a thread for each core. Measured in the
    image under ``--memory 1g`` on 2026-10-06: 760 MB before any query.
    """
    import ctypes

    with contextlib.suppress(OSError, AttributeError):
        ctypes.CDLL("libc.so.6").mallopt(-8, 2)  # M_ARENA_MAX


def _rss(pid: int) -> int:
    """The resident bytes of a process, from ``/proc``. 0 where there is none."""
    try:
        pages = int(Path(f"/proc/{pid}/statm").read_text(encoding="ascii").split()[1])
    except (OSError, ValueError, IndexError):
        return 0
    size: int = os.sysconf("SC_PAGE_SIZE")  # type: ignore[attr-defined, unused-ignore]
    return pages * size


def _kill(child: subprocess.Popen[bytes]) -> None:
    """Kill the child. On Windows a venv's python.exe is a launcher, so the kill
    takes its tree (the host tests only). In the image it is one process."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(child.pid)], capture_output=True, check=False
        )
    child.kill()


def _watch(child: subprocess.Popen[bytes], wall: float) -> str | None:
    """Wait for *child*. Kill it at the wall clock (``time``) or at the memory cap
    of resident bytes (``memory``). Return the cause of a kill, or None."""
    deadline, cap = time.monotonic() + wall, _budget() * 3 // 4
    while child.poll() is None:
        cause = (
            "time" if time.monotonic() > deadline else "memory" if _rss(child.pid) > cap else None
        )
        if cause:
            _kill(child)
            child.wait()
            return cause
        time.sleep(0.02)
    return None


def _isolated(dirs: Dirs, verb: str, req: dict[str, Any], rid: str, timeout: int) -> dict[str, Any]:
    """Run the verb in a child: a fresh interpreter, never a fork.

    The parent may hold threads (a test that imported DuckDB does), and a
    fork of a process with threads can deadlock. The parent kills the child
    at the time cap plus ``KILL_MARGIN_SECONDS``, or when its resident memory
    passes 75% of the container. It then removes the child's temp dir, its
    part file and its stage dir, and it always writes the answer itself.
    """
    out = dirs.run / "answers" / f".{rid}.child"
    err = dirs.run / "answers" / f".{rid}.err"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    argv = [sys.executable, "-I", str(Path(__file__).resolve()), "--child", verb, rid]
    argv += [str(d.resolve()) for d in (dirs.run, dirs.data, dirs.outputs)]
    with open(err, "wb") as stderr:
        child = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr
        )
        cause = _watch(child, timeout + KILL_MARGIN_SECONDS)
    try:
        answer = json.loads(out.read_text(encoding="utf-8")) if child.returncode == 0 else None
    except (OSError, ValueError):
        answer = None
    trace = err.read_text(encoding="utf-8", errors="replace")[-2000:]
    _remove_soon(out, err, _scratch_dir(rid))
    if answer is not None:
        return dict(answer)
    (dirs.outputs / f".export-{rid}.part").unlink(missing_ok=True)
    for stage in dirs.data.glob(f".stage-{rid}-*"):
        _remove(stage)
    if cause == "time":
        message = f"The work passed the time cap of {timeout} s."
        return {"ok": False, "verb": verb, "error": "time", "message": message,
                "truncated": True, "truncated_reason": ["time"], "rows": []}  # fmt: skip
    # A kill by the watch or by the kernel (SIGKILL, -9), or a trace of a
    # failed allocation, is the memory cap. Any other exit is an engine error.
    evidence = ("MemoryError", "bad_alloc", "Cannot allocate memory", "Out of Memory")
    if cause == "memory" or child.returncode == -9 or any(e in trace for e in evidence):
        return _memory_answer(verb)
    message = f"The engine stopped with exit code {child.returncode}."
    return {"ok": False, "verb": verb, "error": "engine_error", "message": message}


def _remove_soon(*paths: Path) -> None:
    """Remove each path. Windows frees the files of a killed child a moment late."""
    for _ in range(50):
        for path in paths:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)
        if not any(path.exists() for path in paths):
            return
        time.sleep(0.1)


def _scratch_dir(rid: str) -> Path:
    """The child's temp dir. It takes the request id, because on Windows the pid of
    a venv's launcher is not the pid of the interpreter."""
    return Path(tempfile.gettempdir(), f"data-engine-{rid}")


def _fault(verb: str) -> None:
    """A test hook. ``DATA_ENGINE_FAULT`` makes the child fail in one named way.

    The broker sets the environment of the container, and the model cannot
    set it. WS43-F26 uses it to prove each kill path of the parent.
    """
    fault = os.environ.get("DATA_ENGINE_FAULT", "")
    if fault == "exit":
        os._exit(3)
    if fault == "kill":
        os.kill(os.getpid(), signal.SIGKILL)  # type: ignore[attr-defined, unused-ignore]
    if fault == "sleep":
        time.sleep(3600)
    if fault == "grow":
        hold = []
        while True:
            hold.append(bytearray(32 * 2**20))
            time.sleep(0.05)


def _child(argv: list[str]) -> int:
    """The child of ``_isolated``. It writes its answer to ``.<rid>.child``."""
    verb, rid, run, data, outputs = argv
    dirs = Dirs(Path(run), Path(data), Path(outputs))
    _limit_arenas()
    with contextlib.suppress(OSError):
        # The kernel picks this process, and not the parent, at an out-of-memory kill.
        Path("/proc/self/oom_score_adj").write_text("1000", encoding="ascii")
    duckdb.connect  # noqa: B018 — import it before the limit
    if resource is not None:
        # A backstop for one huge allocation. DuckDB keeps the address space
        # that it freed, so a tight limit refuses a wide load (round 2).
        limit = _budget() * 4
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))  # type: ignore[attr-defined, unused-ignore]
    # openpyxl and DuckDB's spill write here, and the parent removes it.
    scratch = _scratch_dir(rid)
    scratch.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(scratch)
    try:
        req = json.loads((dirs.run / "requests" / f"{rid}.json").read_text(encoding="utf-8"))
        _fault(verb)
        answer = _serve(dirs, verb, req, rid)
        body = json.dumps(answer, default=str)
        (dirs.run / "answers" / f".{rid}.child").write_text(body, encoding="utf-8")
    finally:
        tempfile.tempdir = None
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


def _write_answer(run_dir: Path, request_id: str, answer: dict[str, Any]) -> None:
    body = json.dumps(answer, ensure_ascii=False, default=str)
    while len(body.encode("utf-8")) > MAX_ANSWER_BYTES and answer.get("rows"):
        answer["rows"] = answer["rows"][: len(answer["rows"]) // 2]
        answer.pop("cells", None)
        answer["rows_returned"], answer["truncated"] = len(answer["rows"]), True
        answer["truncated_reason"] = [*answer.get("truncated_reason", []), "characters"]
        body = json.dumps(answer, ensure_ascii=False, default=str)
    folder = run_dir / "answers"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f".{request_id}.tmp").write_text(body, encoding="utf-8")
    os.replace(folder / f".{request_id}.tmp", folder / f"{request_id}.json")


def main(argv: list[str], dirs: Dirs | None = None) -> int:
    """Run one verb. Return 0 when an answer was written, and 2 for bad usage."""
    if (
        argv[:1] == ["--child"]
        and len(argv) == 6
        and argv[1] in VERBS
        and _ID_RE.fullmatch(argv[2])
    ):
        return _child(argv[1:])
    dirs = dirs or Dirs()
    if len(argv) != 2 or argv[0] not in VERBS or not _ID_RE.fullmatch(argv[1]):
        print("usage: data_engine.py <verb> <request id>", file=sys.stderr)
        return 2
    verb, request_id = argv
    try:
        req = json.loads((dirs.run / "requests" / f"{request_id}.json").read_text(encoding="utf-8"))
        if not isinstance(req, dict):
            raise Refused("bad_request", "The request is not a JSON object.")
        answer = _isolated(dirs, verb, req, request_id, _timeout(req, verb))
    except Refused as exc:
        answer = {"ok": False, "verb": verb, "error": exc.code, "message": exc.message}
    except Exception as exc:  # The engine always writes an answer.
        message = f"{type(exc).__name__}: {exc}"[:500]
        answer = {"ok": False, "verb": verb, "error": "engine_error", "message": message}
    _write_answer(dirs.run, request_id, answer)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
