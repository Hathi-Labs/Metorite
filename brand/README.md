# Brand assets — the Metorite comet mark

These are the source files for the Metorite logo. The app does not import them
at build time. They are the masters that each surface copies or inlines. The
apex landing page (`site/index.html`) **inlines** the SVG and does not link
these files, because that subtree forbids external asset fetches
(`site/AGENTS.md`).

## One solid colour

**The comet has one solid colour and no gradient** (owner, 2026-10-30). It is
**black** (`#000000`) on a light surface and **white** (`#ffffff`) on a dark
surface. Each surface that follows a colour mode swaps the fill with that mode.
An accent never changes it.

## Files

| File | Use |
|---|---|
| `metorite-logo.svg` | The main vector. Solid black, for **light** surfaces. |
| `metorite-logo-dark.svg` | The same shape in solid white, for **dark** surfaces. |
| `metorite-logo.png` | The transparent raster master (918×933), in black. The "M" and the gaps between the streaks are clear. Use it for socials and exports. |
| `metorite-logo-white.png` | The white twin of the raster master, for a dark surface. `make_icons.py` writes it. |
| `favicon.svg` | A white comet on a rounded dark tile. The site's tab icon is this file as a `data:` URI. The app's tab icon is the clear comet. |
| `make_icons.py` | Makes the app's tab, home-screen and dot icons from `metorite-logo.png`. Run `uv run --with pillow python brand/make_icons.py`. Its docstring lists each output. |

The app's vector tab icon is `workbench/control_plane/src/app/icon.svg`. It is
`metorite-logo.svg` on a square canvas. It is black, and it changes to white
when the browser frame is dark (`prefers-color-scheme`).

In the app, `components/MetoriteMark.tsx` draws the comet. It is white in dark
mode and black under the `.light` root class.

The mark is a **knockout**. The "M" and the gaps between the motion streaks are
holes (`fill-rule="evenodd"`), so they show the surface behind them.

## Rasters cannot follow the colour mode

A PNG or ICO has one colour, so each raster icon gets a fixed ground:

- The tab fallback (`favicon.ico`) and the dot icons are a black comet on a
  clear ground.
- The app's home-screen icons (`apple-icon.png`, `icon-192.png`,
  `icon-512.png`, `icon-maskable-512.png`) are a black comet on a white tile.
- The site's `apple-touch-icon` is a white comet on the dark tile of
  `favicon.svg`.

## Site palette

The site accent is a separate decision from the mark. The mark no longer
carries purple, and the accent keeps these values:

| Token | Light | Dark |
|---|---|---|
| Site `--brand` | `#5b4ae0` | `#8b7cf6` |
| Site `--brand-strong` (hover) | `#4a39c9` | `#a595f9` |

On the landing page, the CSS rule `.logo-mark path` sets the header comet to
black. A `prefers-color-scheme: dark` rule sets it to white. So one inline mark
serves both themes.
