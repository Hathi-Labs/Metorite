"""The Trafilatura child process of :mod:`acb_skills.web_tools` (WS-WEB WEB-2).

The parent starts this file as a SCRIPT, never with ``-m``::

    python -I <path>/web_reader_worker.py

``-m acb_skills...`` runs the package ``__init__``, which imports the whole
tool set of the agents and costs about 1.2 s more on each read. ``-I`` ignores
``PYTHONPATH`` and the user site, and it puts no script dir on ``sys.path``.
This file imports nothing from ``acb_skills``. It loads the one confinement
module of the platform, ``child_confine.py`` in the same dir, by its path.

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
  ``memory`` at the memory cap, ``unavailable`` when the confinement cannot
  apply and ``failed`` for each other fault. The parent
  owns each word that a person reads, so the child sends a code only.

**The limits.** The parent kills the child at its timeout. On Linux the child
also caps its address space and its CPU time, writes no core file and takes a
low priority. The CPU cap is the second net: it ends a child that outlives its
parent. Windows is the dev box only, and it has no ``resource`` module.

**No secret and no fetch.** The parent gives the allowlisted env of
``acb_common.child_env.child_env``, so no API key and no database URL reach a
process that parses a hostile page. ``extract`` gets text and no URL, so it
fetches nothing.

**The confinement** (WEB-2b). The child runs as the gateway's user, so a bug in
a parser could read ``.env`` or open a socket. The order of :func:`main` is the
control:

1. ``close_inherited_fds``: shut each descriptor from 3 up. ``close_fds`` is
   not in force when the parent runs on uvloop, so an inheritable descriptor of
   the parent reaches the child.
2. ``_limit_self``: the memory cap, the CPU cap and a low priority.
3. ``confine``, while the child has ONE thread: Landlock lets it READ only the
   Python and library dirs and write nowhere. With ABI 4 or later it refuses
   each TCP bind and connect. The seccomp filter refuses each new socket and
   each signal to another process.
4. ``_preload``: one small page runs through ``extract``, so each import
   happens inside the confinement.
5. ``close_limits``: no fork, no new thread and no file growth.
6. Only then does the child read the page from stdin.

Landlock ABI 1 (the file system) is the floor here, in :data:`LANDLOCK_ABI`.
The network needs no Landlock rule: seccomp refuses ``socket`` itself, and step
1 leaves no socket to connect. A kernel with no Landlock, or a machine that is
not x86-64, gives ``{"fault": "unavailable"}`` and no parse. The parent then
reads with its regex strip.

Fence: ``tests/unit/test_web_readers.py``.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

#: The address space of the child on Linux. Pages of 192 KiB of many tags, of
#: deep nesting and of plain words peaked at 171 MB, and at 66 MB resident
#: (measured on Linux on 2026-11-02, with Trafilatura 2.3.1).
MEMORY_BYTES = 512 * 1024 * 1024
#: The CPU seconds of the child on Linux. One thread, so CPU time is about
#: wall time. The parent's kill at ``READER_TIMEOUT_S`` comes first.
CPU_SECONDS = 10

#: The Landlock ABI that the child asks of the kernel. 1 is the file system.
#: The kernel's own ABI decides the rest: from 4 on, TCP is refused too.
LANDLOCK_ABI = 1

#: One small page for :func:`_preload`.
_WARM_HTML = ("<html><head><title>Warm</title></head><body><article><p>"
              "The reader runs one small page before it reads the real one.</p>"
              "</article></body></html>")

#: Trafilatura's extensive search takes a year from prose ("founded in 2009")
#: or from a footer ("(c) 2019") and writes it as ``2009-01-01``. So it is off.
_DATE_PARAMS = {"extensive_search": False, "original_date": True}


def _load_confine() -> Any:
    """Load ``child_confine.py`` from this dir, by path.

    ``-I`` puts no dir on ``sys.path``, and ``import acb_skills`` would run the
    package ``__init__``. The module writes no bytecode into the package dir."""
    path = Path(__file__).resolve().with_name("child_confine.py")
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec = importlib.util.spec_from_file_location("child_confine", path)
        if spec is None or spec.loader is None:
            raise ImportError("child_confine.py does not load")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = keep
    return module


_cc = _load_confine()


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


def _preload() -> None:
    """Run one small page through ``extract``, inside the confinement.

    Each import of Trafilatura and of lxml happens here, so the read of the
    real page opens no file. An import that fails raises ``ImportError``."""
    _extract_markdown(_WARM_HTML)


def main() -> int:
    try:
        _cc.close_inherited_fds()
        _limit_self()
        # One thread here, so the confinement binds each thread.
        _cc.confine(LANDLOCK_ABI)
        _preload()
        _cc.close_limits()
        # The untrusted page arrives only now, after the confinement.
        item = _answer(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
    except _cc.Refused:  # no confinement means no parse
        item = {"fault": "unavailable"}
    except ImportError:
        item = {"fault": "package"}
    except MemoryError:
        item = {"fault": "memory"}
    except Exception:  # a limit or a read that fails is a fault of the child
        item = {"fault": "failed"}
    # json.dumps writes ASCII, so the code page of the console cannot change it.
    sys.stdout.write(json.dumps(item) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
