"use client";

/**
 * Projects · the `?` sheet (WS-27ab item 3).
 *
 * **Generated from the command registry, never written.** Every row here is a
 * `Command` from `lib/commands.ts` — its label, its section and the sequence
 * it actually answers to. There is no list of shortcuts in this file to fall
 * out of step with the keyboard, which is the failure mode of every hand-kept
 * help screen: the binding changes, the sheet does not, and the sheet is what
 * people believe.
 *
 * A command with no sequence still appears, marked as palette-only. "Open ⌘K
 * and type it" is a real answer to *how do I do this*, and hiding those rows
 * would make the sheet look like the app can do a third of what it can.
 *
 * Fence: `commands.test.ts` — "the shortcuts sheet is generated, never
 * written" (every command printed exactly once, the keys are the command's
 * own, and a palette-only command prints no keys rather than an invented one).
 */

import Icon from "@/components/Icon";
import Modal from "@/components/ui/Modal";

import { type ShortcutRow, shortcutSections } from "../lib/commands";

/** One titled block of rows. Projects' sections, or another app's own. */
export interface ShortcutSheetSection {
  section: string;
  rows: readonly ShortcutRow[];
}

/**
 * The `?` sheet. Projects passes nothing and gets its registry. My Tasks
 * passes its own sections (`app/tasks/lib/shortcuts.ts`), generated from
 * its key map, so both apps draw ONE sheet (continuity P3).
 */
export function ShortcutsSheet({
  onClose,
  sections = shortcutSections(),
}: {
  onClose: () => void;
  sections?: readonly ShortcutSheetSection[];
}) {

  // WS-27ak — the hand-rolled overlay and its window-Escape listener are gone.
  //
  // That listener existed because the sheet is raised by a KEY rather than by a
  // click, so nothing inside it was focused and a handler on the dialog alone
  // could never see Escape. `Modal` moves focus into the popup on open, and its
  // substrate binds Escape at the document, so the case the listener was
  // written for no longer exists — and one fewer window listener is one fewer
  // racer against `page.tsx:1039`.
  return (
    <Modal
      open
      onClose={onClose}
      // No `label`: `Modal` uses it only when there is no `title`, and there is
      // one. The close button keeps the specific name this sheet had before
      // WS-27ak — a generic "Close" is fine beside a visible title and useless
      // in a screen reader's list of the page's buttons.
      title="Keyboard shortcuts"
      closeLabel="Close the shortcuts sheet"
      icon="Keyboard"
      size="2xl"
      placement="top"
      className="max-h-[80vh]"
    >
      <div className="max-h-[68vh] overflow-y-auto px-3 py-3">
        <p className="mb-3 text-[11px] text-muted-foreground">
          Press the keys in order — <Keys keys="g p" /> is <em>g</em> then{" "}
          <em>p</em>, not both together. Anything without keys is one{" "}
          <Keys keys="⌘ K" /> away.
        </p>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {sections.map((section) => (
            <section key={section.section}>
              <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                {section.section}
              </h3>
              <ul className="space-y-0.5">
                {section.rows.map((row) => (
                  <li
                    key={`${section.section}:${row.label}`}
                    className="flex items-center gap-2 rounded px-1 py-1 text-xs text-foreground"
                  >
                    <Icon
                      name={row.icon}
                      className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                    />
                    <span className="min-w-0 flex-1 truncate">{row.label}</span>
                    {row.keys ? (
                      <Keys keys={row.keys} />
                    ) : (
                      <span className="shrink-0 text-[10px] text-muted-foreground">
                        ⌘K
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </div>
    </Modal>
  );
}

/**
 * One key per `<kbd>`, so "g p" reads as two presses rather than one chord.
 *
 * `inline-flex`, not `flex`: this is used mid-sentence in the intro line as
 * well as at the end of a row, and a block-level flex there broke the sentence
 * across three lines.
 */
function Keys({ keys }: { keys: string }) {
  return (
    <span className="inline-flex shrink-0 items-center gap-0.5 align-middle">
      {keys.split(" ").map((key, index) => (
        <kbd
          key={`${key}:${index}`}
          className="rounded border border-border bg-muted px-1 text-[10px] text-muted-foreground"
        >
          {key}
        </kbd>
      ))}
    </span>
  );
}
