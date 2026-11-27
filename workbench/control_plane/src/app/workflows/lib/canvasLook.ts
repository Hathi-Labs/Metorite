/**
 * The look of the workflow canvas (task 3340, row E7b of the AI Studio spec).
 *
 * xyflow adds a `light` class to its own wrapper in light mode. That class
 * matches the `.light {` block of `src/app/globals.css` a second time, inside
 * the canvas. The block then re-declares each colour token there, so the
 * member's accent and the glass tokens, which sit on `<html>`, stopped at the
 * canvas edge. An inline style beats that class rule. So each token the block
 * declares is set to `inherit` here, and the value flows through from `<html>`.
 *
 * xyflow also paints with its own hex colours. Each `--xy-*` variable below
 * points at a token of the one look. This file holds no colour literal.
 * `canvasLook.test.ts` is the fence: it reads `globals.css` and xyflow's own
 * style sheet as text.
 */
import type { CSSProperties } from "react";

/** One entry for each custom property of the `.light {` block of globals.css. */
const TOKEN_FLOW_THROUGH = {
  "--background": "inherit",
  "--foreground": "inherit",
  "--card": "inherit",
  "--card-foreground": "inherit",
  "--popover": "inherit",
  "--popover-foreground": "inherit",
  "--tooltip": "inherit",
  "--tooltip-foreground": "inherit",
  "--primary": "inherit",
  "--primary-foreground": "inherit",
  "--secondary": "inherit",
  "--secondary-foreground": "inherit",
  "--muted": "inherit",
  "--muted-foreground": "inherit",
  "--accent": "inherit",
  "--accent-foreground": "inherit",
  "--destructive": "inherit",
  "--destructive-foreground": "inherit",
  "--border": "inherit",
  "--input": "inherit",
  "--ring": "inherit",
  "--success": "inherit",
  "--success-foreground": "inherit",
  "--warning": "inherit",
  "--warning-foreground": "inherit",
  "--info": "inherit",
  "--info-foreground": "inherit",
  "--violet": "inherit",
  "--violet-foreground": "inherit",
  "--hatch-stripe": "inherit",
  "--cat-1": "inherit",
  "--cat-2": "inherit",
  "--cat-3": "inherit",
  "--cat-4": "inherit",
  "--cat-5": "inherit",
  "--cat-6": "inherit",
  "--cat-7": "inherit",
  "--cat-8": "inherit",
  "--cat-9": "inherit",
  "--cat-10": "inherit",
  "--cat-11": "inherit",
  "--cat-12": "inherit",
  "--sidebar-background": "inherit",
  "--sidebar-foreground": "inherit",
  "--sidebar-primary": "inherit",
  "--sidebar-primary-foreground": "inherit",
  "--sidebar-accent": "inherit",
  "--sidebar-accent-foreground": "inherit",
  "--sidebar-border": "inherit",
  "--sidebar-ring": "inherit",
} as const;

/** xyflow variables, each mapped to a token. */
const XYFLOW_VARIABLES = {
  "--xy-background-color": "transparent",
  "--xy-background-pattern-color": "var(--muted-foreground)",
  "--xy-edge-stroke": "var(--muted-foreground)",
  "--xy-edge-stroke-selected": "var(--primary)",
  "--xy-connectionline-stroke": "var(--primary)",
  "--xy-handle-background-color": "var(--muted-foreground)",
  "--xy-handle-border-color": "var(--card)",
  "--xy-selection-background-color": "color-mix(in srgb, var(--primary) 10%, transparent)",
  "--xy-selection-border": "1px dotted var(--primary)",
  "--xy-controls-button-background-color": "var(--card)",
  "--xy-controls-button-background-color-hover": "var(--muted)",
  "--xy-controls-button-color": "var(--foreground)",
  "--xy-controls-button-color-hover": "var(--foreground)",
  "--xy-controls-button-border-color": "var(--border)",
  "--xy-controls-box-shadow": "none",
  "--xy-edge-label-background-color": "var(--card)",
  "--xy-edge-label-color": "var(--foreground)",
  "--xy-attribution-background-color": "transparent",
  "--xy-minimap-background-color": "var(--card)",
  "--xy-minimap-mask-background-color": "color-mix(in srgb, var(--background) 60%, transparent)",
  "--xy-minimap-node-background-color": "var(--muted)",
} as const;

export const CANVAS_STYLE = {
  ...TOKEN_FLOW_THROUGH,
  ...XYFLOW_VARIABLES,
} as CSSProperties;
