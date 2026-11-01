"""The Trafilatura child process of :mod:`acb_skills.web_tools` (WS-WEB WEB-2).

The parent starts this file as a SCRIPT, never with ``-m``::

    python -I <path>/web_reader_worker.py

``-m acb_skills...`` runs the package ``__init__``, which imports the whole
tool set of the agents and costs about 1.2 s more on each read. ``-I`` ignores
``PYTHONPATH`` and the user site, and it puts no script dir on ``sys.path``.
This file imports nothing from ``acb_skills``.

**Why a child.** One Python regex holds the GIL for its whole pass, so a
thread cannot bound Trafilatura. One public page with a long
``<meta name="author">`` value stopped the event loop of the gateway for 21 s,
and a timeout on that loop cannot fire while the loop is stopped. The parent
can kill a child at any moment, so the parent stays free. WEB-3 and each later
parser run the same way.

**The contract.** The HTML comes on stdin as UTF-8 text, never as bytes that
Trafilatura could inflate. The child writes one JSON object on one line of
stdout and exits 0:

* ``{"text": "...", "title": "...", "date": "..."}``. ``text`` is the main
  text as Markdown. An empty ``text`` means that Trafilatura found none.
* ``{"fault": "<code>"}``: ``package`` when Trafilatura does not import,
  ``memory`` at the memory cap and ``failed`` for each other fault. The parent
  owns each word that a person reads, so the child sends a code only.

**The limits.** The parent kills the child at its timeout. On Linux the child
also caps its address space and its CPU time, writes no core file and takes a
low priority. The CPU cap is the second net: it ends a child that outlives its
parent. Windows is the dev box only, and it has no ``resource`` module.

**No secret and no fetch.** The parent gives the allowlisted env of
``acb_common.child_env.child_env``, so no API key and no database URL reach a
process that parses a hostile page. ``extract`` gets text and no URL, so it
fetches nothing.

Fence: ``tests/unit/test_web_readers.py``.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys

#: The address space of the child on Linux. Pages of 192 KiB of many tags, of
#: deep nesting and of plain words peaked at 171 MB, and at 66 MB resident
#: (measured on Linux on 2026-11-02, with Trafilatura 2.3.1).
MEMORY_BYTES = 512 * 1024 * 1024
#: The CPU seconds of the child on Linux. One thread, so CPU time is about
#: wall time. The parent's kill at ``READER_TIMEOUT_S`` comes first.
CPU_SECONDS = 10

#: Trafilatura's extensive search takes a year from prose ("founded in 2009")
#: or from a footer ("(c) 2019") and writes it as ``2009-01-01``. So it is off.
_DATE_PARAMS = {"extensive_search": False, "original_date": True}


def _limit_self() -> None:
    """The memory cap, the CPU cap, no core file and a low priority, on Linux.

    It runs before the import of Trafilatura and before one byte of the page.
    Windows has no ``resource`` module, and the child runs with no cap there.
    """
    sys.dont_write_bytecode = True
    try:
        import resource
    except ImportError:
        return
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with contextlib.suppress(OSError):
        os.nice(10)


def _scalar(value: str) -> str:
    """One value of Trafilatura's front matter. It writes a plain value, or a
    JSON string when the value holds a special character."""
    if value.startswith('"'):
        try:
            loaded = json.loads(value)
        except ValueError:
            return value
        return loaded if isinstance(loaded, str) else value
    return value


def _front_matter(markdown: str) -> tuple[dict[str, str], str]:
    """``(fields, text)``: the front matter of Trafilatura's Markdown, and
    the text after it.

    Trafilatura writes ``---``, one ``name: value`` line for each field, and
    ``---``. A value with a line break or a special character is a JSON
    string, so no field line is ``---``. A title that holds ``---`` cannot
    end the front matter. With no front matter, ``fields`` is empty."""
    lines = markdown.split("\n")
    if lines[0] != "---" or "---" not in lines[1:]:
        return {}, markdown
    end = lines.index("---", 1)
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        name, sep, value = line.partition(": ")
        if sep:
            fields[name.strip()] = _scalar(value.strip())
    return fields, "\n".join(lines[end + 1:])


def _stated(date: str, html: str) -> bool:
    """True when a ``meta`` tag, a ``time`` tag or a JSON-LD date field of the
    page names *date*.

    With the extensive search off, Trafilatura still takes a bare
    ``2021-03-12`` from the prose, and a date from a heading or from a URL. A
    date that the page only mentions is not the date of the page, so it never
    reaches the header. A date in another format gives no line. That loss is
    safe, and a guess shown as a fact is not."""
    day = re.escape(date)
    pattern = rf'<(?:meta|time)\b[^<>]*{day}|"date\w*"\s*:\s*"{day}'
    return re.search(pattern, html, re.IGNORECASE) is not None


def _extract_markdown(html: str) -> dict[str, str] | None:
    """The fields of the answer for ``html``, or None when no text was found.

    ``extract`` gets the text and no URL, so it fetches nothing. ``html`` is
    text, not bytes: Trafilatura inflates bytes that look compressed, and that
    would pass the bound of the decode."""
    from trafilatura import extract

    markdown = extract(html, output_format="markdown", with_metadata=True,
                       include_comments=False, favor_precision=True,
                       date_extraction_params=_DATE_PARAMS)
    if not markdown:
        return None
    fields, text = _front_matter(markdown)
    text = text.strip()
    if not text:
        return None
    date = fields.get("date", "")
    return {"text": text, "title": fields.get("title", ""),
            "date": date if date and _stated(date, html) else ""}


def _answer(html: str) -> dict[str, str]:
    """The one object that the child writes. It never raises for a fault."""
    try:
        return _extract_markdown(html) or {"text": ""}
    except ImportError:
        return {"fault": "package"}
    except MemoryError:
        return {"fault": "memory"}
    except Exception:  # no traceback reaches the parent, only a code
        return {"fault": "failed"}


def main() -> int:
    try:
        _limit_self()
        item = _answer(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
    except Exception:  # a limit or a read that fails is a fault of the child
        item = {"fault": "failed"}
    # json.dumps writes ASCII, so the code page of the console cannot change it.
    sys.stdout.write(json.dumps(item) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
