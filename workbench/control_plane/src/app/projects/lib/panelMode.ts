/**
 * Projects · how much room the task panel takes, and how it gives focus back
 * (WS-27ab item 1, from Plane research item P-15).
 *
 * **Side → full is one axis with two stops, not two panels.** The same
 * `TaskPanel` renders at both; only its width class and where the page mounts
 * it change. A second "expanded task" component would be a second detail
 * surface to keep in step, which is the drift §11.22 spent a slice undoing on
 * this very file.
 *
 * ⚠️ It was THREE stops until 2026-09-23, and the owner cut `peek`. See
 * `PANEL_MODES` for the ask and the reason.
 *
 * The choice persists per user because it is a *reading* preference, not a
 * property of the task: somebody who wants a wide panel wants it for the next
 * task too. `localStorage` is the house idiom for that (`ViewModeProvider`'s
 * desktop toggle, `Sidebar`'s folded sections) — a per-user server preference
 * would be a new table and a new endpoint for a value that has no meaning on
 * another device.
 *
 * Everything here is pure so the rules that are easy to get subtly wrong —
 * escalation stopping at the ends rather than wrapping, a corrupt stored value
 * degrading to the default rather than to a blank panel, and which Escape
 * closes the panel versus leaves a half-typed comment alone — are assertions
 * in `panelMode.test.ts` rather than clicks.
 */

/**
 * The two stops, narrowest first. Order is the escalation order.
 *
 * ⚠️ **This was `peek → side → full` until 2026-09-23. The owner cut it to
 * two.** In their words: *"Keep it the same as my tasks, where we have a
 * sidebar which can also open as a full card. The switcher where we change
 * the width of the sidebar is not needed."*
 *
 * So this is no longer a WIDTH SWITCHER with three stops. It is the affordance
 * `/tasks` already has — a docked panel, and a full card over the page —
 * reached by ONE expand toggle. `ItemDetail` calls the second stop `focused`,
 * and `taskStore` keeps `selectedItemId` for the first and `focusedItemId` for
 * the second. Two surfaces, one idea.
 *
 * `peek` (320px) went because it could not pay for itself: too narrow for the
 * paired fields, and a third button on a header `/tasks` answers with none.
 *
 * ⚠️ A member who last chose `peek` still has it in `localStorage`.
 * `isPanelMode` rejects that string now, so `readPanelMode` falls back to the
 * default — the retired stop degrades to the docked panel, never to a blank
 * one. `panelMode.test.ts` asserts that by name.
 */
export const PANEL_MODES = ["side", "full"] as const;

export type PanelMode = (typeof PANEL_MODES)[number];

/** What a panel opens at when nobody has chosen — today's shipped behaviour. */
export const DEFAULT_PANEL_MODE: PanelMode = "side";

export function isPanelMode(value: unknown): value is PanelMode {
  return (PANEL_MODES as readonly unknown[]).includes(value);
}

/**
 * One step wider, and one step narrower.
 *
 * **Neither wraps.** A cycling control would turn "wider" into "suddenly tiny"
 * on the third press, which is the behaviour that makes a keyboard shortcut
 * unusable without looking at the screen. At the end of the axis the step is a
 * no-op, and the button that issues it renders disabled.
 */
export function widerPanel(mode: PanelMode): PanelMode {
  const at = PANEL_MODES.indexOf(mode);
  return PANEL_MODES[Math.min(at + 1, PANEL_MODES.length - 1)];
}

export function narrowerPanel(mode: PanelMode): PanelMode {
  const at = PANEL_MODES.indexOf(mode);
  return PANEL_MODES[Math.max(at - 1, 0)];
}

/** The label and glyph each stop wears on the panel's own switch. */
export const PANEL_MODE_LABELS: Record<PanelMode, string> = {
  side: "Side panel",
  full: "Full card",
};

/**
 * Both are `icon-registry.ts` entries. An unmapped name falls back to Lucide
 * on every theme, which beside a mapped glyph reads as a bug.
 *
 * These are the glyphs of the TARGET stop, because the header now draws one
 * toggle rather than one button per stop: docked shows `Maximize2` ("open it
 * as a full card"), and full shows `Minimize2` ("put it back").
 */
export const PANEL_MODE_ICONS: Record<PanelMode, string> = {
  side: "PanelRight",
  full: "Maximize2",
};

export const PANEL_MODE_HINTS: Record<PanelMode, string> = {
  side: "Open as a full card",
  full: "Back to the side panel",
};

/**
 * The width the panel's `<aside>` takes at each stop.
 *
 * A `max-w-*` rather than a `w-*` because the panel is `w-full` inside
 * whatever the page docks it in, and because the phone branch lifts the cap
 * with `[&>aside]:max-w-none` — a fixed width would survive that override and
 * leave a 448px panel on a 390px screen.
 *
 * `full` is `max-w-3xl` and not the whole viewport: that is the reading width
 * `/tasks`' maximise-out-of-the-pane already uses (`TaskFocusModal`), and a
 * one-column panel stretched across a 4K monitor is a line of text nobody can
 * track back to its start.
 */
export const PANEL_WIDTH_CLASS: Record<PanelMode, string> = {
  side: "max-w-md",
  full: "max-w-3xl",
};

/** Where the page mounts the panel: docked in the row, or over the board. */
export function isOverlayMode(mode: PanelMode): boolean {
  return mode === "full";
}

export const PANEL_MODE_STORAGE_KEY = "cc-projects-panel-mode";

/** The slice of `Storage` this needs — so a test can hand it a plain object. */
export interface PanelModeStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function browserStore(): PanelModeStore | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    // Storage can throw outright (Safari private mode, a blocked third-party
    // frame). The panel still works; it just forgets.
    return null;
  }
}

/**
 * The stored choice, or the default.
 *
 * Anything that is not one of the two stops reads as the default rather than
 * as an error: a value written by a newer client, hand-edited in devtools, or
 * the retired `peek`, must not leave the panel with no width class at all.
 */
export function readPanelMode(store?: PanelModeStore | null): PanelMode {
  const target = store === undefined ? browserStore() : store;
  if (!target) return DEFAULT_PANEL_MODE;
  try {
    const raw = target.getItem(PANEL_MODE_STORAGE_KEY);
    return isPanelMode(raw) ? raw : DEFAULT_PANEL_MODE;
  } catch {
    return DEFAULT_PANEL_MODE;
  }
}

export function writePanelMode(
  mode: PanelMode,
  store?: PanelModeStore | null,
): void {
  const target = store === undefined ? browserStore() : store;
  if (!target) return;
  try {
    target.setItem(PANEL_MODE_STORAGE_KEY, mode);
  } catch {
    /* a preference that cannot be saved is not a failure worth surfacing */
  }
}

/** What Escape means inside the panel. */
export type PanelEscape = "close" | "blur" | null;

/**
 * Escape inside the panel: leave the field, or close the panel.
 *
 * **A half-written comment must survive the first Escape.** The panel's
 * composer is the one control on the surface holding text nothing else has a
 * copy of, so Escape from inside a field with content blurs it — the panel
 * closes on the *second* press, from the panel itself. An empty field has
 * nothing to lose, so it closes straight away and Escape stays one key for the
 * common case.
 *
 * A modifier held means the keystroke belongs to the browser or the OS
 * (`Alt+Escape`, `Ctrl+Escape` are window-manager keys), so it is not ours.
 */
export function panelEscape(
  event: {
    key: string;
    metaKey?: boolean;
    ctrlKey?: boolean;
    altKey?: boolean;
    shiftKey?: boolean;
  },
  target?: { tagName?: string; value?: string; isContentEditable?: boolean } | null,
): PanelEscape {
  if (event.key !== "Escape") return null;
  if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return null;
  const tag = (target?.tagName ?? "").toLowerCase();
  const editable =
    target?.isContentEditable === true || tag === "input" || tag === "textarea";
  if (editable && (target?.value ?? "").trim().length > 0) return "blur";
  return "close";
}
