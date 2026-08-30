/**
 * Projects · the palette's action registry (WS-27ab item 3, from Plane
 * research item P-16).
 *
 * **A declared registry, not a chain of `if` branches inside the palette.**
 * Every action the palette can run, every key sequence that runs one without
 * the palette, and every row the shortcuts sheet prints come from THIS list.
 * That is the whole point: a help sheet written by hand is a help sheet that
 * is wrong within two releases, and a shortcut implemented inside a keydown
 * handler is a shortcut nothing can enumerate.
 *
 * Three consumers, one source:
 *   • `SearchPalette` — lists the applicable commands beside the task hits.
 *   • `projects/page.tsx` — routes `g`/`v` key sequences through
 *     `stepSequence`, and supplies the `CommandActions` the `run`s call.
 *   • `ShortcutsSheet` — printed from `shortcutSections`, so `?` cannot drift
 *     from what the keyboard actually does.
 *
 * **The Go section is derived from `@/lib/nav`, not written out here.** A route
 * list copied into a second file is a route list that outlives the route: the
 * label a sequence advertises is the label the sidebar shows, because it is
 * literally the same `NavPane`. `GO_KEYS` assigns the letters and nothing
 * else; `commands.test.ts` fails if one of them names a pane that is gone.
 *
 * Everything here is pure — no React, no DOM — so the rules that are wrong-but-
 * plausible (a prefix that is also a whole sequence, a stale pending prefix
 * after a key that starts nothing, a command offered while the thing it acts
 * on is absent) are assertions rather than clicks.
 */

import { PANES } from "@/lib/nav";

import { type PanelMode, narrowerPanel, widerPanel } from "./panelMode";
import type { Hit } from "./search";

/**
 * The five view modes. Declared HERE rather than in `page.tsx` so the mode
 * switch and the palette read one vocabulary — a second list is how the
 * toolbar and the palette come to offer different sets.
 *
 * Icon names are `icon-registry.ts` entries: an unmapped name falls back to
 * Lucide on every theme, which is the one glyph in a row of Material Symbols
 * that reads as a bug.
 */
export const VIEW_MODES = [
  { id: "board", icon: "Kanban", key: "b" },
  { id: "list", icon: "List", key: "l" },
  { id: "table", icon: "Table", key: "t" },
  { id: "calendar", icon: "Calendar", key: "c" },
  { id: "timeline", icon: "Milestone", key: "m" },
] as const;

export type ViewMode = (typeof VIEW_MODES)[number]["id"];

/** Where the app is, so a command can refuse to be offered when it would no-op. */
export interface CommandContext {
  mode: ViewMode;
  /** A project is selected — most of the Project section needs one. */
  hasProject: boolean;
  /** That project is a root (department): the lifecycle policy's own rule. */
  isRoot: boolean;
  /** Something is filtering, so "clear filters" would do something. */
  filtered: boolean;
  /** A task panel is open, so the panel-width commands mean something. */
  panelOpen: boolean;
  panelMode: PanelMode;
  /** Desktop only — a phone reaches the tree through the drawer. */
  canToggleRail: boolean;
}

/** What a command is allowed to do. The page supplies every one of these. */
export interface CommandActions {
  navigate(href: string): void;
  setMode(mode: ViewMode): void;
  setPanelMode(mode: PanelMode): void;
  clearFilters(): void;
  toggleRail(): void;
  manage(what: "fields" | "tags" | "lifecycle"): void;
  showShortcuts(): void;
}

/** The sections, in the order the palette and the sheet print them. */
export const COMMAND_SECTIONS = ["Go", "View", "Panel", "Project", "Help"] as const;

export type CommandSection = (typeof COMMAND_SECTIONS)[number];

export interface Command {
  id: string;
  label: string;
  section: CommandSection;
  /** Extra words the palette matches on — what somebody would type instead. */
  keywords: readonly string[];
  /** Lucide icon name; the theme picks the pack. */
  icon: string;
  /** The key sequence that runs it without opening the palette, if any. */
  sequence?: readonly string[];
  /** Set only on commands that navigate; pinned to `nav.PANES` by the tests. */
  href?: string;
  /** Offered only when this holds. Absent = always. */
  when?(ctx: CommandContext): boolean;
  run(actions: CommandActions, ctx: CommandContext): void;
}

/**
 * `g <letter>` → a pane. The letters, and nothing else: the label, the icon
 * and the route itself come from the shared nav config.
 */
const GO_KEYS: ReadonlyArray<{ href: string; key: string }> = [
  { href: "/projects", key: "p" },
  { href: "/tasks", key: "t" },
  { href: "/notes", key: "n" },
  { href: "/email", key: "e" },
  { href: "/dashboard", key: "d" },
  { href: "/crm", key: "c" },
  { href: "/workflows", key: "w" },
  { href: "/memory", key: "m" },
];

/** The Go section, built from the panes that actually exist. */
function goCommands(): Command[] {
  const out: Command[] = [];
  for (const { href, key } of GO_KEYS) {
    const pane = PANES.find((p) => p.href === href);
    // A pane that has been removed from the nav produces NO command rather
    // than a shortcut into a 404. `commands.test.ts` turns that into a
    // failure, so this branch is a safety net, not a shrug.
    if (!pane) continue;
    out.push({
      id: `go.${key}`,
      label: pane.label,
      section: "Go",
      keywords: ["go", "open", "navigate", pane.href, ...pane.note.toLowerCase().split(/\s+/)],
      icon: pane.icon,
      sequence: ["g", key],
      href: pane.href,
      run: (actions) => actions.navigate(pane.href),
    });
  }
  return out;
}

/** `v <letter>` → a canvas. */
function viewCommands(): Command[] {
  return VIEW_MODES.map((entry) => ({
    id: `view.${entry.id}`,
    label: `${entry.id[0].toUpperCase()}${entry.id.slice(1)} view`,
    section: "View" as const,
    keywords: ["view", "canvas", "layout", entry.id],
    icon: entry.icon,
    sequence: ["v", entry.key],
    when: (ctx: CommandContext) => ctx.hasProject,
    run: (actions: CommandActions) => actions.setMode(entry.id),
  }));
}

const PANEL_COMMANDS: Command[] = [
  {
    id: "panel.wider",
    label: "Widen the task panel",
    section: "Panel",
    keywords: ["peek", "side", "full", "expand", "escalate", "bigger"],
    icon: "Maximize2",
    when: (ctx) => ctx.panelOpen && ctx.panelMode !== "full",
    run: (actions, ctx) => actions.setPanelMode(widerPanel(ctx.panelMode)),
  },
  {
    id: "panel.narrower",
    label: "Narrow the task panel",
    section: "Panel",
    keywords: ["peek", "side", "shrink", "smaller"],
    icon: "Minimize2",
    when: (ctx) => ctx.panelOpen && ctx.panelMode !== "peek",
    run: (actions, ctx) => actions.setPanelMode(narrowerPanel(ctx.panelMode)),
  },
];

// "My work" and its return command were REMOVED (owner directive
// 2026-08-31): /tasks is the personal lens (D52-D54), reachable as `g t`.
const PROJECT_COMMANDS: Command[] = [
  {
    id: "project.clearFilters",
    label: "Clear the filters",
    section: "Project",
    keywords: ["reset", "unfilter", "everything"],
    icon: "X",
    when: (ctx) => ctx.filtered,
    run: (actions) => actions.clearFilters(),
  },
  {
    id: "project.rail",
    label: "Show or hide the project tree",
    section: "Project",
    keywords: ["sidebar", "rail", "spaces", "departments", "collapse"],
    icon: "PanelLeftClose",
    when: (ctx) => ctx.canToggleRail,
    run: (actions) => actions.toggleRail(),
  },
  {
    id: "project.fields",
    label: "Custom fields",
    section: "Project",
    keywords: ["columns", "properties", "custom"],
    icon: "SlidersHorizontal",
    when: (ctx) => ctx.hasProject,
    run: (actions) => actions.manage("fields"),
  },
  {
    id: "project.tags",
    label: "Tags",
    section: "Project",
    keywords: ["labels", "registry"],
    icon: "Tag",
    when: (ctx) => ctx.hasProject,
    run: (actions) => actions.manage("tags"),
  },
  {
    id: "project.lifecycle",
    label: "Lifecycle policy",
    section: "Project",
    keywords: ["archive", "auto", "close", "sweep"],
    icon: "Archive",
    // The gateway 422s a child project: the policy is a root setting the whole
    // subtree inherits, so offering it on a subproject would offer a refusal.
    when: (ctx) => ctx.hasProject && ctx.isRoot,
    run: (actions) => actions.manage("lifecycle"),
  },
];

const HELP_COMMANDS: Command[] = [
  {
    id: "help.shortcuts",
    label: "Keyboard shortcuts",
    section: "Help",
    keywords: ["keys", "help", "cheatsheet", "bindings"],
    icon: "Keyboard",
    sequence: ["?"],
    run: (actions) => actions.showShortcuts(),
  },
];

/** THE registry. Everything the palette, the keyboard and the sheet know. */
export const COMMANDS: readonly Command[] = [
  ...goCommands(),
  ...viewCommands(),
  ...PANEL_COMMANDS,
  ...PROJECT_COMMANDS,
  ...HELP_COMMANDS,
];

/** The commands that mean something where the app currently is. */
export function availableCommands(
  ctx: CommandContext,
  registry: readonly Command[] = COMMANDS,
): Command[] {
  return registry.filter((command) => !command.when || command.when(ctx));
}

/**
 * Which commands a typed query names.
 *
 * An empty query returns everything applicable — a palette that shows nothing
 * until you type is a palette that teaches nobody what it can do. Matching is
 * over the label, the section and the keywords; a label match sorts above a
 * keyword match so typing "tags" puts *Tags* first and not the project whose
 * note happens to mention them.
 */
export function matchCommands(
  commands: readonly Command[],
  query: string,
): Command[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...commands];
  const scored: Array<{ command: Command; score: number; at: number }> = [];
  commands.forEach((command, at) => {
    const label = command.label.toLowerCase();
    if (label.startsWith(q)) scored.push({ command, score: 0, at });
    else if (label.includes(q)) scored.push({ command, score: 1, at });
    else if (
      command.section.toLowerCase().includes(q) ||
      command.keywords.some((word) => word.includes(q)) ||
      (command.sequence ?? []).join(" ").includes(q)
    )
      scored.push({ command, score: 2, at });
  });
  return scored
    .sort((a, b) => a.score - b.score || a.at - b.at)
    .map((entry) => entry.command);
}

/** "g p" — how a sequence is printed, everywhere it is printed. */
export function sequenceLabel(command: Command): string {
  return (command.sequence ?? []).join(" ");
}

export interface SequenceStep {
  /** The prefix still pending. Empty when the sequence finished or died. */
  pending: string[];
  /** The command this keystroke completed, if any. */
  command: Command | null;
  /** True when the keystroke belonged to a sequence and must be swallowed. */
  claimed: boolean;
}

/**
 * One keystroke against the pending prefix.
 *
 * **A key that starts nothing after a dead prefix still gets to start its
 * own.** Pressing `g` then `z` then `g` then `p` must reach Projects: without
 * the restart, the third `g` would be swallowed clearing the prefix and the
 * `p` would land on nothing — a shortcut that silently fails once per typo is
 * a shortcut people stop trusting.
 */
export function stepSequence(
  pending: readonly string[],
  key: string,
  commands: readonly Command[],
): SequenceStep {
  const next = [...pending, key];
  const sequences = commands.filter((c) => c.sequence && c.sequence.length > 0);
  const exact = sequences.find(
    (c) =>
      c.sequence!.length === next.length &&
      c.sequence!.every((step, i) => step === next[i]),
  );
  if (exact) return { pending: [], command: exact, claimed: true };
  const prefix = sequences.some(
    (c) =>
      c.sequence!.length > next.length &&
      next.every((step, i) => c.sequence![i] === step),
  );
  if (prefix) return { pending: next, command: null, claimed: true };
  // Dead end. Try the key on its own before giving up.
  if (pending.length > 0) return stepSequence([], key, commands);
  return { pending: [], command: null, claimed: false };
}

/**
 * Should this keystroke be ignored because somebody is typing into something?
 *
 * A bare letter shortcut and a text field are the classic collision: without
 * this, typing "go" into the quick-add box navigates away mid-word.
 */
export function isTypingTarget(
  target: { tagName?: string; isContentEditable?: boolean } | null | undefined,
): boolean {
  if (!target) return false;
  if (target.isContentEditable === true) return true;
  const tag = (target.tagName ?? "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select";
}

/** How long a half-typed prefix survives before it is forgotten. */
export const SEQUENCE_TIMEOUT_MS = 1500;

/**
 * Does this keystroke get offered to the sequence matcher at all?
 *
 * Meta/Ctrl/Alt chords belong to the browser and the OS; Shift does NOT,
 * because `?` is Shift-slash on every layout that has one and excluding it
 * would make the help key unreachable.
 */
export function isSequenceKey(event: {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
}): boolean {
  if (event.metaKey || event.ctrlKey || event.altKey) return false;
  return event.key.length === 1;
}

/** ── What the palette draws ─────────────────────────────────────────────── */

export type PaletteRow =
  | { kind: "section"; key: string; label: string }
  | { kind: "command"; key: string; command: Command }
  | { kind: "task"; key: string; hit: Hit };

export interface PaletteList {
  rows: PaletteRow[];
  /** Indices into `rows` that the cursor may land on — never a heading. */
  pickable: number[];
}

/**
 * Commands and task hits, in one list with headings.
 *
 * Commands lead because they are local and instantaneous; the task hits arrive
 * from the server after a debounce, and a list that reorders itself under the
 * cursor when the response lands is a list that opens the wrong thing. Adding
 * rows *below* the cursor cannot move what is already under it.
 */
export function paletteList(
  commands: readonly Command[],
  hits: readonly Hit[],
): PaletteList {
  const rows: PaletteRow[] = [];
  const pickable: number[] = [];
  for (const section of COMMAND_SECTIONS) {
    const inSection = commands.filter((c) => c.section === section);
    if (inSection.length === 0) continue;
    rows.push({ kind: "section", key: `section:${section}`, label: section });
    for (const command of inSection) {
      pickable.push(rows.length);
      rows.push({ kind: "command", key: `command:${command.id}`, command });
    }
  }
  if (hits.length > 0) {
    rows.push({ kind: "section", key: "section:tasks", label: "Tasks" });
    for (const hit of hits) {
      pickable.push(rows.length);
      rows.push({ kind: "task", key: `task:${hit.id}`, hit });
    }
  }
  return { rows, pickable };
}

/** ── What the shortcuts sheet prints ────────────────────────────────────── */

export interface ShortcutRow {
  label: string;
  /** The sequence, or `""` for a command that is palette-only. */
  keys: string;
  icon: string;
}

export interface ShortcutSection {
  section: CommandSection;
  rows: ShortcutRow[];
}

/**
 * The sheet, generated from the registry.
 *
 * Every command appears — the ones with a sequence showing their keys, the
 * rest showing none, because "reachable from ⌘K" is itself the answer to *how
 * do I do this*. `when` is deliberately NOT applied: the sheet is a reference
 * for the app, not a report on this instant, and a shortcut vanishing from the
 * help because no task happens to be open is worse than one listed while it is
 * momentarily inapplicable.
 */
export function shortcutSections(
  registry: readonly Command[] = COMMANDS,
): ShortcutSection[] {
  return COMMAND_SECTIONS.map((section) => ({
    section,
    rows: registry
      .filter((command) => command.section === section)
      .map((command) => ({
        label: command.label,
        keys: sequenceLabel(command),
        icon: command.icon,
      })),
  })).filter((entry) => entry.rows.length > 0);
}
