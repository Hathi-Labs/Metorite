# Brand assets — the Metorite comet mark

Canonical source files for the Metorite logo. Nothing in the app imports these
at build time yet; they are the masters that surfaces copy or inline from. The
apex landing page (`site/index.html`) **inlines** the SVG rather than linking
these files, because that subtree forbids external asset fetches
(`site/AGENTS.md`).

## Files

| File | Use |
|---|---|
| `metorite-logo.svg` | Primary vector. Faithful navy→purple gradient — for **light** surfaces. |
| `metorite-logo-dark.svg` | Same shape, gradient floor lifted so the base stays legible on **dark** surfaces (≈`#0b1020`). |
| `metorite-logo.png` | Transparent raster master (918×933). All white knocked out — the "M" and streak-gaps are see-through, edges de-fringed. Source for socials/exports. |
| `favicon.svg` | Rounded dark tile + comet, for browser tabs / app icons. |

The mark is a **knockout**: the "M" and the gaps between the motion streaks are
holes (`fill-rule="evenodd"`), so they show whatever is behind them. It reads on
any background *with contrast* — but the navy floor blends into near-black, which
is why the `-dark` variant exists.

## Palette

Measured off the artwork; drives both the mark's gradient and the site accent.

| Token | Light | Dark |
|---|---|---|
| Gradient stops | `#07133c → #4139ae → #a77dfc` | `#3f38a6 → #6355e6 → #b79dfd` |
| Site `--brand` | `#5b4ae0` | `#8b7cf6` |
| Site `--brand-strong` (hover) | `#4a39c9` | `#a595f9` |

On the landing page the gradient is one inline `<linearGradient>` whose stops
carry classes `.mg0/.mg1/.mg2`; a `prefers-color-scheme: dark` rule swaps them to
the dark stops, so a single inline mark serves both themes.
