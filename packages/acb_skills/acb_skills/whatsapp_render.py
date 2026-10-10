"""Chart and table images for the WhatsApp channel (WS-47 WAC-10a).

WhatsApp cannot draw a chart or a table. So the bot sends a picture of one.
Each function here builds an SVG and turns it into a PNG with ``pymupdf``,
which the gateway already holds. No new dependency.

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §12.

The colours are the LIGHT values of ``workbench/control_plane/src/app/
globals.css`` (``--primary``, ``--foreground``, ``--cat-1…8``). A phone shows a
chat image on its own background, so one light card reads in both modes.

Fence: ``tests/unit/test_wac_native_ui.py``.
"""

from __future__ import annotations

import colorsys
import math
from xml.sax.saxutils import escape

# ── The look (light values of globals.css) ──────────────────────────────────


def _hsl(h: float, s: float, l: float) -> str:  # noqa: E741 - the CSS name
    r, g, b = colorsys.hls_to_rgb(h / 360, l / 100, s / 100)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


BACKGROUND = "#ffffff"
FOREGROUND = _hsl(222.2, 84, 4.9)
MUTED = _hsl(215.4, 16.3, 46.9)
BORDER = _hsl(214.3, 31.8, 91.4)
STRIPE = "#f6f8fa"
PRIMARY = _hsl(198, 89, 35)
SUCCESS = _hsl(142, 72, 28)
WARNING = _hsl(36, 92, 29)
DANGER = _hsl(0, 72, 45)
#: ``--cat-1…8`` (light), for a series that needs more than one hue.
CATEGORICAL = (
    _hsl(215, 85, 47), _hsl(27, 85, 36), _hsl(142, 85, 26), _hsl(264, 85, 59),
    _hsl(358, 85, 54), _hsl(182, 85, 26), _hsl(324, 85, 50), _hsl(66, 85, 25),
)

#: The SVG width. The PNG is ``SCALE`` times wider, which stays sharp on a
#: phone and small on the wire.
WIDTH = 640
SCALE = 1.5
PAD = 28
FONT = "Helvetica, Arial, sans-serif"

#: What one image can hold. More would be unreadable on a phone.
MAX_BARS = 12
MAX_POINTS = 60
MAX_COLUMNS = 6
MAX_ROWS = 20


class RenderError(ValueError):
    """The data cannot make this image. The message is for the model."""


def _text_width(text: str, size: float, *, bold: bool = False) -> float:
    try:
        import pymupdf

        return float(pymupdf.get_text_length(text, fontname="hebo" if bold else "helv",
                                             fontsize=size))
    except Exception:  # an approximation is enough for layout
        return len(text) * size * 0.55


def _fit(text: str, size: float, width: float, *, bold: bool = False) -> str:
    """*text* cut with an ellipsis so it fits *width* at *size*."""
    text = " ".join(str(text).split())
    if _text_width(text, size, bold=bold) <= width:
        return text
    while text and _text_width(text + "…", size, bold=bold) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _t(x: float, y: float, text: str, *, size: float = 15, fill: str = FOREGROUND,
       bold: bool = False, anchor: str = "start") -> str:
    weight = ' font-weight="bold"' if bold else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" '
            f'font-size="{size}"{weight} fill="{fill}" text-anchor="{anchor}">'
            f"{escape(text)}</text>")


def _header(title: str, subtitle: str | None) -> tuple[list[str], float]:
    """The title block. Returns the parts and the y where the body starts."""
    parts = [_t(PAD, PAD + 22, _fit(title, 24, WIDTH - 2 * PAD, bold=True),
                size=24, bold=True)]
    y = PAD + 34
    if subtitle:
        parts.append(_t(PAD, y + 18, _fit(subtitle, 15, WIDTH - 2 * PAD),
                        size=15, fill=MUTED))
        y += 26
    return parts, y + 18


def _footer(height: float) -> str:
    return _t(WIDTH - PAD, height - 14, "Metorite", size=12, fill=MUTED,
              anchor="end")


def _svg(parts: list[str], height: float) -> str:
    h = math.ceil(height)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
            f'height="{h}" viewBox="0 0 {WIDTH} {h}">'
            f'<rect width="{WIDTH}" height="{h}" fill="{BACKGROUND}"/>'
            + "".join(parts) + "</svg>")


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RenderError(f"{field} must be numbers")
    if not math.isfinite(value):
        raise RenderError(f"{field} must be finite numbers")
    return float(value)


def fmt(value: float, unit: str = "") -> str:
    """A short number: 1234 → 1,234, 12.5 → 12.5, with the unit after it."""
    text = f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"
    if not unit:
        return text
    return f"{unit}{text}" if unit in {"₹", "$", "€", "£"} else f"{text} {unit}"


# ── The charts ──────────────────────────────────────────────────────────────


def bar_svg(title: str, labels: list[str], values: list[float], *,
            subtitle: str | None = None, unit: str = "") -> str:
    """Horizontal bars, which keep long labels readable on a phone."""
    if not labels or len(labels) != len(values):
        raise RenderError("labels and values must be two lists of the same length")
    if len(labels) > MAX_BARS:
        raise RenderError(f"a bar chart holds at most {MAX_BARS} bars")
    nums = [_number(v, "values") for v in values]
    top = max(max(nums), 0.0)
    low = min(min(nums), 0.0)
    span = (top - low) or 1.0

    parts, y = _header(title, subtitle)
    label_w = 170
    value_w = 90
    x0 = PAD + label_w + 12
    track = WIDTH - PAD - value_w - x0
    zero = x0 + track * (-low / span)
    row = 34
    for i, (label, v) in enumerate(zip(labels, nums)):
        cy = y + i * row
        parts.append(_t(PAD, cy + 20, _fit(str(label), 15, label_w), size=15))
        w = track * abs(v) / span
        x = zero if v >= 0 else zero - w
        parts.append(f'<rect x="{x:.1f}" y="{cy + 6:.1f}" width="{max(w, 2):.1f}" '
                     f'height="20" rx="4" fill="{PRIMARY if v >= 0 else DANGER}"/>')
        parts.append(_t(WIDTH - PAD, cy + 20, fmt(v, unit), size=15, bold=True,
                        anchor="end"))
    height = y + len(labels) * row + 40
    return _svg(parts + [_footer(height)], height)


def line_svg(title: str, labels: list[str], values: list[float], *,
             subtitle: str | None = None, unit: str = "") -> str:
    """A trend: a line with a soft fill, the first and last labels, and the
    last value called out."""
    if len(values) < 2 or len(labels) != len(values):
        raise RenderError("a line chart needs at least 2 values and one label each")
    if len(values) > MAX_POINTS:
        raise RenderError(f"a line chart holds at most {MAX_POINTS} points")
    nums = [_number(v, "values") for v in values]
    parts, y = _header(title, subtitle)
    left, right = PAD + 56, WIDTH - PAD - 8
    top, bottom = y + 10, y + 230
    lo, hi = min(nums), max(nums)
    if hi == lo:
        hi, lo = hi + 1, lo - 1
    pad = (hi - lo) * 0.1
    lo, hi = lo - pad, hi + pad

    def px(i: int) -> float:
        return left + (right - left) * i / (len(nums) - 1)

    def py(v: float) -> float:
        return bottom - (bottom - top) * (v - lo) / (hi - lo)

    for k in range(5):
        gv = lo + (hi - lo) * k / 4
        gy = py(gv)
        parts.append(f'<line x1="{left}" y1="{gy:.1f}" x2="{right}" y2="{gy:.1f}" '
                     f'stroke="{BORDER}" stroke-width="1"/>')
        parts.append(_t(left - 8, gy + 5, fmt(gv), size=12, fill=MUTED, anchor="end"))
    pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(nums))
    area = f"{left:.1f},{bottom:.1f} {pts} {right:.1f},{bottom:.1f}"
    parts.append(f'<polygon points="{area}" fill="{PRIMARY}" fill-opacity="0.12"/>')
    parts.append(f'<polyline points="{pts}" fill="none" stroke="{PRIMARY}" '
                 f'stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>')
    lx, ly = px(len(nums) - 1), py(nums[-1])
    parts.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="5" fill="{PRIMARY}"/>')
    parts.append(_t(min(lx, right), max(ly - 12, top + 12), fmt(nums[-1], unit),
                    size=15, bold=True, anchor="end"))
    parts.append(_t(left, bottom + 22, _fit(str(labels[0]), 13, 200), size=13,
                    fill=MUTED))
    parts.append(_t(right, bottom + 22, _fit(str(labels[-1]), 13, 200), size=13,
                    fill=MUTED, anchor="end"))
    height = bottom + 60
    return _svg(parts + [_footer(height)], height)


def progress_svg(title: str, labels: list[str], values: list[float], *,
                 subtitle: str | None = None, totals: list[float] | None = None) -> str:
    """One bar per item. Each value is a percent, or a count of its total."""
    if not labels or len(labels) != len(values):
        raise RenderError("labels and values must be two lists of the same length")
    if len(labels) > MAX_BARS:
        raise RenderError(f"a progress card holds at most {MAX_BARS} items")
    if totals is not None and len(totals) != len(values):
        raise RenderError("totals must have one number for each value")
    nums = [_number(v, "values") for v in values]
    tots = [_number(t, "totals") for t in totals] if totals is not None else None

    parts, y = _header(title, subtitle)
    row = 52
    track = WIDTH - 2 * PAD
    for i, label in enumerate(labels):
        cy = y + i * row
        if tots is not None:
            pct = 100 * nums[i] / tots[i] if tots[i] else 0.0
            right = f"{fmt(nums[i])} / {fmt(tots[i])}"
        else:
            pct = nums[i]
            right = f"{pct:.0f}%"
        pct = max(0.0, min(pct, 100.0))
        colour = SUCCESS if pct >= 100 else PRIMARY
        parts.append(_t(PAD, cy + 16, _fit(str(label), 15, track - 120), size=15))
        parts.append(_t(WIDTH - PAD, cy + 16, right, size=15, bold=True, anchor="end"))
        parts.append(f'<rect x="{PAD}" y="{cy + 26}" width="{track}" height="12" '
                     f'rx="6" fill="{BORDER}"/>')
        if pct > 0:
            parts.append(f'<rect x="{PAD}" y="{cy + 26}" width="{max(track * pct / 100, 12):.1f}" '
                         f'height="12" rx="6" fill="{colour}"/>')
    height = y + len(labels) * row + 36
    return _svg(parts + [_footer(height)], height)


def table_svg(title: str, columns: list[str], rows: list[list[object]], *,
              subtitle: str | None = None) -> str:
    """A striped table. A column of numbers aligns right."""
    if not columns or len(columns) > MAX_COLUMNS:
        raise RenderError(f"a table needs 1 to {MAX_COLUMNS} columns")
    if not rows:
        raise RenderError("a table needs at least one row")
    if len(rows) > MAX_ROWS:
        raise RenderError(f"a table image holds at most {MAX_ROWS} rows")
    cells: list[list[str]] = []
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) != len(columns):
            raise RenderError("each row must be a list with one cell per column")
        cells.append(["" if c is None else (fmt(c) if isinstance(c, (int, float))
                                            and not isinstance(c, bool) else str(c))
                      for c in r])
    numeric = [all(isinstance(r[j], (int, float)) and not isinstance(r[j], bool)
                   for r in rows if r[j] is not None)
               for j in range(len(columns))]

    # Each column gets width in proportion to its longest text, with a floor.
    need = [max([_text_width(str(columns[j]), 14, bold=True)]
                + [_text_width(r[j], 14) for r in cells]) + 20
            for j in range(len(columns))]
    avail = WIDTH - 2 * PAD
    floor = min(70.0, avail / len(columns))
    widths = [max(floor, n) for n in need]
    total = sum(widths)
    if total > avail:
        widths = [w * avail / total for w in widths]
    else:
        widths[0] += avail - total

    parts, y = _header(title, subtitle)
    row_h = 34

    def _row(cy: float, values: list[str], *, bold: bool, fill: str) -> None:
        x = PAD
        for j, value in enumerate(values):
            inner = widths[j] - 20
            text = _fit(value, 14, inner, bold=bold)
            if numeric[j]:
                parts.append(_t(x + widths[j] - 10, cy + 22, text, size=14,
                                bold=bold, fill=fill, anchor="end"))
            else:
                parts.append(_t(x + 10, cy + 22, text, size=14, bold=bold, fill=fill))
            x += widths[j]

    _row(y, [str(c) for c in columns], bold=True, fill=MUTED)
    parts.append(f'<line x1="{PAD}" y1="{y + row_h:.1f}" x2="{WIDTH - PAD}" '
                 f'y2="{y + row_h:.1f}" stroke="{BORDER}" stroke-width="2"/>')
    for i, values in enumerate(cells):
        cy = y + row_h * (i + 1)
        if i % 2 == 1:
            parts.append(f'<rect x="{PAD}" y="{cy:.1f}" width="{avail}" '
                         f'height="{row_h}" fill="{STRIPE}"/>')
        _row(cy, values, bold=False, fill=FOREGROUND)
    height = y + row_h * (len(cells) + 1) + 40
    return _svg(parts + [_footer(height)], height)


# ── To a PNG ────────────────────────────────────────────────────────────────


def to_png(svg: str) -> bytes:
    """Rasterise *svg* at ``SCALE``. Raises :class:`RenderError` when the
    renderer is missing or fails, so the tool can tell the model."""
    try:
        import pymupdf
    except ImportError as exc:  # the gateway holds it, a bare install may not
        raise RenderError("this server cannot draw images") from exc
    try:
        doc = pymupdf.open(stream=svg.encode("utf-8"), filetype="svg")
        try:
            pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(SCALE, SCALE), alpha=False)
            return bytes(pix.tobytes("png"))
        finally:
            doc.close()
    except Exception as exc:
        raise RenderError("the image could not be drawn") from exc
