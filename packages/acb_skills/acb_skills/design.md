---
name: Metorite design system (DESIGN.md)
when_to_use: >
  Load before writing a full-page HTML or Markdown report, or any bespoke
  custom HTML/CSS for an emit_generative_ui "html" node, so the output matches
  the Metorite look. You do NOT need it for named emit_generative_ui
  templates — those are already on-brand by construction.
summary: >
  Palette tokens (--cc-*), typography, spacing, motion, dark/light theming, and
  the full-page report block kit (cc-report, cc-eyebrow, cc-grid, cc-compare…).
---

# Metorite — DESIGN.md

This is the single source of truth for how any UI, document, report, or HTML you
generate should look. Everything you render — a Markdown report, an HTML page, an
`emit_generative_ui` card, a full-page interactive report — must follow this
design language so it feels native to Metorite. When you write HTML/CSS,
use these exact tokens instead of inventing colors or spacing.

---

## 1. Visual theme & atmosphere

Metorite is a **dark-first, professional, technical control surface** —
think a calm mission-control console, not a marketing page. It is precise,
low-noise, and confident. Surfaces are deep blue-grey; content sits on subtly
elevated cards; a single blue accent carries interactivity and a warm orange
accent marks highlights and calls to action. Motion is subtle and purposeful,
never decorative. Prefer clarity and restraint over ornament.

Design mood words: **precise, calm, technical, trustworthy, spacious.**

---

## 2. Color palette & roles

Use these semantic roles, **never a raw hex value**. In-app they exist as CSS
variables (`var(--primary)` etc.); in generated HTML sandboxes they are
pre-injected as `--cc-*` variables (see §9).

⚠️ **The HSL values in the table below are ONE theme's, and they are there to
tell you what a role means — not to be copied.** Metorite is themed:
Settings → Appearance switches the whole org between RapidTool, Fluent, Material
and Graphite, which disagree about palette, corner radius, icon set and control
behaviour. The token resolves to whatever is active when your artifact is
*viewed*, which may be months after you wrote it and may not be what is active
now. Write `var(--primary)` and it follows; write `hsl(198 89% 50%)` and that
part of your artifact leaves the design system permanently — while still
rendering fine, so nobody notices until it looks wrong.

The values shown are RapidTool's dark mode. Light-mode and per-theme overrides
are applied automatically.

| Role | Token | Dark value | Use for |
|------|-------|-----------|---------|
| Background | `--background` | `hsl(220 13% 8%)` | Page / app base |
| Foreground | `--foreground` | `hsl(210 40% 98%)` | Primary text |
| Card | `--card` | `hsl(220 13% 10%)` | Elevated surface / panel |
| Popover | `--popover` | `hsl(220 13% 12%)` | Menus, tooltips |
| **Primary** | `--primary` | `hsl(198 89% 50%)` | Interactive blue: buttons, links, focus, active state |
| Secondary | `--secondary` | `hsl(220 13% 14%)` | Quiet buttons, chips, input fills |
| Muted | `--muted` | `hsl(220 13% 15%)` | Subtle fills |
| Muted text | `--muted-foreground` | `hsl(215 20% 65%)` | Secondary / helper text |
| **Accent** | `--accent` | `hsl(27 96% 61%)` | Warm orange highlight / attention (use sparingly) |
| Success | `--success` | `hsl(142 76% 47%)` | Positive state, done |
| Warning | `--warning` | `hsl(47 96% 53%)` | Caution |
| Destructive | `--destructive` | `hsl(0 63% 60%)` | Danger, delete |
| Border | `--border` | `hsl(220 13% 16%)` | Hairline separators, card outlines |
| Ring | `--ring` | `hsl(198 89% 50%)` | Focus outline (matches primary) |

**Rules:**
- `--primary` is the ONLY interactive color. If it's clickable, it's primary.
  (It is blue on RapidTool; other themes choose their own — which is the point,
  and why the rule names the token and not the colour.)
- `--accent` is a spotlight, not a second button color. Use it for one key
  metric, a highlight, or an "attention" badge — never for large fills.
- Never introduce colors outside this palette. No pure black (`#000`), no pure
  white text on dark (use `--foreground`), no random greens/purples.

---

## 3. Typography

- **Font family:** the app's system UI stack (Inter / system-ui, sans-serif).
  In generated HTML, use `font-family: var(--cc-font, system-ui, -apple-system, "Segoe UI", sans-serif)`.
- Monospace (code, data, keys): `var(--cc-mono)` in a sandbox, `font-mono`
  in-app. Both follow the theme — Fluent uses Cascadia, Material Roboto Mono.
- **Hierarchy:**
  - Page title / H1 — `1.5rem`, weight `600`, tight tracking.
  - Section / H2 — `1.125rem`, weight `600`.
  - Sub-section / H3 — `0.95rem`, weight `600`, often `--muted-foreground`.
  - Body — `0.875rem`, weight `400`, line-height `1.6`.
  - Caption / meta — `0.75rem`, `--muted-foreground`, often uppercase with
    `letter-spacing: 0.05em` for section labels.
- Numbers and metrics can be larger (`2rem`+, weight `600`) — data is the hero.
- Do not use more than two weights in one component. Keep it quiet.

---

## 4. Component styling

- **Buttons** (primary): background `--primary`, text `--primary-foreground`,
  `border-radius: calc(var(--radius) - 0.25rem)`, padding `0.5rem 1rem`, weight
  `500`, `hover: opacity 0.9`, `transition: 150ms`. Secondary buttons use
  `--secondary` fill with `--foreground` text.
- **Cards:** `--card` background, `1px solid --border`, `border-radius: var(--radius)`
  (`0.75rem`), padding `1rem–1.5rem`. Elevation via a soft shadow, not a heavy one.
- **Inputs / selects / textareas / sliders:** `--secondary` (or `--background`)
  fill, `1px solid --border`, `--radius` corners, focus ring `--ring`. Sliders
  use `--primary` for the thumb and filled track.
- **Badges / pills:** small, `--secondary` fill, `--muted-foreground` text, thin
  border; status badges tint toward success/warning/destructive.
- **Tables:** hairline `--border` row separators, `--muted-foreground` header row
  (uppercase caption style), comfortable cell padding (`0.5rem 0.75rem`), no heavy
  gridlines.
- Interactive elements always show a hover and a focus state.

---

## 5. Layout & spacing

- Spacing scale (multiples of `0.25rem`): `4, 8, 12, 16, 24, 32, 48px`. Stick to
  the scale; don't use arbitrary values.
- Generous whitespace. Group related content in cards; separate groups with space,
  not lines, where possible.
- Content max-width for readable documents: ~`720–860px`, centered.
- Use CSS grid / flexbox for structure. Dashboards: responsive grid of cards
  (`repeat(auto-fit, minmax(220px, 1fr))`).

---

## 6. Depth & elevation

- Elevation is subtle. Base page → cards → popovers, each one step lighter
  (`8% → 10% → 12%` lightness) plus a soft shadow.
- Shadows: soft and low-spread, e.g. `0 1px 3px rgba(0,0,0,0.3)` for cards,
  slightly larger for popovers. No harsh, high-contrast drop shadows.
- Borders do most of the separating work; shadows are a light accent.

---

## 7. Motion

- Default transition: `150ms` for hovers/small state, `200–250ms` for panels.
- Easing curve: `cubic-bezier(0.25, 0.46, 0.45, 0.94)` (exposed as `--cc-ease`).
- Animate opacity and transform (fade/slide/scale). Avoid animating layout
  properties. Keep entrances short and calm (a 200ms fade-up, not a bounce).
- Respect `prefers-reduced-motion` — gate non-essential animation behind it.

---

## 8. Do's and don'ts

**Do**
- Use the semantic tokens/variables for every color, radius, and spacing value.
- Let data be the hero: big numbers, clear labels, quiet chrome.
- Prefer a card + table + a single accent metric over a wall of text.
- Keep one primary action per view; make it blue.

**Don't**
- Don't hard-code hex colors, pure black/white, or off-palette hues.
- Don't use more than one orange accent per view.
- Don't add decorative gradients, glows, or drop shadows for their own sake.
- Don't cram — when in doubt, add spacing and remove borders.
- Don't invent a new visual style; match what's described here.

### 8.1 Avoid the generic-AI look

There is a recognisable house style that LLM-generated pages fall into, and it
reads as unconsidered. Actively avoid it:

- **No purple/violet gradients.** This is the single strongest tell. Command
  Center is blue (`--cc-primary`) with one warm-orange accent (`--cc-accent`).
- **No `font-family` declaration at all** — the sandbox already sets the
  on-brand stack. Naming Inter explicitly is a tell.
- **Don't centre everything.** Reports are left-aligned documents. Centre stat
  tiles and gauges; leave prose, headings, and tables ranged left.
- **Vary the corner radius.** Uniform pill-rounding on every element flattens
  hierarchy — `--cc-radius` on cards, tighter on buttons and inputs.
- **No emoji as iconography.** Use the `cc-dot`, `cc-ico`, and `cc-mark` blocks,
  or a Lucide icon via `ccIcon('Name')`.

The test is not "does this look nice in isolation" but **"does it look like it
was designed for this product?"**

---

## 8.5 Your output is linted — read the warnings

Generated HTML runs in a sandbox that fails **silently**: a CDN `<script src>`
is blocked with no error, a misspelled `cc-` class renders unstyled, and a
`cc-bar` without its `--v` custom property draws an empty track. You cannot see
any of this from the tool result alone.

So `write_artifact` (for `.html` files) and `emit_generative_ui` (for `html`
nodes) both **lint the markup and return a `warnings` list** when something is
wrong. Treat it as part of the result, not noise:

- **`ERROR:`** — the artifact is genuinely broken (almost always a remote URL
  the CSP blocks). Fix it and write again with `overwrite=True`.
- **`warning:`** — it renders, but off-brand or degraded (unknown class,
  missing `--v`, hard-coded hex, no `cc-report` wrapper).

A clean write returns no `warnings` key at all. If you get warnings, fix them
in the same turn — the user should never be the one to discover the artifact is
broken.

---

## 9. Generating documents & HTML (how this applies to your output)

**Markdown documents** (`write_artifact("outputs/report.md", ...)`): the app
renders them with the design system automatically (headings, tables, code blocks
are already themed). Just write clean, well-structured Markdown — clear headings,
tables for tabular data, short paragraphs. The live preview shows exactly how the
user sees it.

**HTML documents / reports.** The SAME building blocks and pre-injected CSS
below back BOTH ways of showing HTML — pick the surface by how much you're
presenting:

- **Inline in the chat** — emit an `emit_generative_ui` **`html` node**. It
  renders as a self-contained card *in the message stream* (auto-height, framed).
  Use it for something compact and glanceable: a couple of KPI stats, one chart,
  a decision box, a small comparison — a few blocks the user reads without leaving
  the conversation.
- **Full-page report in the side panel** — `write_artifact("outputs/<name>.html", ...)`.
  It opens automatically in the document side panel (chrome-less, full height),
  and it is **saved** — it persists, is downloadable, and can be re-opened later.
  Use it for something substantial and multi-section: an analysis, a plan, a
  briefing, an audit — anything the user will keep or scroll through.

### Logos, icons and images — what actually works

Artifacts run offline in a locked sandbox (`default-src 'none'`, `img-src data:`).
**Every remote URL fails silently** — no broken-image icon, no console error, just
nothing. That includes company logos from Clearbit, Google favicons, CDN icon
sets, and Google Fonts. If you write one, the tool result will now tell you.

Three things that DO work:

| Want | Use | Notes |
|---|---|---|
| An icon | `<Icon name="trending-up" />` from `@cc/ui`, or `<span data-cc-icon="trending-up">` in HTML | Any [Lucide](https://lucide.dev/icons) name. Resolved to inline SVG by the parent, so no network. Inherits `currentColor`. |
| A company logo / photo | A `data:` URI | Only if you already hold the bytes. There is no way to fetch one. |
| A chart, diagram, sparkline | The kit blocks (`Chart`, `Spark`, `Bars`, `Donuts`, `cc-arch`) | Pure CSS/inline SVG — this is the intended route, not an image. |

The icon **name must be a literal string** — `<Icon name="rocket" />` works,
`<Icon name={someVariable} />` cannot be seen by the resolver and renders nothing.

When you want a brand mark you cannot embed, use the company's initials in a
`cc-node` or a `Chip` instead. That reads as deliberate; a missing image reads as
broken.

### Which surface? Let the volume decide

Pick the surface from the shape and size of the answer, before writing any UI:

| The answer is… | Surface | How |
|---|---|---|
| A one-line fact, or a narrative explanation | Prose | Just say it |
| ≤8 rows · ≤4 metrics · one chart · a status · a comparison | **Inline card** in the chat | `emit_generative_ui` (`template` → `tree` → `react` → `html`) |
| >12 rows · several sections or charts · anything they'll scroll, sort, filter, or return to | **Side panel** | `"surface": "panel"` for a live view |
| A deliverable they'll keep, re-open, or download | **Saved artifact** | `write_artifact("outputs/<name>.jsx"` or `.html")` |
| Something they must *act* on — filter, sort, pick, set a value | **React + `@cc/ui`** | `load_artifact_kit()`, then compose |

Two tests that resolve most cases:

- **Would this need its own scrollbar inside a chat bubble?** Then it belongs in
  the panel, not inline.
- **Am I about to type a markdown table?** If it is more than a few rows, render
  it instead — a `Table` from `@cc/ui` (or the `cc-table` block) is more readable
  and no more work.

Between 8 and 12 rows either is defensible; prefer inline unless the table is
part of a larger answer. And when a big result could genuinely be *either* a
saved document or a live dashboard, **ask** rather than guessing — an
`optionPicker` template with `"hitl": true` gets an answer in the same turn.

Rule of thumb: **a few blocks the user just needs to see → inline `html` node;
a full document the user will keep or read at length → a saved `outputs/*.html`
report.** The markup is identical either way — the same `cc-report` container and
`cc-*` blocks work in both, so you never rewrite to move between them.

**React artifacts.** When the thing you're building is genuinely *interactive*
rather than a document — a filterable table, a multi-step form, a calculator, a
live dashboard — write a real React component instead of hand-rolling DOM
scripting. Same two surfaces, same design kit:

- **Inline in the chat** — an `emit_generative_ui` **`react` node**
  (`{"type":"react","props":{"code":"…"}}`).
- **Full-page in the side panel** — `write_artifact("outputs/<name>.jsx", …)`.

Default-export the component. **Don't hand-write the markup** — import the same
blocks documented below as ready-made components from `@cc/ui`:

```jsx
import { useState } from "react";
import { Report, Eyebrow, Stats, Stat, Bars, Submit } from "@cc/ui";

export default function Dashboard() {
  const [region, setRegion] = useState("all");
  return (
    <Report>
      <Eyebrow>Pipeline</Eyebrow>
      <h1>Deals by region</h1>
      <Stats><Stat label="Revenue" value={18} unit="%" delta={12} /></Stats>
      <Bars data={[{ label: "North", value: 72 }, { label: "South", value: 41 }]} />
      <Submit label="Region" value={region}>Send to agent</Submit>
    </Report>
  );
}
```

**Call `load_artifact_kit()` for the component list** (a few hundred tokens), then
`load_artifact_kit("Stat,Bars")` for the exact props of the ones you picked. That
is much cheaper and more reliable than writing the divs yourself, and the output
is on-brand by construction. Anything the kit doesn't cover: drop to the `cc-*`
classes below — they work identically inside a React artifact.

Hooks all work and JSX/TypeScript are compiled for you. The one hard limit is
that the sandbox has **no network**: you may import only from `@cc/ui`, `react`,
and `react-dom/client` — no npm packages, no CDNs, no icon libraries — so inline
your helpers and seed the data in the file. Reach the agent with `<Submit>` /
`<Action>` (or `window.ccSubmit("Label", value)` / `window.ccAction("message")`),
available from first mount. A build failure comes back in the tool result with
compiler errors — fix and emit again.

Both render in a locked sandbox with these CSS variables **pre-injected** — use
them, don't redefine colors:

```
colour   --cc-bg --cc-card --cc-fg --cc-muted --cc-border --cc-secondary
         --cc-primary --cc-primary-fg --cc-accent
         --cc-success --cc-warning --cc-danger  (+ -fg ink for each)
type     --cc-font --cc-mono --cc-heading-weight --cc-heading-tracking
shape    --cc-radius --cc-border-width --cc-shadow
motion   --cc-duration --cc-ease
control  --cc-button-radius --cc-control-filled-border --cc-control-state-layer
         --cc-control-focus-ring --cc-control-label-tracking
         --cc-control-label-transform
```

Text on a coloured fill takes the `-fg` partner, never `white` — some themes
ship a pale warning colour and white-on-pale is invisible. The full list with
per-token guidance is in `apps/agents/agent-app-builder/instructions.md`, which
a test checks against the code that injects them in both directions — so a token
listed here always exists, and one that exists is always listed.

- Wrap the document in a container with `max-width` and centered layout.
- Use `--cc-card` panels on the `--cc-*` background, `--cc-border` hairlines,
  `--cc-primary` for anything interactive, one `--cc-accent` highlight.
- Native `button/input/select/textarea` and range sliders are pre-styled on-brand
  — you usually don't need to style them.

**Report design kit (USE THESE for full-page HTML documents & reports).** A set of
pre-styled, on-brand building blocks is available — write terse semantic HTML with
these class names and you get a polished Metorite report with zero custom CSS.
Wrap the whole document in `<div class="cc-report">…</div>`. Blocks:

- `cc-eyebrow` — a mono uppercase kicker with a leading rule (section label).
- `cc-sec-num` — a small accent section number/tag placed before an `<h2>`.
- `cc-lede` — the large intro paragraph under the title.
- `cc-callout` (accent) / add `cc-callout-key` (blue) — a tinted highlight panel;
  put a `<span class="cc-tag">Note</span>` then a `<p>` inside.
- `cc-chips` with `cc-chip` children (use `<b>` for the value) — metadata row.
- `cc-grid` of `cc-card`s (each `<h4>` may lead with `<span class="cc-dot">`) —
  responsive card grid for parallel points.
- `cc-compare` wrapping a `<table>` — comparison table; mark cells with
  `cc-yes` / `cc-no` / `cc-partial`, or a `cc-pill` for status.
- `cc-diagram` wrapping a `<pre>` — ASCII architecture/flow diagram; use `<b>` for
  key nodes and `<span class="cc-hl">` for accent notes.
- `cc-steps` of `cc-step` (each has a `<div class="cc-n">1</div>` + `<h4>`/`<p>`) —
  numbered sequence (ONLY when order truly matters).
- `cc-phase` rows (each has `<span class="cc-badge">Phase 1</span>` + content) —
  a phased plan / roadmap. `cc-badge` is a standalone solid label chip and is
  safe anywhere — inside a callout, a table cell, next to a heading.

**Data-viz & decision blocks (USE THESE for charts, KPIs, decisions, and
architecture — never hand-roll SVG paths or bar math).** These are pure
CSS / inline-SVG: you pass a value, the block draws itself. Set a color tone on
any of them by adding `cc-t-success` / `cc-t-warning` / `cc-t-danger` /
`cc-t-accent`.

- **KPI stats** — `cc-stats` grid of `cc-stat`, each `<p class="cc-k">LABEL</p>`
  then `<div class="cc-v">42<small>%</small></div>` and an optional
  `<div class="cc-d cc-up">+12% MoM</div>` (`cc-up`/`cc-down` add a ▲/▼ and
  color). Add `cc-feature` to ONE tile to spotlight it in accent. Reach for this
  whenever a number is the headline — big number, quiet label.
- **Bar chart** — `cc-bars` containing rows: each
  `<div class="cc-bar" style="--v:72"><b>Label</b><div class="cc-track"></div><span>72%</span></div>`.
  `--v` is the percent (0–100) — you compute the percent, the CSS draws the fill.
  No `<svg>`, no width math.
- **Donut / ring gauge** — `cc-donuts` grid of
  `<div class="cc-donut" style="--v:64"><div class="cc-ring"><span>64<small>%</small></span></div><b>Label</b></div>`.
  One `--v` percent → a conic ring. Use for a single completion / share figure.
- **Sparkline** — wrap a tiny inline SVG in `cc-spark`; the polyline is already
  themed (stroke/fill), so you just plot points:
  `<div class="cc-spark"><svg viewBox="0 0 100 30" preserveAspectRatio="none"><polyline points="0,20 25,12 50,16 75,6 100,9"/></svg></div>`.
  Add a `<polyline class="cc-fill">` (closed to the baseline) for an area fill and
  a `<circle class="cc-dot">` to emphasize the endpoint.
- **Decision / recommendation box** — `<div class="cc-decision">` (add
  `cc-t-warning`/`cc-t-danger` to change the verdict color) with a
  `<div class="cc-mark">✓</div>`, then
  `<div><p class="cc-verdict">Recommendation</p><h4>Go with option B</h4><p>…why…</p></div>`.
  Use this for a clear call-to-action verdict, not a generic note (that's
  `cc-callout`).
- **Architecture diagram (visual)** — `cc-arch` of `cc-node` boxes joined by
  `cc-arrow` (→ by default, `cc-arrow cc-down` for ↓, `cc-arrow cc-bi` for ⇄).
  Each node: `<div class="cc-node cc-primary"><div class="cc-node-t">Gateway</div><div class="cc-node-s">FastAPI</div></div>`.
  Wrap a stacked tier in `cc-arch-row`. Add `cc-primary`/`cc-accent`/`cc-muted`
  (dashed) to a node. Prefer this over the ASCII `cc-diagram` for a real
  box-and-connector architecture; keep `cc-diagram` for terminal-style flows.
- **Legend** — `cc-legend` of `<span><i class="cc-t-success"></i> Passing</span>`
  to key a chart's colors.
- **Status callout** — `<div class="cc-note cc-warning"><span class="cc-ico">!</span><p><strong>Risk.</strong> …</p></div>`.
  Tones: `cc-info` (blue), `cc-success` (green ✓), `cc-warning` (amber !),
  `cc-danger` (red ✕). Use for a short status notice; use `cc-decision` when
  there's a verdict/recommendation, `cc-callout` for a neutral highlight.
- **Data table + status cells** — `<div class="cc-table"><table>…</table></div>`
  for ops/audit/status tables. Cell helpers: `td.cc-num` (mono, right-aligned
  numbers), `td.cc-dim` (muted), a leading `<td class="cc-stripe cc-t-danger">`
  for a row severity stripe, `<span class="cc-status cc-t-success">ok</span>`
  (dot + label), `<span class="cc-minibar" style="--v:74"></span>` (inline bar),
  and `<span class="cc-tag-pill cc-t-warning">Degraded</span>` (solid pill).
  Use this over `cc-compare` when rows are records with status, not options.
- **Timeline / roadmap (Gantt)** — `<div class="cc-timeline" style="--cols:12">`
  with rows: `<div class="cc-tl-row"><b>Design</b><div class="cc-tl-track"><span class="cc-tl-bar" style="--s:1;--e:3">Design</span></div></div>`.
  `--cols` = total time columns; each bar's `--s` = start column (1-based),
  `--e` = span in columns. Add a tone class to a bar, or `cc-ghost` for a
  planned/unstarted bar. Optional axis: a `<div class="cc-tl-axis" style="--cols:12"><div></div><div class="cc-tl-ticks"><span>Jan</span>…</div></div>`
  row of tick labels. Prefer this over `cc-phase` when timing/overlap matters.
- **Trend line / area chart** — `<div class="cc-chart">` wrapping an inline
  `<svg class="cc-plot" viewBox="0 0 300 120" preserveAspectRatio="none">`; the
  classes theme it, so you only supply geometry: a `<g class="cc-grid">` of
  `<line>`s, a `<polyline class="cc-area">` closed down to the baseline for the
  fill, a `<polyline class="cc-line">` for the trend, and a `<circle class="cc-end">`
  at the last point. Follow with `<div class="cc-x"><span>Jan</span>…</div>` for
  x-axis labels. Use for a real multi-point trend; use `cc-spark` for a tiny
  inline one and `cc-bars` for categorical values.

Compose these instead of hand-rolling report CSS; they already match this
document's palette, spacing, typography, and both light/dark themes. Reach for a
`cc-report` HTML document (opened in the side panel) whenever you produce something
substantial — an analysis, a plan, a comparison, a briefing — rather than a long
plain-text chat reply.
- **Interactivity** (this is encouraged — build reports the user can act on):
  - `data-cc-action='<message>'` on a clickable element (or `ccAction('...')`)
    fires a fixed follow-up message back to the agent, like a button press.
  - `data-cc-submit='<label>'` on a button harvests every named
    input/select/textarea in its enclosing `form`/`[data-cc-form]` and sends
    their VALUES back; or call `ccSubmit('Label', value)` directly. Use this so a
    slider/dropdown/number the user sets actually reaches you.
  - No external network or CDNs — inline all CSS/JS; embed images as data URIs.
- Respect `prefers-color-scheme` / the injected theme; don't force a light page
  onto the dark app.

The guiding test for anything you generate: **would it look at home dropped into
the Metorite app?** If yes, ship it. If it looks like a generic Bootstrap
page, rework it against this document.
