"""Card images for the WhatsApp channel: tiles, board, timeline, agenda, gantt,
donut (WS-47 WAC-10c).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §13.

``whatsapp_render`` draws the charts and the table. This module draws the
cards that the web app shows as a view: KPI tiles, a board of columns, a
timeline, a calendar day, a project schedule and a breakdown. Each one is an
SVG that ``whatsapp_render.to_png`` turns into a PNG.

**One colour vocabulary.** A status maps to one of six hues as
``workbench/control_plane/src/lib/statusAccent.ts`` maps it: a stored colour
name first, then the category, then a keyword in the name (:func:`hue`). So a
"Blocked" column is amber here and on the web. Only where the web gives no
hue do the WhatsApp extensions apply: health and priority words such as
"at risk" or "overdue".

Fence: ``tests/unit/test_wac_cards.py``.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta

from acb_skills.whatsapp_render import (
    BORDER,
    CATEGORICAL,
    FOREGROUND,
    MUTED,
    PAD,
    STRIPE,
    TEXT_MAX,
    WIDTH,
    RenderError,
    _footer,
    _header,
    _hsl,
    _number,
    _svg,
    _t,
    _text_width,
    _fit,
    fmt,
)

# ── The hues of statusAccent.ts (light values of globals.css) ───────────────

HUES: dict[str, str] = {
    "gray": MUTED,
    "red": _hsl(0, 72, 45),        # --destructive
    "amber": _hsl(36, 92, 29),     # --warning
    "green": _hsl(142, 72, 28),    # --success
    "blue": _hsl(198, 89, 32),     # --info
    "violet": _hsl(268, 70, 50),   # --violet
}
_ALIASES = {"grey": "gray", "orange": "amber", "yellow": "amber", "purple": "violet"}
#: ``CATEGORY_HUES`` of statusAccent.ts.
_CATEGORY = {"backlog": "gray", "todo": "gray", "in_progress": "blue", "done": "green",
             "cancelled": "red", "triage": "violet"}
#: ``keywordHue`` of statusAccent.ts, in its order. Leaving "cancel" out is
#: the web's own choice (statusAccent.ts), so it is left out here too.
_WEB_KEYWORDS = (
    (re.compile(r"done|complete|closed|finished|shipped"), "green"),
    (re.compile(r"wait|block|hold|paused|stuck"), "amber"),
    (re.compile(r"progress|doing|active|working|review|in[\s-]?process"), "blue"),
    (re.compile(r"todo|to[\s-]?do|backlog|new|open|inbox"), "gray"),
)
#: WhatsApp extensions, used ONLY where the web gives no hue. Report health
#: (RAG), due-date and priority words that a card's ``tone`` may carry.
_EXTRA = {"on_track": "green", "at_risk": "amber", "off_track": "red", "late": "red",
          "overdue": "red", "good": "green", "bad": "red", "warn": "amber",
          "neutral": "gray", "info": "blue", "critical": "red", "urgent": "red",
          "important": "amber", "approved": "green", "pending": "amber",
          "rejected": "red", "failed": "red", "stopped": "red", "queued": "gray"}
_EXTRA_WORDS = re.compile(r"overdue|late|reject|fail")


def hue(value: object, *, default: str = "gray") -> str:
    """The hue name for a colour, a status category, or a status word.

    The order of ``statusAccent.resolveHue``: a colour name, the category,
    then the web's keyword rules. Only when none answers do the extensions
    (:data:`_EXTRA`) apply, so a word the web colours is never coloured
    differently here.
    """
    if not isinstance(value, str) or not value.strip():
        return default
    v = value.strip().lower()
    v = _ALIASES.get(v, v)
    if v in HUES:
        return v
    key = re.sub(r"[\s-]+", "_", v)
    if key in _CATEGORY:
        return _CATEGORY[key]
    for rule, name in _WEB_KEYWORDS:
        if rule.search(v):
            return name
    if key in _EXTRA:
        return _EXTRA[key]
    if _EXTRA_WORDS.search(v):
        return "red"
    return default


def colour(value: object, *, default: str = "gray") -> str:
    return HUES[hue(value, default=default)]


# ── Small helpers ──────────────────────────────────────────────────────────


def _s(value: object, field: str, *, required: bool = True) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise RenderError(f'each {field} needs text')
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return fmt(_number(value, field))
    return " ".join(str(value).split())[:TEXT_MAX]


def _items(value: object, field: str, most: int) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise RenderError(f'"{field}" must be a non-empty list')
    if len(value) > most:
        raise RenderError(f'"{field}" holds at most {most} items. Send the first '
                          f"{most} and say how many more there are")
    if not all(isinstance(v, dict) for v in value):
        raise RenderError(f'each item of "{field}" must be an object')
    return value


def _wrap(text: str, size: float, width: float, lines: int, *, bold: bool = False) -> list[str]:
    """Greedy word wrap into at most *lines* lines. The last line is cut with
    an ellipsis when the text does not fit."""
    words = " ".join(str(text).split())[:TEXT_MAX].split(" ")
    out: list[str] = []
    cur = ""
    for i, w in enumerate(words):
        trial = f"{cur} {w}".strip()
        if _text_width(trial, size, bold=bold) <= width:
            cur = trial
            continue
        if cur:
            out.append(cur)
        cur = w
        if len(out) == lines - 1:
            rest = " ".join([cur, *words[i + 1:]])
            out.append(_fit(rest, size, width, bold=bold))
            return out
    if cur:
        out.append(_fit(cur, size, width, bold=bold))
    return out[:lines]


def _rect(x: float, y: float, w: float, h: float, *, fill: str, rx: float = 8,
          stroke: str | None = None, opacity: float | None = None) -> str:
    extra = f' stroke="{stroke}" stroke-width="1"' if stroke else ""
    if opacity is not None:
        extra += f' fill-opacity="{opacity}"'
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}" height="{max(h, 0):.1f}" '
            f'rx="{rx}" fill="{fill}"{extra}/>')


def _date(value: object, field: str) -> date:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip()[:19]).date()
        except ValueError:
            pass
    raise RenderError(f'{field} must be a date like "2026-10-14"')


def _minutes(value: object, field: str) -> int:
    if isinstance(value, str):
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value)
        if m and int(m.group(1)) <= 24 and int(m.group(2)) < 60:
            return int(m.group(1)) * 60 + int(m.group(2))
    raise RenderError(f'{field} must be a time like "09:30"')


def _short_date(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


# ── KPI tiles ───────────────────────────────────────────────────────────────

MAX_TILES = 8


def stats_svg(title: str, tiles: list[dict], *, subtitle: str | None = None) -> str:
    """Two tiles a row. A tile: label, value, and a delta or a hint.

    ``delta`` is text such as "+12%" or "-3". Its sign picks the colour, and
    ``good`` ("up" or "down") says which way is good. ``tone`` colours the
    value instead.
    """
    tiles = _items(tiles, "tiles", MAX_TILES)
    parts, y = _header(title, subtitle)
    gap = 14
    w = (WIDTH - 2 * PAD - gap) / 2
    h = 104
    for i, tile in enumerate(tiles):
        x = PAD + (i % 2) * (w + gap)
        ty = y + (i // 2) * (h + gap)
        tone = tile.get("tone")
        parts.append(_rect(x, ty, w, h, fill=STRIPE, rx=12, stroke=BORDER))
        if tone:
            parts.append(_rect(x, ty + 14, 4, h - 28, fill=colour(tone), rx=2))
        parts.append(_t(x + 18, ty + 28, _fit(_s(tile.get("label"), "tile label"), 14, w - 36),
                        size=14, fill=MUTED))
        value = _s(tile.get("value"), "tile value")
        parts.append(_t(x + 18, ty + 66, _fit(value, 30, w - 36, bold=True), size=30,
                        bold=True, fill=colour(tone) if tone else FOREGROUND))
        delta = _s(tile.get("delta"), "delta", required=False)
        hint = _s(tile.get("hint"), "hint", required=False)
        if delta:
            up = not delta.lstrip().startswith(("-", "−", "▼"))
            good = str(tile.get("good") or "up").lower() != "down"
            tone_d = "green" if up == good else "red"
            arrow = "▲" if up else "▼"
            parts.append(_t(x + 18, ty + 90, _fit(f"{arrow} {delta.lstrip('+-−▲▼ ')}"
                                                  + (f"  {hint}" if hint else ""), 13, w - 36),
                            size=13, fill=HUES[tone_d], bold=True))
        elif hint:
            parts.append(_t(x + 18, ty + 90, _fit(hint, 13, w - 36), size=13, fill=MUTED))
    rows = math.ceil(len(tiles) / 2)
    height = y + rows * (h + gap) + 30
    return _svg([*parts, _footer(height)], height)


# ── Board ───────────────────────────────────────────────────────────────────

MAX_BOARD_COLUMNS = 4
MAX_CARDS = 6


def board_svg(title: str, columns: list[dict], *, subtitle: str | None = None) -> str:
    """Columns of cards, like the Projects board. A column: name, cards, and
    an optional ``total`` when there are more cards than shown. A card:
    title, meta (one muted line) and tone (a status or a priority)."""
    columns = _items(columns, "columns", MAX_BOARD_COLUMNS)
    parts, y = _header(title, subtitle)
    gap = 10
    n = len(columns)
    cw = (WIDTH - 2 * PAD - gap * (n - 1)) / n
    title_size = 14 if n <= 2 else 13
    card_h = 74
    bottom = y
    for c, col in enumerate(columns):
        x = PAD + c * (cw + gap)
        name = _s(col.get("name"), "column name")
        cards = col.get("cards") or []
        if not isinstance(cards, list) or not all(isinstance(k, dict) for k in cards):
            raise RenderError('each column needs "cards", a list of objects')
        total = col.get("total")
        count = int(_number(total, "total")) if total is not None else len(cards)
        shown = cards[:MAX_CARDS]
        tone = col.get("tone") or name
        head_h = 36
        parts.append(_rect(x, y, cw, head_h, fill=STRIPE, rx=10))
        parts.append(f'<circle cx="{x + 14:.1f}" cy="{y + 18:.1f}" r="5" fill="{colour(tone)}"/>')
        parts.append(_t(x + 26, y + 23, _fit(name, 14, cw - 70, bold=True), size=14, bold=True))
        parts.append(_t(x + cw - 10, y + 23, str(count), size=13, fill=MUTED, anchor="end"))
        cy = y + head_h + 8
        for card in shown:
            ct = _s(card.get("title"), "card title")
            meta = _s(card.get("meta"), "meta", required=False)
            ctone = card.get("tone")
            parts.append(_rect(x, cy, cw, card_h, fill="#ffffff", rx=8, stroke=BORDER))
            parts.append(_rect(x, cy + 8, 4, card_h - 16, fill=colour(ctone or tone), rx=2))
            lines = _wrap(ct, title_size, cw - 24, 2, bold=True)
            for li, line in enumerate(lines):
                parts.append(_t(x + 14, cy + 22 + li * 17, line, size=title_size, bold=True))
            if meta:
                parts.append(_t(x + 14, cy + card_h - 12, _fit(meta, 12, cw - 24), size=12,
                                fill=MUTED))
            cy += card_h + 8
        if count > len(shown):
            parts.append(_t(x + cw / 2, cy + 14, f"+{count - len(shown)} more", size=13,
                            fill=MUTED, anchor="middle"))
            cy += 24
        if not shown and count == 0:
            parts.append(_t(x + cw / 2, cy + 18, "Empty", size=13, fill=MUTED, anchor="middle"))
            cy += 30
        bottom = max(bottom, cy)
    height = bottom + 36
    return _svg([*parts, _footer(height)], height)


# ── Timeline ────────────────────────────────────────────────────────────────

MAX_EVENTS = 12
_STATE_TONE = {"done": "green", "current": "blue", "upcoming": "gray", "late": "red",
               "blocked": "amber"}


def timeline_svg(title: str, events: list[dict], *, subtitle: str | None = None) -> str:
    """Dated events on a vertical line: milestones, a task's history, a
    thread. An event: date (text, as it should read), title, detail, and
    state ("done", "current", "upcoming", "late", "blocked")."""
    events = _items(events, "events", MAX_EVENTS)
    parts, y = _header(title, subtitle)
    line_x = PAD + 112
    text_x = line_x + 22
    text_w = WIDTH - PAD - text_x
    rows: list[tuple[float, dict, list[str], str]] = []
    cy = y + 6
    for ev in events:
        ttl = _s(ev.get("title"), "event title")
        detail = _s(ev.get("detail"), "detail", required=False)
        dlines = _wrap(detail, 13, text_w, 2) if detail else []
        rows.append((cy, ev, dlines, ttl))
        cy += 30 + 17 * len(dlines) + 14
    top, last = rows[0][0], rows[-1][0]
    parts.append(f'<line x1="{line_x}" y1="{top + 6:.1f}" x2="{line_x}" y2="{last + 6:.1f}" '
                 f'stroke="{BORDER}" stroke-width="3"/>')
    for ry, ev, dlines, ttl in rows:
        state = str(ev.get("state") or "upcoming").lower()
        tone = _STATE_TONE.get(state) or hue(ev.get("tone") or state)
        col = HUES[tone]
        parts.append(_t(line_x - 16, ry + 11, _fit(_s(ev.get("date"), "date", required=False),
                                                   13, 92), size=13, fill=MUTED, anchor="end"))
        if state == "upcoming":
            parts.append(f'<circle cx="{line_x}" cy="{ry + 6:.1f}" r="7" fill="#ffffff" '
                         f'stroke="{col}" stroke-width="3"/>')
        else:
            parts.append(f'<circle cx="{line_x}" cy="{ry + 6:.1f}" r="8" fill="{col}"/>')
            if state == "current":
                parts.append(f'<circle cx="{line_x}" cy="{ry + 6:.1f}" r="13" fill="none" '
                             f'stroke="{col}" stroke-opacity="0.35" stroke-width="3"/>')
        parts.append(_t(text_x, ry + 12, _fit(ttl, 15, text_w, bold=True), size=15, bold=True,
                        fill=FOREGROUND if state != "upcoming" else MUTED))
        for li, line in enumerate(dlines):
            parts.append(_t(text_x, ry + 31 + li * 17, line, size=13, fill=MUTED))
    height = cy + 30
    return _svg([*parts, _footer(height)], height)


# ── A calendar day ──────────────────────────────────────────────────────────

MAX_AGENDA_ITEMS = 16
_KIND_TONE = {"event": "blue", "meeting": "blue", "focus": "violet", "task": "green",
              "free": "gray", "busy": "gray", "travel": "amber"}


def agenda_svg(title: str, items: list[dict], *, subtitle: str | None = None,
               all_day: list[str] | None = None) -> str:
    """One day on a time axis. An item: start and end ("09:30"), title,
    detail, and kind ("event", "focus", "task", "free", "travel") or tone.
    ``all_day`` lists the all-day items and the tasks due with no time."""
    items = _items(items, "items", MAX_AGENDA_ITEMS)
    parsed = []
    for it in items:
        s = _minutes(it.get("start"), "start")
        e = _minutes(it.get("end"), "end") if it.get("end") else s + 30
        if e <= s:
            raise RenderError("each item must end after it starts")
        parsed.append((s, e, it))
    lo = min(s for s, _e, _i in parsed) // 60
    hi = math.ceil(max(e for _s, e, _i in parsed) / 60)
    lo, hi = max(lo, 0), min(max(hi, lo + 1), 24)
    if hi - lo > 15:
        raise RenderError("an agenda shows at most 15 hours. Split the day")
    parts, y = _header(title, subtitle)
    if all_day:
        if not isinstance(all_day, list):
            raise RenderError('"all_day" must be a list of text')
        x = PAD
        for label in all_day[:6]:
            text = _fit(_s(label, "all-day item"), 13, 260)
            w = _text_width(text, 13) + 22
            if x + w > WIDTH - PAD:
                y += 32
                x = PAD
            parts.append(_rect(x, y, w, 26, fill=STRIPE, rx=13, stroke=BORDER))
            parts.append(_t(x + 11, y + 18, text, size=13))
            x += w + 8
        y += 40
    hour_h = 52
    axis_x = PAD + 50
    area_w = WIDTH - PAD - axis_x
    for hr in range(lo, hi + 1):
        hy = y + (hr - lo) * hour_h
        parts.append(f'<line x1="{axis_x}" y1="{hy:.1f}" x2="{WIDTH - PAD}" y2="{hy:.1f}" '
                     f'stroke="{BORDER}" stroke-width="1"/>')
        parts.append(_t(axis_x - 8, hy + 5, f"{hr:02d}:00", size=12, fill=MUTED, anchor="end"))
    # Lanes: an item takes the first lane that is free at its start.
    lanes: list[int] = []
    placed = []
    for s, e, it in sorted(parsed, key=lambda p: (p[0], -p[1])):
        lane = next((i for i, end in enumerate(lanes) if end <= s), None)
        if lane is None:
            lanes.append(e)
            lane = len(lanes) - 1
        else:
            lanes[lane] = e
        placed.append((s, e, it, lane))
    n_lanes = min(len(lanes), 3)
    for s, e, it, lane in placed:
        lane = min(lane, n_lanes - 1)
        # The lanes in use while this item runs. An item that overlaps
        # nothing takes the full width.
        width_lanes = min(1 + max((ln for s2, e2, _i2, ln in placed
                                   if s2 < e and e2 > s), default=0), n_lanes)
        width_lanes = max(width_lanes, lane + 1)
        lane_w = area_w / width_lanes
        kind = str(it.get("kind") or "event").lower()
        tone = it.get("tone") or _KIND_TONE.get(kind, "blue")
        col = colour(tone)
        bx = axis_x + 6 + lane * lane_w
        by = y + (s / 60 - lo) * hour_h + 2
        bh = max((e - s) / 60 * hour_h - 4, 18)
        bw = lane_w - 10
        if kind == "free":
            parts.append(_rect(bx, by, bw, bh, fill=STRIPE, rx=8, stroke=BORDER))
        else:
            parts.append(_rect(bx, by, bw, bh, fill=col, rx=8, opacity=0.14))
            parts.append(_rect(bx, by, 4, bh, fill=col, rx=2))
        label = _s(it.get("title"), "item title")
        when = f"{s // 60:02d}:{s % 60:02d}–{e // 60:02d}:{e % 60:02d}"
        parts.append(_t(bx + 12, by + 16, _fit(label, 14, bw - 20, bold=True), size=14,
                        bold=True, fill=MUTED if kind == "free" else FOREGROUND))
        if bh >= 36:
            detail = _s(it.get("detail"), "detail", required=False)
            sub = f"{when} · {detail}" if detail else when
            parts.append(_t(bx + 12, by + 33, _fit(sub, 12, bw - 20), size=12, fill=MUTED))
    height = y + (hi - lo) * hour_h + 44
    return _svg([*parts, _footer(height)], height)


# ── A project schedule ──────────────────────────────────────────────────────

MAX_GANTT_ROWS = 15


def gantt_svg(title: str, rows: list[dict], *, subtitle: str | None = None,
              today: str | None = None) -> str:
    """Bars over dates. A row: label, start and end (ISO dates), status or
    tone, and progress (0 to 100). A row whose start is its end is a
    milestone. ``today`` draws the today line."""
    rows = _items(rows, "rows", MAX_GANTT_ROWS)
    spans = []
    for r in rows:
        s = _date(r.get("start"), "start")
        e = _date(r.get("end") or r.get("start"), "end")
        if e < s:
            raise RenderError("each row must end on or after its start")
        spans.append((s, e, r))
    d0 = min(s for s, _e, _r in spans)
    d1 = max(e for _s, e, _r in spans)
    if (d1 - d0).days > 730:
        raise RenderError("a schedule shows at most two years")
    # Room after the last day, so an end bar or a milestone is never clipped.
    d1 = max(d1, d0 + timedelta(days=1)) + timedelta(days=max(2, (d1 - d0).days // 30))
    parts, y = _header(title, subtitle)
    label_w = 170
    x0 = PAD + label_w + 10
    x1 = WIDTH - PAD
    span = (d1 - d0).days + 1

    def dx(d: date) -> float:
        return x0 + (x1 - x0) * (d - d0).days / span

    # Ticks: weekly, or monthly over a long range.
    step = 7 if span <= 120 else 30
    # Widen the step until two labels sit 56 px apart.
    while (x1 - x0) * step / span < 56:
        step *= 2
    axis_y = y + 6
    tick = d0
    while tick <= d1:
        tx = dx(tick)
        parts.append(_t(tx, axis_y + 10, _short_date(tick), size=11, fill=MUTED,
                        anchor="middle"))
        parts.append(f'<line x1="{tx:.1f}" y1="{axis_y + 16:.1f}" x2="{tx:.1f}" '
                     f'y2="{axis_y + 22:.1f}" stroke="{BORDER}" stroke-width="1"/>')
        tick += timedelta(days=step)
    row_h = 34
    top = axis_y + 22
    for i, (s, e, r) in enumerate(spans):
        ry = top + i * row_h
        if i % 2 == 0:
            parts.append(_rect(PAD, ry, WIDTH - 2 * PAD, row_h, fill=STRIPE, rx=4))
        parts.append(_t(PAD + 8, ry + 22, _fit(_s(r.get("label"), "row label"), 14,
                                               label_w - 12), size=14))
        col = colour(r.get("tone") or r.get("status"), default="blue")
        if s == e:
            cx, cy = dx(s) + (x1 - x0) / span / 2, ry + row_h / 2
            parts.append(f'<path d="M {cx:.1f} {cy - 9:.1f} L {cx + 9:.1f} {cy:.1f} L {cx:.1f} '
                         f'{cy + 9:.1f} L {cx - 9:.1f} {cy:.1f} Z" fill="{col}"/>')
            continue
        bx, bw = dx(s), max(dx(e + timedelta(days=1)) - dx(s), 6)
        parts.append(_rect(bx, ry + 8, bw, row_h - 16, fill=col, rx=5, opacity=0.25))
        prog = r.get("progress")
        pct = max(0.0, min(_number(prog, "progress"), 100.0)) if prog is not None else 100.0
        if pct > 0:
            parts.append(_rect(bx, ry + 8, max(bw * pct / 100, 6), row_h - 16, fill=col, rx=5))
    bottom = top + len(spans) * row_h
    if today:
        td = _date(today, "today")
        if d0 <= td <= d1:
            tx = dx(td)
            parts.append(f'<line x1="{tx:.1f}" y1="{axis_y + 14:.1f}" x2="{tx:.1f}" '
                         f'y2="{bottom:.1f}" stroke="{HUES["red"]}" stroke-width="2" '
                         f'stroke-dasharray="4 3"/>')
    height = bottom + 40
    return _svg([*parts, _footer(height)], height)


# ── A breakdown ─────────────────────────────────────────────────────────────

MAX_SLICES = 8


def donut_svg(title: str, labels: list[str], values: list[float], *,
              subtitle: str | None = None, tones: list[str] | None = None,
              center: str | None = None) -> str:
    """Parts of a whole: a ring, the total in the middle, and a legend. A
    label that is a status takes its status hue unless ``tones`` says."""
    if not labels or len(labels) != len(values):
        raise RenderError("labels and values must be two lists of the same length")
    if len(labels) > MAX_SLICES:
        raise RenderError(f"a donut holds at most {MAX_SLICES} parts. Group the rest as Other")
    nums = [_number(v, "values") for v in values]
    if any(n < 0 for n in nums) or sum(nums) <= 0:
        raise RenderError("a donut needs values of zero or more, with a total above zero")
    if tones is not None and len(tones) != len(labels):
        raise RenderError("tones must have one entry for each label")
    total = sum(nums)
    parts, y = _header(title, subtitle)
    r_out, r_in = 92, 58
    cx, cy = PAD + r_out + 4, y + r_out + 4
    used: set[str] = set()
    cols = []
    for i, lab in enumerate(labels):
        if tones is not None:
            cols.append(colour(tones[i]))
            continue
        h = hue(str(lab), default="")
        if h and h not in used:
            used.add(h)
            cols.append(HUES[h])
        else:
            cols.append(CATEGORICAL[i % len(CATEGORICAL)])
    angle = -math.pi / 2
    for n, col in zip(nums, cols, strict=True):
        if n <= 0:
            continue
        sweep = 2 * math.pi * n / total
        if sweep >= 2 * math.pi - 1e-6:
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{(r_out + r_in) / 2}" fill="none" '
                         f'stroke="{col}" stroke-width="{r_out - r_in}"/>')
            break
        a2 = angle + sweep
        large = 1 if sweep > math.pi else 0
        p = [(cx + r_out * math.cos(angle), cy + r_out * math.sin(angle)),
             (cx + r_out * math.cos(a2), cy + r_out * math.sin(a2)),
             (cx + r_in * math.cos(a2), cy + r_in * math.sin(a2)),
             (cx + r_in * math.cos(angle), cy + r_in * math.sin(angle))]
        parts.append(
            f'<path d="M {p[0][0]:.2f} {p[0][1]:.2f} A {r_out} {r_out} 0 {large} 1 '
            f'{p[1][0]:.2f} {p[1][1]:.2f} L {p[2][0]:.2f} {p[2][1]:.2f} A {r_in} {r_in} 0 '
            f'{large} 0 {p[3][0]:.2f} {p[3][1]:.2f} Z" fill="{col}" stroke="#ffffff" '
            f'stroke-width="2"/>')
        angle = a2
    parts.append(_t(cx, cy + 4, _fit(center or fmt(total), 26, r_in * 1.6, bold=True),
                    size=26, bold=True, anchor="middle"))
    parts.append(_t(cx, cy + 24, "total", size=12, fill=MUTED, anchor="middle"))
    lx = cx + r_out + 36
    lw = WIDTH - PAD - lx
    ly = cy - len(labels) * 15
    for lab, n, col in zip(labels, nums, cols, strict=True):
        parts.append(_rect(lx, ly - 11, 14, 14, fill=col, rx=3))
        parts.append(_t(lx + 22, ly, _fit(str(lab), 14, lw - 120), size=14))
        parts.append(_t(WIDTH - PAD - 52, ly, fmt(n), size=14, bold=True, anchor="end"))
        parts.append(_t(WIDTH - PAD, ly, f"{100 * n / total:.0f}%", size=14, fill=MUTED,
                        anchor="end"))
        ly += 30
    height = max(cy + r_out, ly) + 40
    return _svg([*parts, _footer(height)], height)
