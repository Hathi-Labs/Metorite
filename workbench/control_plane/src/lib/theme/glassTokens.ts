/**
 * The glass look's `--cc-*` values, for the sandboxed frames (GL-10a, D143,
 * `glass_look.md` GL-4).
 *
 * A sandboxed card (`SandboxedHtml`, `SandboxedReact`, a published Custom
 * App) runs in an opaque-origin iframe. It inherits no stylesheet, no
 * `data-look` and no custom property, so the host hands it a `--cc-*` block
 * (`app-tokens.ts`). Under `<html data-look="glass">` the host hands it THIS
 * block in place of the classic one.
 *
 * ⚠️ The values mirror the glass blocks of `src/app/globals.css`. They are
 * literals, because the frame has no `var()` to resolve them against.
 * `glassTokens.test.ts` parses `globals.css` and fails when one drifts, as
 * `themes.test.ts` does for classic. Change the CSS first, then this file.
 *
 * ⚠️ The `--cc-*` NAMES do not change. They are a published interface
 * (`app-tokens.test.ts`). Only the values differ. A token this file does not
 * list keeps its classic value in glass too (`--cc-border-width` and the
 * control personality tokens).
 */
import { GLASS_LOOK, LOOK_ATTRIBUTE, type Look } from "./look";
import type { ThemeMode } from "./types";

/** Which globals.css custom property each glass `--cc-*` value mirrors. */
export const GLASS_CC_SOURCE: Readonly<Record<string, string>> = {
  "--cc-bg": "--background",
  "--cc-card": "--card",
  "--cc-fg": "--foreground",
  "--cc-muted": "--muted-foreground",
  "--cc-border": "--border",
  "--cc-secondary": "--secondary",
  "--cc-primary": "--primary",
  "--cc-primary-fg": "--primary-foreground",
  // `--accent` IS the tint in glass (`--accent: var(--primary)`).
  "--cc-accent": "--primary",
  "--cc-success": "--success",
  "--cc-warning": "--warning",
  "--cc-success-fg": "--success-foreground",
  "--cc-warning-fg": "--warning-foreground",
  "--cc-danger": "--destructive",
  "--cc-danger-fg": "--destructive-foreground",
  "--cc-shadow": "--card-shadow",
};

/** The glass values that differ by colour mode. */
export const GLASS_CC_COLOURS: Readonly<Record<ThemeMode, Readonly<Record<string, string>>>> = {
  dark: {
    "--cc-bg": "#000000",
    "--cc-card": "#1c1c1e",
    "--cc-fg": "#f5f5f7",
    "--cc-muted": "#98989d",
    "--cc-border": "rgb(84 84 88 / 0.5)",
    "--cc-secondary": "rgb(118 118 128 / 0.24)",
    "--cc-primary": "#0a84ff",
    "--cc-primary-fg": "#000000",
    "--cc-accent": "#0a84ff",
    "--cc-success": "#30d158",
    "--cc-warning": "#ffb340",
    "--cc-success-fg": "#000000",
    "--cc-warning-fg": "#000000",
    "--cc-danger": "#ff7b72",
    "--cc-danger-fg": "#000000",
    "--cc-shadow": "0 0 0 0.5px rgb(255 255 255 / 0.06)",
  },
  light: {
    "--cc-bg": "#f2f2f7",
    "--cc-card": "#ffffff",
    "--cc-fg": "#1d1d1f",
    "--cc-muted": "#6e6e73",
    "--cc-border": "rgb(60 60 67 / 0.13)",
    "--cc-secondary": "rgb(120 120 128 / 0.12)",
    "--cc-primary": "#0068d6",
    "--cc-primary-fg": "#ffffff",
    "--cc-accent": "#0068d6",
    "--cc-success": "#146c2b",
    "--cc-warning": "#b02e00",
    "--cc-success-fg": "#ffffff",
    "--cc-warning-fg": "#ffffff",
    "--cc-danger": "#c0001a",
    "--cc-danger-fg": "#ffffff",
    "--cc-shadow": "0 0 0 0.5px rgb(0 0 0 / 0.07), 0 1px 2px rgb(0 0 0 / 0.04), 0 4px 14px rgb(0 0 0 / 0.035)",
  },
};

/**
 * Which globals.css property each shared glass value mirrors. The font stacks
 * carry no `next/font` handle: the frame cannot resolve one
 * (`stripFontHandles` in `app-tokens.ts`), so the mirror drops that entry.
 */
export const GLASS_CC_SHARED_SOURCE: Readonly<Record<string, string>> = {
  "--cc-font": "--font-app",
  "--cc-mono": "--font-app-mono",
  "--cc-heading-tracking": "--heading-tracking",
  // The 18 px step: the frame sits inside a 22 px card with a 4 px inset.
  "--cc-radius": "--glass-radius-xl",
  // A control's corner is the concentric row corner, 14 px.
  "--cc-button-radius": "--glass-radius-row",
  "--cc-duration": "--motion-duration",
  "--cc-ease": "--motion-easing",
};

/** The glass values that are the same in both colour modes. */
export const GLASS_CC_SHARED: Readonly<Record<string, string>> = {
  "--cc-font": '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable Text", system-ui, sans-serif',
  "--cc-mono": 'ui-monospace, "SF Mono", SFMono-Regular, Menlo, "Cascadia Mono", Consolas, monospace',
  "--cc-heading-tracking": "-0.022em",
  "--cc-radius": "1.125rem",
  "--cc-button-radius": "0.875rem",
  "--cc-duration": "0.28s",
  "--cc-ease": "cubic-bezier(0.32, 0.72, 0, 1)",
};

/** The glass override of one `--cc-*` name, or undefined to keep classic. */
export function glassToken(name: string, mode: ThemeMode): string | undefined {
  return GLASS_CC_COLOURS[mode][name] ?? GLASS_CC_SHARED[name];
}

/** The look the document carries now: glass under `data-look="glass"`, else classic. */
export function documentLook(): Look {
  if (typeof document === "undefined") return "classic";
  return document.documentElement.getAttribute(LOOK_ATTRIBUTE) === GLASS_LOOK ? "glass" : "classic";
}

/** Subscribe to a change of the look (a member's Settings → Appearance → Design). */
export function subscribeLook(onChange: () => void): () => void {
  if (typeof MutationObserver === "undefined" || typeof document === "undefined") return () => {};
  const mo = new MutationObserver(onChange);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: [LOOK_ATTRIBUTE] });
  return () => mo.disconnect();
}
