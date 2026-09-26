/**
 * My Tasks · the keyboard map, and the `?` sheet printed from it
 * (continuity P3, item 7).
 *
 * `?` opens the SAME sheet Projects opens (`ShortcutsSheet`). The Go section
 * is the same `g <letter>` commands Projects binds (`GO_COMMANDS`), so `g p`
 * reaches Projects from here and `g t` reaches My Tasks from there.
 *
 * The rows below are My Tasks' real bindings. A hand-kept help screen drifts
 * from the keyboard, so `shortcuts.test.ts` reads the handlers' source and
 * fails when a key is bound and not listed, or listed and not bound.
 *
 * Pure: no React and no DOM. The component that binds the keys is
 * `components/TasksShortcuts.tsx`.
 */

import {
  type Command,
  GO_COMMANDS,
  HELP_COMMANDS,
  type SequenceStep,
  isSequenceKey,
  isTypingTarget,
  sequenceLabel,
  stepSequence,
} from "@/app/projects/lib/commands";

export interface TasksKey {
  /** How the key prints on the sheet: `"c"`, `"⌘ K"`, `"↑ ↓"`. */
  keys: string;
  label: string;
  icon: string;
}

/** Bound on every My Tasks view (`page.tsx`, `UndoToast.tsx`). */
export const TASKS_GLOBAL_KEYS: readonly TasksKey[] = [
  { keys: "c", label: "Capture", icon: "Plus" },
  { keys: "⌘ K", label: "Search", icon: "Search" },
  { keys: "u", label: "Undo the last change", icon: "Undo2" },
  { keys: "?", label: "Keyboard shortcuts", icon: "Keyboard" },
];

/**
 * Bound in the Inbox (`InboxView.tsx`'s triage switch). `bound` is the
 * `e.key` value the switch tests, so the fence can compare the two.
 */
export const INBOX_KEYS: readonly (TasksKey & { bound: string })[] = [
  { keys: "e", bound: "e", label: "Edit the title", icon: "Pencil" },
  { keys: "x", bound: "x", label: "Select", icon: "CheckSquare" },
  { keys: "t", bound: "t", label: "Delete, or remove from my lists", icon: "Trash2" },
  { keys: "s", bound: "s", label: "Someday / Maybe", icon: "Lightbulb" },
  { keys: "r", bound: "r", label: "Reference", icon: "FileText" },
  { keys: "2", bound: "2", label: "Do it now (done)", icon: "Check" },
  { keys: "m", bound: "m", label: "Move to project…", icon: "FolderInput" },
  { keys: "o", bound: "o", label: "Open on its board", icon: "ExternalLink" },
  { keys: "esc", bound: "Escape", label: "Clear the selection", icon: "X" },
];

/** The shared cursor's keys (`@/lib/cursor`), which the Inbox also answers. */
export const CURSOR_KEYS: readonly TasksKey[] = [
  { keys: "↑ ↓", label: "Move", icon: "ArrowUpDown" },
  { keys: "↵", label: "Clarify", icon: "Sparkles" },
];

/** The sheet, top to bottom. Go first, as Projects prints it. */
export function tasksShortcutSections(): { section: string; rows: TasksKey[] }[] {
  return [
    {
      section: "Go",
      rows: GO_COMMANDS.map((c) => ({ keys: sequenceLabel(c), label: c.label, icon: c.icon })),
    },
    { section: "My Tasks", rows: [...TASKS_GLOBAL_KEYS] },
    {
      section: "Inbox",
      rows: [...CURSOR_KEYS, ...INBOX_KEYS.map(({ keys, label, icon }) => ({ keys, label, icon }))],
    },
  ];
}

/** The sequences My Tasks answers: the shared go-keys, and `?`. */
export const TASKS_SEQUENCES: readonly Command[] = [...GO_COMMANDS, ...HELP_COMMANDS];

/** What one keystroke did. */
export type TasksKeyOutcome =
  | { kind: "go"; href: string }
  | { kind: "help" }
  | { kind: "none" };

/**
 * Is this keystroke offered to the sequence matcher at all? Never inside a
 * text field, a textarea or a select: typing "?" into the capture box must
 * type a question mark. Never with Meta, Ctrl or Alt held.
 */
export function offersKey(event: {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  target?: {
    tagName?: string;
    isContentEditable?: boolean;
    getAttribute?: (name: string) => string | null;
  } | null;
}): boolean {
  if (!isSequenceKey(event)) return false;
  return !isTypingTarget(event.target ?? null);
}

/** The store fields that say another surface owns the keyboard. */
export interface TasksOverlayState {
  quickCaptureOpen: boolean;
  clarifyModalOpen: boolean;
  settingsModalOpen: boolean;
  focusedItemId: string | null;
  reclarifyItemId: string | null;
  scheduleItemId: string | null;
  eliminateItemId: string | null;
  delegateItemId: string | null;
  pendingDeleteIds: string[] | null;
}

/**
 * Is an overlay open, so `?` and `g` must stay silent? The My Tasks twin of
 * Projects' `overlayOpen`: every store-driven dialog, plus the page's own
 * search palette and maximised task. A dialog that a component opens from
 * its own state (the task detail's DelegateDialog, the PromoteDialog) is not
 * in the store. `TasksShortcuts` catches those by an open modal in the DOM.
 */
export function tasksOverlayOpen(
  s: TasksOverlayState,
  page: { searching: boolean; maximised: boolean },
): boolean {
  return (
    page.searching ||
    page.maximised ||
    s.quickCaptureOpen ||
    s.clarifyModalOpen ||
    s.settingsModalOpen ||
    Boolean(s.focusedItemId) ||
    Boolean(s.reclarifyItemId) ||
    Boolean(s.scheduleItemId) ||
    Boolean(s.eliminateItemId) ||
    Boolean(s.delegateItemId) ||
    Boolean(s.pendingDeleteIds?.length)
  );
}

/**
 * While the sheet is open, does this key reach the page? Only Escape (the
 * dialog closes on it) and Tab (the dialog traps focus with it). Every other
 * key would reach the Inbox's triage switch, which acts on the row under the
 * cursor behind the sheet.
 */
export function keyPassesOpenSheet(key: string): boolean {
  return key === "Escape" || key === "Tab";
}

/** One keystroke against the pending prefix, and what it resolves to. */
export function stepTasksKey(
  pending: readonly string[],
  key: string,
): SequenceStep & { outcome: TasksKeyOutcome } {
  const step = stepSequence(pending, key, TASKS_SEQUENCES);
  const command = step.command;
  const outcome: TasksKeyOutcome = !command
    ? { kind: "none" }
    : command.href
      ? { kind: "go", href: command.href }
      : command.id === "help.shortcuts"
        ? { kind: "help" }
        : { kind: "none" };
  return { ...step, outcome };
}
