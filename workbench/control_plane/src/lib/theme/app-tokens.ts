/**
 * The `--cc-*` contract — the theme, as seen by code that is not this app.
 *
 * Three surfaces render markup we do not compile and cannot restyle after the
 * fact: agent-authored HTML in `SandboxedHtml`, React artifacts in
 * `SandboxedReact`, and published Custom Apps from the App Workshop. All three
 * run in an **opaque-origin iframe**, so they inherit nothing — not our
 * stylesheet, not `data-theme`, not a single custom property. Whatever design
 * system they get, we have to hand them.
 *
 * They already have one. `apps/agents/agent-app-builder/instructions.md` and
 * `acb_skills/design.md` both tell agents to style with `--cc-bg`, `--cc-card`,
 * `--cc-primary` and friends, and the sandbox injects a `<style>` defining
 * them. The vocabulary was right; the **values were frozen** — hand-written
 * RapidTool `hsl()` literals that switched on light/dark and nothing else. So
 * every app ever built stayed RapidTool-blue while the shell around it turned
 * Fluent or Material, and the theming engine stopped at the iframe boundary.
 *
 * This module is that boundary, done properly: one function from a `Theme` to
 * the `--cc-*` block. Nothing else may write those variables.
 *
 * ## Why a second vocabulary at all
 *
 * It is fair to ask why the sandbox does not just receive `--primary`,
 * `--card`, `--muted-foreground` — the names the app itself uses. Because that
 * set is an **implementation detail we change**: it is Tailwind v4's
 * `@theme inline` surface, it carries 31 tokens including eight sidebar
 * overrides no app has any use for, and renaming one is a refactor inside this
 * repository. `--cc-*` is a **published interface** — it is written into agent
 * instructions, into `design.md`, and into every app already built and
 * published, none of which we can go back and edit. Fourteen stable names,
 * mapped here, is the seam that lets the inside keep moving.
 *
 * That is also why this file is the only place the mapping exists: a second
 * copy would drift, and the drift would only show up in somebody's published
 * app weeks later.
 */

import { THEME } from "./themes";
import type { Theme, ThemeMode } from "./types";

/**
 * `controls.buttonRadius` is `var(--radius)` — a reference to a variable that
 * exists in *our* document and not in the iframe's. Resolving it
 * here is the difference between a themed button and a square one, and the
 * failure is silent: an unresolvable `var()` makes the whole declaration
 * invalid at computed-value time, so the control just loses its radius.
 *
 * Only the handful of `--…` names a theme manifest actually references are
 * substitutable; anything else is passed through untouched, so an unexpected
 * value degrades to "as written" rather than to empty.
 */
function resolveVars(value: string, subs: Record<string, string>): string {
  return value.replace(
    /var\(\s*(--[a-z-]+)\s*(?:,\s*([^)]*))?\)/gi,
    (whole, name: string, fallback: string | undefined) =>
      subs[name] ?? (fallback !== undefined ? fallback.trim() : whole),
  );
}

/**
 * Drop `next/font` handles from a font stack before it crosses into the frame.
 *
 * Theme manifests write stacks like `var(--font-geist-sans), system-ui, …`.
 * That handle is registered by `next/font` on OUR `<html>`; inside an
 * opaque-origin iframe it is undefined, and an unresolvable `var()` with no
 * fallback invalidates the entire `font-family` declaration — so exporting the
 * stack verbatim gives the app *no* font rather than a themed one.
 *
 * Nor can we fix it by shipping the font: the frame's CSP is `font-src data:`,
 * and the `@font-face` rules live in a stylesheet the frame cannot reach. A
 * sandboxed app therefore gets the theme's **named and system** families —
 * Segoe UI for Fluent, Roboto for Material — and falls back gracefully where
 * one is not installed. That is the honest ceiling of this boundary, and it is
 * worth knowing when an app looks *nearly* right: the shapes, spacing and
 * colour are the theme's; a self-hosted webfont is the one thing that is not.
 */
function stripFontHandles(stack: string): string {
  const cleaned = stack
    .split(",")
    .map((part) => part.trim())
    .filter((part) => !/^var\(\s*--font-/i.test(part));
  // A stack that was ONLY handles would leave nothing; keep the generic so the
  // declaration stays valid.
  return cleaned.length > 0 ? cleaned.join(", ") : "sans-serif";
}

/**
 * The `--cc-*` token block for a theme and mode, as CSS declarations.
 *
 * Returned as pairs rather than a string so the same source feeds both the
 * `<style>` in the iframe's initial document and the live `postMessage` patch
 * that follows a theme change — two consumers that must never disagree about
 * what a token means.
 */
export function appTokens(theme: Theme, mode: ThemeMode): [string, string][] {
  const c = theme.colors[mode];
  const subs = { "--radius": theme.shape.radius };

  return [
    // ── Surfaces and text ────────────────────────────────────────────────
    ["--cc-bg", c.background],
    ["--cc-card", c.card],
    ["--cc-fg", c.foreground],
    // NOT `muted` — that is a muted *fill*, and an app writing
    // `color: var(--cc-muted)` on helper text wants the muted *ink*. The name
    // is a decade-old wart in the published contract; the mapping is what makes
    // it behave the way every app already assumes.
    ["--cc-muted", c.mutedForeground],
    ["--cc-border", c.border],
    ["--cc-secondary", c.secondary],

    // ── Semantic colours ─────────────────────────────────────────────────
    ["--cc-primary", c.primary],
    ["--cc-primary-fg", c.primaryForeground],
    ["--cc-accent", c.accent],
    ["--cc-success", c.success],
    ["--cc-warning", c.warning],
    // Ink for text sitting ON a success/warning fill. Previously hardcoded as
    // `hsl(20 14% 12%)` at two call sites in the sandbox stylesheet, which is
    // only legible over a *yellow* warning — a theme with a dark warning colour
    // rendered black-on-dark.
    ["--cc-success-fg", c.successForeground],
    ["--cc-warning-fg", c.warningForeground],
    ["--cc-danger", c.destructive],
    ["--cc-danger-fg", c.destructiveForeground],

    // ── Typography ───────────────────────────────────────────────────────
    // `--cc-font`/`--cc-mono` were REFERENCED by the sandbox stylesheet and by
    // design.md and never defined. An undefined `var()` with no fallback
    // invalidates the declaration, so `.cc-chart` axis labels silently
    // inherited the body font instead of rendering monospace.
    ["--cc-font", stripFontHandles(theme.typography.app)],
    ["--cc-mono", stripFontHandles(theme.typography.mono)],
    ["--cc-heading-weight", theme.typography.headingWeight],
    ["--cc-heading-tracking", theme.typography.headingLetterSpacing],

    // ── Shape, motion, elevation ─────────────────────────────────────────
    ["--cc-radius", theme.shape.radius],
    ["--cc-border-width", theme.shape.borderWidth],
    ["--cc-shadow", theme.effects.shadow],
    ["--cc-duration", theme.effects.motionDuration],
    ["--cc-ease", theme.effects.motionEasing],

    // ── Control personality ──────────────────────────────────────────────
    // The reason a Material app looks Material rather than "RapidTool with
    // Material's colours": pill buttons, uppercase labels, a state layer on
    // hover, Fluent's outline on solid buttons. Handing these across means an
    // app built a year ago picks up a new theme's *behaviour*, not just its
    // palette — which is the whole promise of the engine.
    ["--cc-button-radius", resolveVars(theme.controls.buttonRadius, subs)],
    ["--cc-control-filled-border", theme.controls.filledBorderWidth],
    ["--cc-control-state-layer", theme.controls.stateLayerOpacity],
    ["--cc-control-focus-ring", theme.controls.focusRingWidth],
    ["--cc-control-label-tracking", theme.controls.labelTracking],
    ["--cc-control-label-transform", theme.controls.labelTransform],
  ];
}

/**
 * The token block as a CSS declaration list (no selector), ready to drop into
 * a `:root { … }` rule.
 */
export function appTokenCss(theme: Theme, mode: ThemeMode, indent = "    "): string {
  return appTokens(theme, mode)
    .map(([k, v]) => `${indent}${k}: ${v};`)
    .join("\n");
}

/**
 * The token block as a plain object, for the `postMessage` patch path.
 *
 * A theme change must not rebuild the iframe's `srcDoc`: that remounts the
 * document and a running app loses whatever the person had typed into it. The
 * frame instead receives these pairs and writes them onto its own `:root`.
 */
export function appTokenMap(theme: Theme, mode: ThemeMode): Record<string, string> {
  return Object.fromEntries(appTokens(theme, mode));
}

/**
 * Every `--cc-*` name this contract defines.
 *
 * Exported so the conformance test can assert that the documentation agents
 * read lists exactly these — a token that exists but is undocumented is one no
 * app will ever use, and a documented token that does not exist is one an app
 * will use and silently lose.
 */
export const CC_TOKEN_NAMES: readonly string[] = appTokens(
  // Derived from the real manifest rather than a hand-kept list, so there is
  // no second copy to fall out of date.
  THEME,
  "dark",
).map(([name]) => name);
