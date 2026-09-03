/**
 * Theming engine — manifest types.
 *
 * A theme is DATA, not code. Every visual decision the Control Plane makes is
 * a design token, and a theme is a bundle of token values plus a choice of
 * icon pack and font stacks. Swapping themes therefore restyles the whole app
 * without touching a single component.
 *
 * Two axes, deliberately independent:
 *   • style — "rapidtool" | "fluent" | "material" | …  (the theme identity)
 *   • mode  — "dark" | "light"                          (per-theme variant)
 *
 * The style axis lives on `<html data-theme="…">`; the mode axis stays on the
 * `.light` / `.dark` class managed by next-themes. Every theme MUST supply
 * both modes, so the two axes never interfere.
 */

/**
 * The categorical ramp — `--cat-1` … `--cat-8`.
 *
 * Semantic tokens answer "what does this MEAN" (success, warning, primary).
 * A categorical hue answers a different question: "which ONE of these is it",
 * for a set whose members have no ranking and no meaning — @contexts, tags,
 * custom-field options, chart series. There are about five semantic tones, so
 * tokenising categories onto them makes eight @contexts share four colours and
 * stop being distinguishable, which is the whole job.
 *
 * Hence a ramp: eight hues chosen to be mutually distinguishable at chip size,
 * each theme picking values coherent with its own identity, both modes
 * supplied. A surface that uses them tracks the theme like everything else —
 * which `bg-sky-500` never did.
 *
 * Rules for a theme author:
 *   • all eight slots, both modes, always (a missing one is a compile error,
 *     and `themes.test.ts` says so at runtime too);
 *   • each must clear WCAG AA against that theme's `card` AND `background`,
 *     because these render as text, not just as dots — `contrast.test.ts`;
 *   • slot order is meaningless to a reader but STABLE for callers: nothing
 *     may be reordered, because `contextColors.ts` hashes a name to a slot and
 *     reordering would repaint every existing @context.
 */
export type CategoricalToken =
  | "cat-1"
  | "cat-2"
  | "cat-3"
  | "cat-4"
  | "cat-5"
  | "cat-6"
  | "cat-7"
  | "cat-8"
  | "cat-9"
  | "cat-10"
  | "cat-11"
  | "cat-12";

/**
 * The ramp's slots, in order. The length IS the number of slots.
 *
 * Slots 9–12 were added 2026-08-31 (owner ask: more colour choices for a
 * space's marker). They are CHOICE-ONLY: `hashSlot` still takes its modulus
 * over the first eight (`HASH_SLOTS` in `src/lib/categorical.ts`), because
 * widening the modulus would silently repaint every hash-assigned @context
 * and tag — the exact repaint the "never reorder" rule exists to prevent.
 */
export const CATEGORICAL_TOKENS = [
  "cat-1",
  "cat-2",
  "cat-3",
  "cat-4",
  "cat-5",
  "cat-6",
  "cat-7",
  "cat-8",
  "cat-9",
  "cat-10",
  "cat-11",
  "cat-12",
] as const satisfies readonly CategoricalToken[];

/**
 * Semantic colour tokens. Names mirror the shadcn/ui convention already used
 * across the app, which keeps us compatible with the wider ecosystem of theme
 * generators (tweakcn and friends export exactly this vocabulary).
 *
 * Values are raw CSS colours — `hsl(…)`, `oklch(…)` or hex all work, because
 * Tailwind v4 consumes them through `var()` without parsing.
 *
 * The categorical ramp is intersected in rather than listed here: it is not
 * semantic, it is ordinal, and keeping it a separate named set is what lets a
 * caller iterate the slots without also iterating `--background`.
 */
export type ColorTokens = Record<CategoricalToken, string> & {
  background: string;
  foreground: string;
  card: string;
  cardForeground: string;
  popover: string;
  popoverForeground: string;
  primary: string;
  primaryForeground: string;
  secondary: string;
  secondaryForeground: string;
  muted: string;
  mutedForeground: string;
  accent: string;
  accentForeground: string;
  destructive: string;
  destructiveForeground: string;
  border: string;
  input: string;
  ring: string;
  success: string;
  successForeground: string;
  warning: string;
  warningForeground: string;

  /** The blue a STATUS means, which is not the accent a member picks. */
  info: string;
  infoForeground: string;
  /** The fifth lane hue. It was `--primary` at 60%, so it was not a hue. */
  violet: string;
  violetForeground: string;

  /**
   * Sidebar surface overrides. Optional — when a theme omits one it falls back
   * to the matching core token, so a theme only spells out what it wants to
   * differ from the main surface.
   */
  sidebarBackground?: string;
  sidebarForeground?: string;
  sidebarPrimary?: string;
  sidebarPrimaryForeground?: string;
  sidebarAccent?: string;
  sidebarAccentForeground?: string;
  sidebarBorder?: string;
  sidebarRing?: string;
};

/** Which colour tokens every theme must define (used by validation + tests). */
export const REQUIRED_COLOR_TOKENS = [
  "background",
  "foreground",
  "card",
  "cardForeground",
  "popover",
  "popoverForeground",
  "primary",
  "primaryForeground",
  "secondary",
  "secondaryForeground",
  "muted",
  "mutedForeground",
  "accent",
  "accentForeground",
  "destructive",
  "destructiveForeground",
  "border",
  "input",
  "ring",
  "success",
  "successForeground",
  "warning",
  "warningForeground",
  ...CATEGORICAL_TOKENS,
] as const satisfies readonly (keyof ColorTokens)[];

/**
 * Shape tokens — the geometry half of a theme's personality. Fluent is nearly
 * square (2–4px), Material is heavily rounded (16–20px), our default sits in
 * between at 12px.
 */
export type ShapeTokens = {
  /** Base corner radius; drives `rounded-sm/md/lg` through `@theme inline`. */
  radius: string;
  /** Default border width for cards, inputs and outline buttons. */
  borderWidth: string;
};

/**
 * Typography tokens. Font *stacks* rather than family names, so a theme can
 * prefer a platform font it can't bundle (Segoe UI on Windows) and fall back
 * to a self-hosted one. The `var(--font-*)` handles are registered by
 * `next/font` in the root layout.
 */
export type TypographyTokens = {
  /** UI font stack → `--font-app`, consumed by Tailwind's `font-sans`. */
  app: string;
  /** Monospace stack → `--font-mono`, for code and metrics. */
  mono: string;
  /** Optional display stack for headings; defaults to `app`. */
  display?: string;
  /** Tracking applied to headings — Material runs tight, Fluent neutral. */
  headingLetterSpacing: string;
  /** Font weight used for headings and emphasised labels. */
  headingWeight: string;
  /** Font weight used for buttons and interactive labels. */
  labelWeight: string;
};

/**
 * Effect tokens — the "material" half of the personality: how surfaces blur,
 * whether things glow, how motion feels. A flat design system (Material)
 * switches glass and glow off and expresses depth with shadow instead.
 */
export type EffectTokens = {
  /** Backdrop blur radius for `.tech-glass`; `0px` disables the effect. */
  glassBlur: string;
  /** Opacity of glass surfaces — 1 renders them fully opaque (flat themes). */
  glassOpacity: string;
  /**
   * Opacity for glass that sits over page content — modals, drawers, popovers.
   * Always at or above `glassOpacity`: these surfaces carry text that has to
   * stay readable against whatever happens to be behind them.
   */
  glassOpacityStrong: string;
  /** Strength multiplier for `.tech-glow`; `0` switches glowing off. */
  glowStrength: string;
  /** Elevation shadow used by cards and popovers. */
  shadow: string;
  /** Duration for `.tech-transition`. */
  motionDuration: string;
  /** Easing curve for `.tech-transition`. */
  motionEasing: string;
};

/**
 * Control tokens — how buttons and inputs behave, as opposed to what colour
 * they are. This is where a design system's personality actually lives: every
 * theme so far could change a button's palette, radius and font, but a button
 * still *behaved* identically everywhere. Material 3 buttons are full pills
 * with a translucent state layer on hover; Fluent outlines even its solid
 * buttons and keeps a tight focus ring; Metro-descended looks upper-case their
 * labels. None of that is expressible as a colour.
 *
 * These are consumed by the shared primitives in `src/components/ui/`, which
 * is why those primitives exist — there is no way to apply a state layer from
 * a Tailwind class string repeated across 1,100 call sites.
 */
export type ControlTokens = {
  /** Corner radius for buttons. Material 3 renders them as full pills. */
  buttonRadius: string;
  /** Border width on FILLED buttons — Fluent outlines even solid controls. */
  filledBorderWidth: string;
  /**
   * Opacity of the hover/press state layer. Material overlays the foreground
   * colour at 8%; `0` means the control uses a plain opacity shift instead.
   */
  stateLayerOpacity: string;
  /** Focus ring width. */
  focusRingWidth: string;
  /** Letter-spacing on control labels. */
  labelTracking: string;
  /** `none` or `uppercase` — the Metro/industrial signature. */
  labelTransform: string;
};

/**
 * Themes for the third-party surfaces that carry their own theme vocabulary.
 *
 * Monaco and Shiki cannot be driven by our CSS tokens — each ships a closed
 * set of named themes. Before this, both branched on dark/light only, so a
 * code block looked identical on Fluent and Material while everything around
 * it changed. Naming the equivalent per theme is what makes a code view part
 * of the theme rather than an island in it.
 */
export type SurfaceTokens = {
  /** Monaco editor theme id. Only its four built-ins are valid. */
  monaco: { dark: string; light: string };
  /** Shiki highlighting theme; must be a name Shiki bundles. */
  shiki: { dark: string; light: string };
};

/**
 * The theme definition.
 *
 * ⚠️ Singular since 2026-08-31. `IconPackId` and the `iconPack` field went
 * with the engine — `<Icon>` draws Lucide, full stop. `id`, `name` and
 * `description` are kept because the manifest still identifies itself in the
 * sandbox bridge and in test output, not because anything selects on them.
 */
export type Theme = {
  /** Stable identifier. No longer a `data-theme` value — nothing selects it. */
  id: string;
  /** Human-readable name. */
  name: string;
  /** One-line description of the look. */
  description: string;
  /** Attribution, where the look derives from a public design system. */
  inspiration?: string;
  typography: TypographyTokens;
  shape: ShapeTokens;
  effects: EffectTokens;
  controls: ControlTokens;
  surfaces: SurfaceTokens;
  colors: {
    dark: ColorTokens;
    light: ColorTokens;
  };
};

/** Colour mode — the one axis of the look that is still switchable. */
export type ThemeMode = "dark" | "light";

/** UI density — a user preference, not a theme property. */
export type Density = "compact" | "default" | "comfortable";

/** Scale factor applied to the root font size for each density setting. */
export const DENSITY_SCALE: Record<Density, number> = {
  compact: 0.92,
  default: 1,
  comfortable: 1.08,
};

/**
 * The user's resolved appearance preferences. Persisted per user, with an
 * org-wide default supplied by the backend so a company can standardise on
 * one look while individuals opt out.
 */
export type AppearancePreferences = {
  /** Colour mode; `null` means "inherit the organisation default". */
  mode: ThemeMode | null;
  /** UI density; `null` means "inherit the organisation default". */
  density: Density | null;
  /**
   * Optional primary-colour override applied on top of the one theme.
   * Any CSS colour; `null` keeps the app's own primary.
   */
  accent: string | null;
};

/** Appearance settings as stored/served by the backend. */
export type AppearanceSettings = {
  /** Organisation-wide default, set by admins. */
  org: {
    mode: ThemeMode;
    density: Density;
    /** When false, members may not deviate from the org default. */
    allowUserOverride: boolean;
  };
  /** The signed-in member's personal overrides. */
  user: AppearancePreferences;
  /**
   * False when the gateway has no appearance store, so `org` is the built-in
   * default rather than something an admin chose. Settings uses this to
   * explain why the org controls are unavailable instead of silently
   * accepting edits that cannot be saved.
   */
  orgManaged: boolean;
  /**
   * Who last changed the org default, and when. An org-wide setting changes
   * everyone's UI at once, so "who did this" is the first question asked when
   * the company's app suddenly looks different.
   */
  updatedBy?: string;
  updatedAt?: string;
};
