/**
 * Keyboard predicates shared by every app.
 *
 * `isTypingTarget` existed as one good implementation in
 * `app/projects/lib/commands.ts` and **seven hand-rolled copies** elsewhere
 * (`tasks/page.tsx`, `tasks/InboxView.tsx`, `tasks/UndoToast.tsx`,
 * `tasks/ClarifyModal.tsx`, `tasks/ClarifyPanel.tsx`, `email/page.tsx`), each
 * spelling out `tagName === "INPUT" || …`. They already disagree: only the
 * Projects one counts a `<select>`, so a shortcut fired while a dropdown had
 * focus in one app and not in another.
 *
 * A global undo shortcut makes that divergence dangerous rather than untidy —
 * Ctrl+Z inside a text field must reach the BROWSER, not the app, or typing a
 * note and pressing undo reverts somebody's task instead of their sentence. So
 * the predicate is stated once here, and `projects/lib/commands.ts` re-exports
 * it so its callers are unchanged.
 */

/** Is the event's target a field where the user is typing? */
export function isTypingTarget(
  target: { tagName?: string; isContentEditable?: boolean } | null | undefined,
): boolean {
  if (!target) return false;
  if (target.isContentEditable === true) return true;
  const tag = (target.tagName ?? "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select";
}

/** The modifier that means "application command" on this platform. */
function hasCommandModifier(event: {
  ctrlKey: boolean;
  metaKey: boolean;
}): boolean {
  // Either, rather than platform-sniffing: a Mac user on an external PC
  // keyboard presses Ctrl, and nobody has a use for Ctrl+Z meaning something
  // else. `navigator.platform` is deprecated and lies under emulation anyway.
  return event.ctrlKey || event.metaKey;
}

export interface ShortcutEvent {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
}

/** Ctrl/Cmd+Z, and not the redo variant. */
export function isUndoShortcut(event: ShortcutEvent): boolean {
  if (!hasCommandModifier(event) || event.altKey) return false;
  return event.key.toLowerCase() === "z" && !event.shiftKey;
}

/**
 * Ctrl/Cmd+Shift+Z, or Ctrl+Y.
 *
 * Both, because the two conventions are split down the middle — Shift+Z is the
 * Mac and web-app norm, Ctrl+Y the Windows-desktop one — and a user whose
 * muscle memory is the other one experiences "redo is broken", not "redo is
 * bound differently".
 */
export function isRedoShortcut(event: ShortcutEvent): boolean {
  if (!hasCommandModifier(event) || event.altKey) return false;
  const key = event.key.toLowerCase();
  return (key === "z" && event.shiftKey) || (key === "y" && !event.shiftKey);
}
