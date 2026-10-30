"""Make every app icon from the transparent master, `metorite-logo.png`.

Run from the repo root:

    uv run --with pillow python brand/make_icons.py

The comet has one solid colour: black on a light surface and white on a dark
one (owner, 2026-10-30). The master's alpha is the shape, so this script paints
it, and the master's own colour does not matter. A raster cannot follow the
colour mode, so an icon that can sit on a dark surface gets a white tile.

It writes into `workbench/control_plane/`:

- `src/app/favicon.ico` (16, 32, 48): the tab icon for a browser with no SVG
  icon support. A black comet on a clear ground.
- `src/app/apple-icon.png` (180): the iOS home screen. iOS paints a
  transparent pixel black, so this one sits on a white tile.
- `public/icon-192.png`, `public/icon-512.png`: the web manifest's icons. A
  black comet on a rounded white tile. The splash screen puts them on the
  manifest's dark background, where a black comet on a clear ground is lost.
- `public/icon-maskable-512.png`: Android's adaptive icon. A launcher crops
  it to its own shape, so the mark stays inside the 80% safe zone on white.
- `public/favicon-needs.png`, `public/favicon-reply.png` (64): the hidden
  tab's icons with a dot (`lib/runSignals.ts`). Amber means "needs you", and
  blue means "new reply".

It also writes `brand/metorite-logo-white.png`, the master's white twin, for a
dark surface.

`src/app/icon.svg` is the vector tab icon. It is written by hand from
`metorite-logo.svg`, not by this script, because it switches black to white
for a dark browser frame.
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "brand" / "metorite-logo.png"
APP = ROOT / "workbench" / "control_plane"

# The dot colours of the old icons, kept so the signal reads the same.
NEEDS_DOT = (250, 200, 20, 255)
REPLY_DOT = (38, 181, 242, 255)
WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)


def ink(mark: Image.Image, colour) -> Image.Image:
    """The mark's shape in one solid colour. The alpha is the shape."""
    out = Image.new("RGBA", mark.size, colour)
    out.putalpha(mark.getchannel("A"))
    return out


def square(mark: Image.Image) -> Image.Image:
    """Pad the master to a square, with the mark in the centre."""
    side = max(mark.size)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(mark, ((side - mark.width) // 2, (side - mark.height) // 2))
    return out


def fit(mark: Image.Image, size: int, scale: float = 1.0, background=None) -> Image.Image:
    """The mark at `scale` of a `size` square, on `background` or clear."""
    inner = max(1, round(size * scale))
    small = mark.resize((inner, inner), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), background or (0, 0, 0, 0))
    out.alpha_composite(small, ((size - inner) // 2, (size - inner) // 2))
    return out


def tile(mark: Image.Image, size: int, scale: float, colour) -> Image.Image:
    """The mark on a rounded tile of `colour`, clear outside the corners."""
    # Drawn at 4x and scaled down, so the corners are smooth.
    big = size * 4
    ground = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(ground).rounded_rectangle((0, 0, big - 1, big - 1), radius=round(big * 0.22), fill=colour)
    out = ground.resize((size, size), Image.LANCZOS)
    out.alpha_composite(fit(mark, size, scale))
    return out


def with_dot(icon: Image.Image, colour) -> Image.Image:
    """The icon with a status dot at its foot, cut out of the mark."""
    out = icon.copy()
    size = out.width
    r = round(size * 0.22)
    cx, cy = size - r - 1, size - r - 1
    # A clear ring first, so the dot stays legible on the mark's own colour.
    ring = Image.new("L", out.size, 0)
    ImageDraw.Draw(ring).ellipse((cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3), fill=255)
    out.putalpha(Image.composite(Image.new("L", out.size, 0), out.getchannel("A"), ring))
    ImageDraw.Draw(out).ellipse((cx - r, cy - r, cx + r, cy + r), fill=colour)
    return out


def main() -> None:
    master = Image.open(MASTER).convert("RGBA")
    ink(master, WHITE).save(ROOT / "brand" / "metorite-logo-white.png", optimize=True)
    mark = square(ink(master, BLACK))

    ico = fit(mark, 256)
    ico.save(APP / "src" / "app" / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])

    fit(mark, 180, 0.78, WHITE).convert("RGB").save(APP / "src" / "app" / "apple-icon.png")

    tile(mark, 192, 0.72, WHITE).save(APP / "public" / "icon-192.png")
    tile(mark, 512, 0.72, WHITE).save(APP / "public" / "icon-512.png")
    fit(mark, 512, 0.62, WHITE).convert("RGB").save(APP / "public" / "icon-maskable-512.png")

    base = fit(mark, 64)
    with_dot(base, NEEDS_DOT).save(APP / "public" / "favicon-needs.png")
    with_dot(base, REPLY_DOT).save(APP / "public" / "favicon-reply.png")


if __name__ == "__main__":
    main()
