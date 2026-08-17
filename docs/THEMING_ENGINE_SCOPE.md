# Metorite Theming Engine — Scoping Document

**Status:** Built and shipped through the coverage work (phases 1–4 plus the
full icon migration, org-default backend, contrast gate and third-party
surfaces). See "What was built" below; the rest
of this document is the original research and remains the design rationale.
**Scope:** `workbench/control_plane` (Next.js 16 · React 19 · Tailwind CSS v4)
**Goal:** A theming engine that can restyle the entire Control Plane — colors,
fonts, radii, shadows, icon packs, component "personality" — switchable on the
fly from Settings, with company-wide defaults. Example target themes: the
current RapidTool look, a Microsoft Fluent/Metro ("Lumia") look, a Google
Material look.

---

## 0. What was built

Four themes, switchable from **Settings → Appearance**, each changing colours,
fonts, corner radius, effects and the icon pack across every page:

| Theme | Look | Icons | Fonts |
|---|---|---|---|
| **RapidTool** (default) | The original design, preserved token-for-token | Lucide | Geist |
| **Fluent** | Microsoft Fluent 2 — 4px corners, acrylic, no glow | Fluent System Icons | Segoe UI → Inter |
| **Material** | Google Material 3 — 16px corners, flat + elevation | Material Symbols Rounded | Roboto |
| **Graphite** | Low-distraction monochrome, monospace headings | Lucide | Geist / Geist Mono |

**How it works.** A theme is a manifest in `src/lib/theme/themes.ts` — colours
for both modes, font stacks, shape, effects, icon pack. `css.ts` compiles every
manifest to an `html[data-theme="…"]` custom-property scope, inlined once in
the document head, so switching is a single attribute write with no fetch and
no flash. *Style* (`data-theme`) and *mode* (the next-themes `.light` class)
are independent axes. A pre-paint boot script applies the stored preference
before the first frame.

Adding a theme is a manifest entry — no component, CSS or Tailwind change.

**Icons** go through `<Icon name="Plus" />`, using Lucide names as the shared
vocabulary. **Every call site in the app is migrated** — 158 files; the only
modules still importing `lucide-react` are `Icon.tsx` (the primitive, which
falls back to Lucide) and `lib/icons.tsx` (the resolver used by server
components and by `iconSvg.ts`'s static-string rendering, neither of which can
run hooks). All 251 Lucide icons the app uses resolve in both non-Lucide packs.
Those packs are pruned Iconify collections (275 icons, ~115 KB and ~106 KB)
built by `scripts/build-icon-packs.mjs`, fetched lazily only when a theme needs
them, and rendered offline.

`themedIcon(name)` returns a memoised component bound to one name, for tables
that store an icon rather than rendering it inline; `ThemedIcon` is its type.

**Apps and generated UI follow the theme too.** Custom Apps, generative-UI
cards and React artifacts run in an opaque-origin iframe: they inherit nothing —
not our stylesheet, not `data-theme`, not one custom property. They already had
a token vocabulary (`--cc-*`, written into `agent-app-builder`'s instructions
and `acb_skills/design.md`), but its **values were hand-written RapidTool
literals switching only on light/dark**, so every app ever built stayed
RapidTool-blue while the shell around it turned Fluent or Material. Nothing
errored; it just quietly did not theme.

`app-tokens.ts` now derives that block from the active manifest, and
`sandbox-frame.ts` builds the frame around it. Colour, ink pairs, type, shape,
motion and the theme's *control personality* (Material's pill buttons and 8%
state layer, Fluent's tight ring, Graphite's uppercase labels) all cross, so an
app built a year ago picks up a new theme's behaviour and not merely its
palette. Icons cross as SVG resolved from the active pack. The block is applied
twice: in the frame's first `<style>` so there is no flash, and as a live
`postMessage` patch on a theme change — a patch rather than a rebuild, because
rebuilding `srcDoc` remounts the document and a published app would throw away
whatever the user had typed into it.

`--cc-*` is deliberately a *separate, smaller, stable* vocabulary rather than
our own tokens: our set is Tailwind's `@theme inline` surface and we rename
things in it, while `--cc-*` is published into apps we cannot go back and edit.
The two are kept in sync by a test that checks the code against the agent
instructions in both directions. One honest limit: **self-hosted webfonts do not
cross** — the frame's CSP is `font-src data:` — so an app gets the theme's named
and system families (Segoe UI, Roboto) and falls back where one is absent.

**Staying themed** is enforced, not asked for. `conformance.test.ts` fails the
build on a hardcoded colour, a `lucide-react` import, an arbitrary Tailwind
colour class, or a hand-rolled solid control. Existing debt is a frozen
baseline that may only shrink: a file with no budget must be clean, a baselined
file may not get worse, and one that got *better* fails until its number is
lowered — so the figures can never quietly become fiction. Values that are
genuinely not theme decisions (weather pictograms, Gmail's label palette, a
person's identity hue) sit in an exceptions list with the argument for each.

**Third-party surfaces** — Monaco and Shiki — name their equivalents per theme
in `surfaces`, so a code view follows the theme rather than only the colour
mode. Fluent highlights with VS Code's own dark-plus, Material with
material-theme, Graphite with the minimal themes. xyflow's `colorMode` stays
dark/light on purpose: it drives only that library's chrome, and our nodes
already use our tokens.

**Preferences** resolve member override → org default → built-in. Personal
choices are per-browser. The **org default persists in Postgres** —
`org_settings` (migration `151_org_settings.sql`), served by the gateway at
`GET/PUT /settings/appearance` — and is cached locally so it survives first
paint. Reading is open to any member (every client needs it on load); writing
requires `admin:settings:manage`, since it changes everyone's UI. Admins can
lock the org to one theme. Both layers validate independently, and the frontend
still falls back with `orgManaged: false` if the migration has not been applied
or the gateway is down.

The gateway deliberately does **not** know which themes exist: `themeId` is an
opaque, selector-safe string. Validating it against a copy of `THEMES` would
mean a backend deploy for every new theme, and a mismatch would reject a theme
the app can render.

**Contrast is gated.** Every theme × mode × text pair is measured against WCAG
AA and the build fails below it. Seven pairs in the original RapidTool palette
predate the gate and are recorded as a ratchet — they may improve, never
regress, and fixing one forces its entry to be deleted so the list cannot go
stale. New themes must meet AA outright. Two shortfalls introduced by the new
Fluent and Material themes were fixed rather than recorded.

**Verified.** 359 frontend unit tests (manifests, CSS generation, icon
registry, contrast, surface themes), 22 gateway tests, and 14 browser tests
(`e2e/theming.spec.ts`) asserting computed styles and real glyph swapping —
the only place CSS cascade order can actually be checked. A drift guard parses
`globals.css` and fails if its no-JavaScript fallback diverges from the default
manifest.

**Migrations were applied and verified** against a real Postgres 16 with
`pgvector` and `age` installed: all 143 numbered migrations replay cleanly in
order, twice (they are idempotent, and the deploy runner re-runs them every
time), and the store → route round trip works end to end.

⚠️ **`infra/postgres/schema.generated.sql` was deliberately NOT regenerated.**
Doing so from a clean replay would have DELETED ~71 tables of real schema. That
file was dumped from a Postgres instance shared with LiteLLM, Langfuse and
mem0, whose tables come from their own migrations, not this repo's. It is also
badly stale on its own terms — missing 86 tables the numbered migrations
create (crm, gtd, wa, workflows, apps, org access control). Refreshing it
needs a dump from a real deployment running all three systems, and is a
pre-existing chore unrelated to theming.

**Not done (Phase 5).** Shared `<Button>` / `<Input>` primitives. Until they
exist, themes differ in colour, shape, font, effects and icons, but not in
component *behaviour* — no Fluent inner borders, no Material state layers — and
the `--heading-weight` / `--label-weight` tokens have nowhere to apply.

Also outstanding: ~120 hardcoded hex colours, 54 of them in
`genUITemplates.tsx` (the templates agent-generated UI is built from) and 41 in
the observability pixel-art sprites, which are artwork and arguably should stay
hardcoded. And seven pairs in the original RapidTool palette sit below WCAG AA;
they are pinned as a ratchet, and fixing them is a brand decision.

---

## 1. What we already have (audit)

The codebase is unusually well-positioned for this. The hard prerequisite for
a theming engine — *components reference semantic tokens instead of raw
values* — is already largely true.

| Building block | Current state |
|---|---|
| **Color tokens** | shadcn/ui-compatible semantic CSS custom properties (`--primary`, `--background`, `--card`, `--muted-foreground`, …) in `src/app/globals.css`, mapped to Tailwind utilities via v4 `@theme inline`. Dark is `:root`, light is `.light`. |
| **Theme switching** | `next-themes` already installed and mounted in `Providers.tsx` (`attribute="class"`, dark/light only). |
| **Radius** | Tokenized: `--radius` drives `rounded-lg/md/sm` via `@theme inline`. One variable changes the roundness of the whole app. |
| **Fonts** | Geist Sans/Mono via `next/font`, exposed as `--font-geist-sans` / `--font-geist-mono` and wired to `font-sans` / `font-mono`. |
| **Icons** | `lucide-react`, imported **directly in ~160 files**, ~115 unique icons. This is the biggest migration surface. |
| **Design system doc** | `workbench/control_plane/DESIGN_SYSTEM.md` mandates tokens, shared components, and layout patterns — agents already follow it. |
| **Settings infra** | `/settings/*` pages + `/api/settings/*` API routes that persist to the backend. An "Appearance" section slots right in. |

### Gaps that block multi-theming today

1. **Only 2 themes are modeled.** `next-themes` is configured for `dark`/`light`
   classes only; there is no concept of a *style* (RapidTool / Fluent /
   Material) orthogonal to *mode* (dark / light).
2. **Icons are hard-imported.** 160 files `import { X } from "lucide-react"` —
   no indirection layer, so an icon-pack swap currently means editing every file.
3. **Hardcoded values outside the token system.** `tech-glass`, `tech-glow`,
   `chat-shimmer-text`, scrollbars, and the ProseMirror selection color embed
   raw `hsl(...)` values in `globals.css`; a handful of components use inline
   hex/hsl.
4. **No shared `<Button>`/`<Input>` primitives.** The design system specifies
   button *class strings*, not components. Colors/radius will theme fine via
   tokens, but per-theme *shape personality* (e.g. Fluent's 4px squarish
   buttons vs. Material's pill FABs) has no single place to live.
5. **Typography scale is not tokenized.** Sizes like `text-xs`/`text-[10px]`
   are hardcoded; only the font *family* is a variable.
6. **No per-user/org persistence for appearance.** Mode lives in
   `localStorage` via next-themes only.

---

## 2. Recommended architecture: token-driven themes, not component libraries

**Core decision: do NOT adopt a per-theme component library** (Fluent UI
React, MUI, daisyUI, Radix Themes). Swapping component libraries per theme
means N parallel implementations of every screen — a rewrite, not a theme
switch. Instead, keep our own components and make *every visual decision a
design token*, then ship themes as token bundles:

```
Theme = {
  metadata      (id, name, author, preview colors)
  color tokens  (dark + light variants — mode stays orthogonal to style)
  font tokens   (sans / mono / display stacks → next/font variables)
  shape tokens  (radius scale, border widths, shadow scale, density)
  effect tokens (glass, glow, transition curves — or "none" for flat themes)
  icon pack id  ("lucide" | "fluent" | "material" | …)
}
```

### 2.1 CSS layer — themes as `data-theme` scopes (zero new dependencies)

Tailwind v4's CSS-first design makes this nearly free. `@theme inline` already
maps utilities to `var(--*)`; we only add per-theme variable scopes:

```css
/* globals.css — theme scopes set the *source* variables */
:root, [data-theme="rapidtool"] {            /* current design, unchanged */
  --primary: hsl(198 89% 50%); --radius: 0.75rem; --font-app: var(--font-geist-sans); …
}
[data-theme="fluent"] {                       /* Microsoft / Lumia-inspired */
  --primary: hsl(206 100% 42%);  /* Fluent blue #0078D4 */
  --radius: 0.25rem;             /* squarish Fluent corners */
  --font-app: var(--font-segoe-alike);
  --effect-glass-opacity: 0.85;  /* acrylic */
}
[data-theme="material"] {                     /* Google Material 3 */
  --primary: hsl(256 34% 48%);   /* M3 primary40 */
  --radius: 1.25rem;             /* M3 large shape */
  --font-app: var(--font-roboto);
  --effect-glass-opacity: 1;     /* flat surfaces, elevation via shadow */
}
/* mode stays a separate axis, exactly as today */
[data-theme="fluent"].light { … } etc.
```

`next-themes` natively supports arbitrary named themes and can even manage
the mode × style matrix for us (`themes: ["rapidtool-dark", "rapidtool-light",
"fluent-dark", …]`), or we keep next-themes for mode (class) and set
`data-theme` ourselves from a small zustand store — recommended, since we
already use zustand and it keeps "style" and "mode" as clean separate axes.
An inline `<script>` in `layout.tsx` (same trick next-themes uses) applies the
stored theme before first paint to avoid flash.

### 2.2 Icon layer — semantic `<Icon>` component with swappable packs

Introduce `@/components/icon`:

```tsx
<Icon name="add" className="w-4 h-4" />   // renders Plus | AddRegular | MdAdd
```

- A **semantic registry** maps ~115 icon names → per-pack components:
  `{ add: { lucide: Plus, fluent: AddRegular, material: MaterialSymbolsAdd } }`.
- Pack per theme comes from the theme manifest; unmapped icons fall back to
  lucide so a new pack can be added incrementally.
- Migration of the 160 call-sites is a mostly mechanical codemod
  (`import { Plus } from "lucide-react"` → `<Icon name="add">`), doable
  file-by-file — old and new styles coexist during migration.
- Icon sources (all open source, tree-shakable, offline):
  - **lucide-react** (current) — keep as base/fallback.
  - **@fluentui/react-icons** (MIT, Microsoft's official Fluent System Icons) —
    the authentic Fluent look.
  - **Material Symbols** via **@iconify/react + @iconify-json/material-symbols**
    (Apache-2.0) — Iconify renders any of its 200k+ icons offline from JSON
    collections, which also future-proofs adding more packs (Tabler, Phosphor,
    Heroicons…) without new per-pack APIs.

### 2.3 Fonts — preloaded `next/font` families switched by variable

Load 3–4 families up front in `layout.tsx` (Geist, an Open-Sans/Segoe-alike
such as **Selawik** (Microsoft's open-source Segoe substitute, SIL OFL) or
Inter for Fluent, **Roboto/Roboto Flex** for Material). Each registers a CSS
variable; `--font-app` per theme selects one. Cost is a few 10s of KB per
subset font, self-hosted by `next/font` — no runtime font loading needed.

### 2.4 Component personality — tokenize the remaining hardcoded styles

- Convert `tech-glass` / `tech-glow` / scrollbar / shimmer colors to derive
  from tokens (`color-mix(in oklch, var(--card) …)`) with per-theme effect
  variables, so flat themes (Material) can disable glow/glass entirely.
- Extract `<Button>`, `<Input>`, `<Badge>` primitives from the class-string
  recipes in `DESIGN_SYSTEM.md`. This is where deeper per-theme shape
  differences (Fluent's 1px inner borders, Material's state-layer hover)
  get expressed once instead of 160 times. Not a blocker for v1 — tokens
  alone already carry color/radius/font — but it's the path to "really feels
  like Fluent/Material" fidelity.

### 2.5 Settings & persistence — org default + per-user override

- **Settings → Appearance** page: theme gallery cards (mini live preview
  rendered from each theme's tokens), mode toggle (dark/light/system),
  optional accent-color override.
- Persistence mirrors the existing pattern: `GET/PUT /api/settings/appearance`
  stores the **org-wide default** ("across the company") in backend config;
  per-user override stored with the member profile; `localStorage` caches the
  resolved theme for no-flash first paint. Resolution order:
  user override → org default → built-in default.
- Because themes are data (a JSON manifest + CSS variables), new themes can
  later be added *without code changes* — including agent-generated ones.

---

## 3. Open-source landscape — what to adopt, borrow, or skip

| Project | License | What it offers | Verdict |
|---|---|---|---|
| **next-themes** ([repo](https://github.com/pacocoursey/next-themes)) | MIT | No-flash theme switching, arbitrary named themes, `data-theme`/class attributes, system-mode | **Adopt (already installed).** Keep for mode; style axis via store + `data-theme`. |
| **tweakcn** ([repo](https://github.com/jnsahaj/tweakcn), [editor](https://tweakcn.com/editor/theme)) | Open source (MIT) | Visual no-code theme editor for exactly our token set (shadcn variables): colors, typography, radius, shadows; 16+ ready presets (Catppuccin, Supabase, Graphite…); exports plain CSS variables | **Adopt as theme-authoring tool.** Our tokens are shadcn-compatible, so its presets import almost verbatim — instant theme catalog seed. Can self-host later. |
| **@iconify/react + @iconify-json/*** ([icon sets](https://icones.js.org)) | MIT / per-set | One renderer for 200k+ icons across 150+ sets, offline JSON collections, tree-shakable | **Adopt for non-lucide packs** behind our semantic `<Icon>`. |
| **@fluentui/react-icons** ([npm](https://www.npmjs.com/package/@fluentui/react-icons)) | MIT | Microsoft's official Fluent System Icons as React components | **Adopt for the Fluent theme** (directly or via `@iconify-json/fluent`). |
| **@material/material-color-utilities** ([repo](https://github.com/material-foundation/material-color-utilities)) | Apache-2.0 | Google's official M3 dynamic-color engine (HCT color space): full accessible palette from one seed color | **Adopt for the Material theme + "accent color" feature** — generate a correct, contrast-safe scheme from a single user-picked color. |
| **shadcn/ui theming + registry format** ([docs](https://ui.shadcn.com/docs/theming)) | MIT | The token naming convention we already use; registry JSON format for distributing themes | **Borrow conventions.** Keeps us compatible with the whole shadcn theme ecosystem (tweakcn, community themes). |
| **daisyUI 5** ([themes](https://daisyui.com/docs/themes/)) | MIT | Tailwind v4 plugin, 35 built-in `data-theme` themes, theme generator | **Skip as dependency** (brings its own component classes & token names, conflicts with ours) — but **copy its proven `data-theme` scoping pattern** and mine its themes for palette inspiration. |
| **Radix Themes / Fluent UI React / MUI** | MIT | Full themed component libraries | **Skip.** Adopting any means rebuilding every screen on their components; theming ≠ replatforming. |
| **Style Dictionary / Terrazzo (DTCG tokens)** ([style-dictionary](https://github.com/style-dictionary/style-dictionary)) | Apache-2.0 | Design-token build pipeline (W3C DTCG JSON → CSS/TS) | **Defer.** Right answer if themes later need to target more surfaces (email templates, PDFs, mobile). Overkill for one web app now. |
| **theme-change, use-material-you, etc.** | MIT | Small helpers | **Skip** — trivial to do with next-themes/zustand. |

---

## 4. Example theme manifests (target fidelity)

```jsonc
// themes/fluent.json — "Microsoft" (Fluent 2 / Metro-Lumia heritage)
{
  "id": "fluent", "name": "Fluent",
  "iconPack": "fluent",
  "fonts": { "app": "selawik", "mono": "cascadia-code" },
  "shape": { "radius": "0.25rem", "borderWidth": "1px", "density": "compact" },
  "effects": { "glass": "acrylic", "glow": false, "motion": "subtle" },
  "colors": {
    "dark":  { "primary": "hsl(206 100% 50%)", "background": "hsl(0 0% 13%)", … },
    "light": { "primary": "hsl(206 100% 42%)", "background": "hsl(0 0% 98%)", … }
  }
}

// themes/material.json — "Google" (Material 3)
{
  "id": "material", "name": "Material",
  "iconPack": "material-symbols",
  "fonts": { "app": "roboto-flex", "mono": "roboto-mono" },
  "shape": { "radius": "1.25rem", "borderWidth": "0px", "density": "comfortable" },
  "effects": { "glass": "none", "glow": false, "elevation": "m3-shadows" },
  "seedColor": "#6750A4"   // full scheme generated via material-color-utilities
}
```

A build step (or runtime loader) compiles manifests → the `[data-theme="…"]`
CSS blocks, so themes stay declarative data.

---

## 5. Phased implementation plan

| Phase | Work | Outcome | Est. |
|---|---|---|---|
| **1. Token foundation** | Add `data-theme` scoping to `globals.css`; theme store (zustand) + no-flash script; tokenize `tech-glass`/`tech-glow`/scrollbar/shimmer; typography + density variables | Multi-theme capable app; RapidTool = default theme; 2–3 color-only presets imported from tweakcn prove the pipeline | ~2–3 days |
| **2. Settings UI + persistence** | Settings → Appearance page (theme gallery, mode toggle, accent picker); `GET/PUT /api/settings/appearance`; org default + per-user override | Anyone can switch themes from Settings; admins set the company default | ~2 days |
| **3. Icon abstraction** | `<Icon>` component + semantic registry for the ~115 used icons; codemod the 160 lucide import sites; wire pack selection to theme | Icon packs swap with the theme | ~3–4 days (mechanical, parallelizable) |
| **4. Flagship themes** | Fluent theme (Selawik font, Fluent icons, squared radii, acrylic); Material theme (Roboto, Material Symbols, material-color-utilities scheme from seed, M3 elevation) | The "looks like Microsoft / looks like Google" demo | ~3–4 days |
| **5. Component primitives (fidelity pass)** | Extract `<Button>/<Input>/<Badge>` from DESIGN_SYSTEM recipes; per-theme shape/state styling; update DESIGN_SYSTEM.md + agent rules | Deep per-theme personality; future themes get it for free | ~1 week, incremental |

Phases 1–2 alone deliver user-visible theme switching. 3–4 deliver the
Microsoft/Google vision. 5 is polish that can trail indefinitely.

### Risks / notes

- **Agent-authored UI**: `DESIGN_SYSTEM.md` must be updated in lock-step
  (use `<Icon>`, never import lucide directly; never hardcode radii) or
  agents will keep generating non-themeable code.
- **Contrast safety**: every theme must pass WCAG AA in both modes;
  material-color-utilities guarantees it for generated schemes, tweakcn
  presets need a spot check.
- **Charts/editors**: Monaco, xyflow, syntax highlighting have their own
  theme systems — each needs a small per-theme mapping (mostly one-time).
- **Bundle size**: fonts self-hosted via `next/font` (small subsets); icon
  packs are tree-shaken per registry, not whole-set imports.
