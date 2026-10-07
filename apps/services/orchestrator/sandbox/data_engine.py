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

This slice reads ``.csv`` and ``.tsv``. It refuses ``.xlsx`` and ``.xlsm``
until WS-43y1b, and ``.xls``, ``.xlsb`` and ``.ods`` always.

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
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

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
_LATER = "The engine reads {} files from WS-43y1b. Until then, ask for a CSV copy."
_NEVER = "The engine does not read {} files. Save the file as .xlsx or .csv."
_REFUSED_KINDS = {
    ".xlsx": _LATER.format(".xlsx"),
    ".xlsm": _LATER.format(".xlsm"),
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
                message = f"The file has more than {max_rows} rows, the cap of one dataset."
                raise Refused(
                    "too_many_rows", message + " Split the file, or filter it before loading."
                )
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


def _one_row(con: duckdb.DuckDBPyConnection, sql: str, deadline: float) -> tuple[Any, ...]:
    work = lambda: con.execute(sql).fetchone()  # noqa: E731
    return tuple(_timed(con, max(1, int(deadline - time.monotonic())), work, "load") or ())


def _decimal_comma(
    con: duckdb.DuckDBPyConnection, source: str, width: int, deadline: float
) -> bool:
    """A ";" file with 12,5 or 1.234,5 reads its numbers with a decimal comma."""
    for js in _batches(width):
        tests = [f"count_if(c{j} LIKE '%,%' AND NOT vw{j} AND vd{j})" for j in js]
        if sum(
            _one_row(
                con, f"SELECT {', '.join(tests)} FROM {_parts(source, js, ('w', 'd'))}", deadline
            )
        ):
            return True
    return False


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
    con: duckdb.DuckDBPyConnection, source: str, counts: list[tuple[Any, ...]], deadline: float
) -> dict[int, list[str]]:
    """The date formats that read every value of each column, in the order of ``_FORMATS``.

    Only a column that is no boolean and no number, and whose every value has
    the shape of a date, tries the formats. The first 2000 rows pick the
    formats, and only those formats then read every row.
    """
    dated = [j for j, s in enumerate(counts, 1) if s[0] and max(s[1], s[2]) < s[0] == s[12]]
    out: dict[int, list[str]] = {}
    for i in range(0, len(dated), _BATCH):
        out |= _date_batch(con, source, counts, dated[i : i + _BATCH], deadline)
    return out


def _date_batch(
    con: duckdb.DuckDBPyConnection,
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
    found = _one_row(con, f"{head} FROM (SELECT {cols} FROM {source} LIMIT 2000)", deadline)
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
    found = _one_row(con, f"SELECT {', '.join(tries)} FROM {source}", deadline) if tries else ()
    out: dict[int, list[str]] = {}
    at = 0
    for j in dated:
        hits = found[at : at + len(picked[j])]
        at += len(picked[j])
        out[j] = [f for f, hit in zip(picked[j], hits, strict=True) if hit == counts[j - 1][0]]
    return out


def _types(
    con: duckdb.DuckDBPyConnection,
    source: str,
    seen: dict[str, Any],
    semicolon: bool,
    deadline: float,
) -> tuple[bool, list[dict[str, Any]], list[str]]:
    """Give each column one type. Return the decimal comma, the manifest
    entries and the SQL that casts each column."""
    width = len(seen["names"])
    dc = semicolon and _decimal_comma(con, source, width, deadline)
    mode = "d" if dc else "w"
    found: tuple[Any, ...] = ()
    for js in _batches(width):
        stats = [s for j in js for s in _column_stats(j, mode)]
        found += _one_row(
            con, f"SELECT {', '.join(stats)} FROM {_parts(source, js, (mode,))}", deadline
        )
    counts = [found[i : i + 13] for i in range(0, len(found), 13)]
    fits = _date_formats(con, source, counts, deadline)
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
    con = duckdb.connect(":memory:", config={"threads": "1"})
    con.execute(f"SET memory_limit = '{_duckdb_memory_mb()}MB'")
    con.execute("SET threads = 1")
    con.execute(f"SET temp_directory = {_lit((stage / 'spill').as_posix())}")
    table = re.sub(r"[^a-z0-9]+", "_", Path(label).stem.lower()).strip("_")[:60] or "data"
    table = "t_" + table if table[0].isdigit() else table
    keyword = "SELECT count(*) FROM duckdb_keywords() WHERE keyword_name = ?"
    keyword += " AND keyword_category <> 'unreserved'"
    table += "_data" if con.execute(keyword, [table]).fetchone() != (0,) else ""
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
    _one_row(con, f"COPY (SELECT * FROM {source}) TO {_lit(staged)} {options}", deadline)
    raw.unlink()
    source = f"read_parquet({_lit(staged)})"
    dc, cols, casts = _types(con, source, seen, delim == ";", deadline)
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
        _one_row(con, f"{copy} TO {_lit(part)} {options}", deadline)
        parts.append(part)
    if len(parts) == 1:
        os.replace(parts[0], parquet)
    else:
        joined = " POSITIONAL JOIN ".join(f"read_parquet({_lit(p)})" for p in parts)
        _one_row(con, f"COPY (SELECT * FROM {joined}) TO {_lit(parquet)} {options}", deadline)
        for part in parts:
            Path(part).unlink()
    Path(staged).unlink()
    found: tuple[Any, ...] = ()
    for js in _batches(width):
        bounds = ", ".join(
            f"min({_q(cols[j - 1]['name'])}), max({_q(cols[j - 1]['name'])})" for j in js
        )
        found += _one_row(con, f"SELECT {bounds} FROM read_parquet({_lit(parquet)})", deadline)
    for j, col in enumerate(cols):
        if found[2 * j] is not None:
            col["min"], col["max"] = _cell(found[2 * j])[0], _cell(found[2 * j + 1])[0]
    con.close()
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
    if ext not in (".csv", ".tsv"):
        raise Refused("unsupported_kind", f"The engine reads .csv and .tsv, not {ext or 'this'}.")
    max_bytes, header_rows = _limit(req, "max_file_bytes"), _limit(req, "header_rows")
    if path.stat().st_size > max_bytes:
        raise Refused("too_large", f"The file is larger than {max_bytes // 2**20} MB.")
    deadline = time.monotonic() + _timeout(req, "load")
    # The id is a SHA-256 of the bytes and the load options. A second load reads nothing.
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(2**20):
            digest.update(chunk)
    digest.update(b"\0" + json.dumps({"header_rows": header_rows}, sort_keys=True).encode())
    ds_id = "ds_" + digest.hexdigest()[:32]
    if (dirs.data / ds_id / "manifest.json").is_file():
        return {"reused": True, **_summary(_manifest(dirs, ds_id))}
    dirs.data.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".stage-{rid}-", dir=dirs.data))
    try:
        text, raw = stage / "source.csv", stage / "rows.csv"
        encoding = _transcode(path, text, deadline)
        with open(text, encoding="utf-8", newline="") as fh:
            delim = _delimiter(fh.read(2**16), ext)
        budget = PAD_FACTOR * max_bytes
        seen = _prepare(text, raw, delim, header_rows, _limit(req, "max_rows"), budget, deadline)
        text.unlink()
        manifest = _write_dataset(stage, raw, ds_id, label, delim, seen, deadline)
        manifest.update(encoding=encoding, kind=ext[1:], header_rows=header_rows)
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


def _remove(path: Path) -> None:
    if path.exists():
        path.chmod(stat.S_IRWXU)
        for child in path.iterdir():
            child.chmod(stat.S_IRUSR | stat.S_IWUSR)
        shutil.rmtree(path, ignore_errors=True)


def _summary(manifest: dict[str, Any]) -> dict[str, Any]:
    tables = [
        {key: t[key] for key in ("name", "rows", "range", "left_out")}
        | {"columns": [{"name": _short(c["name"]), "type": c["type"]} for c in t["columns"]]}
        for t in manifest["tables"]
    ]
    return {
        "dataset_id": manifest["dataset_id"],
        "findings": manifest["findings"],
        "tables": tables,
    }


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
        # A row that carries its source row gets its cell reference.
        last, at = _letter(len(tables[only]["columns"]) - 1), names.index("_src_row")
        answer["cells"] = [f"{tables[only]['sheet']}!A{r[at]}:{last}{r[at]}" for r in rows[:cap]]
    return answer


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
