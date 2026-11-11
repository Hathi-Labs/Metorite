"""One colour vocabulary for statuses, tags and types (PJP-1, D168.2).

Spec: ``project-docs/specs/project_planning.md`` §3.2.

A stored colour is one of six hues. Any other colour maps to the nearest hue,
here on the server and in ``statusAccent.ts`` (``resolveHue``) on the client.
``tests/fixtures/hue_vocabulary.json`` holds both sides together: a pytest and
a vitest each read it and compare their own tables.

A name or alias maps through :data:`ALIASES`. A hex code (``#RGB`` or
``#RRGGBB``) maps by its HSL value. A word that is neither maps to ``gray``.
"""

from __future__ import annotations

import colorsys
import re

HUES = ("gray", "red", "amber", "green", "blue", "violet")

#: Same table as ``COLOR_ALIASES`` in ``statusAccent.ts``.
ALIASES: dict[str, str] = {
    "grey": "gray",
    "gray": "gray",
    "red": "red",
    "amber": "amber",
    "orange": "amber",
    "yellow": "amber",
    "green": "green",
    "blue": "blue",
    "violet": "violet",
    "purple": "violet",
    "slate": "gray",
    "sky": "blue",
    "cyan": "blue",
    "emerald": "green",
    "teal": "green",
    "rose": "red",
    "pink": "violet",
}

#: A saturation under this is gray, whatever the angle.
GRAY_SATURATION = 0.30
#: ``(hue, from, to)`` in degrees, ``[from, to)``. Red wraps through 0.
HUE_BANDS: tuple[tuple[str, int, int], ...] = (
    ("red", 345, 15),
    ("amber", 15, 65),
    ("green", 65, 170),
    ("blue", 170, 250),
    ("violet", 250, 345),
)

_HEX = re.compile(r"^#([0-9a-f]{3}|[0-9a-f]{6})$", re.IGNORECASE)


def _hex_hue(code: str) -> str:
    digits = code[1:]
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    r, g, b = (int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4))
    h, _light, sat = colorsys.rgb_to_hls(r, g, b)
    # Round both, as the client does: the two float paths differ in the last
    # bit, and a code on a band edge must land on the same side of it.
    if round(sat, 6) < GRAY_SATURATION:
        return "gray"
    deg = round(h * 360, 6)
    for hue, start, end in HUE_BANDS:
        if (start <= deg < end) if start < end else (deg >= start or deg < end):
            return hue
    return "red"


def hue_of(value: str | None) -> str | None:
    """The hue name of a colour, or None when the value is empty.

    None lets a route keep its own default. An unknown word that is not hex
    is ``gray``, so a stored value is always one of the six.
    """
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in ALIASES:
        return ALIASES[text]
    if _HEX.match(text):
        return _hex_hue(text)
    return "gray"
