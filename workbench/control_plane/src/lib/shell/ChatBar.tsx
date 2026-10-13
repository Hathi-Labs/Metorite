"use client";

/**
 * THE one field (D99, D102 as amended 2026-10-14): ask Metorite, search and
 * open, from the middle of the shell bar at the top of every desktop page.
 *
 *   [ ✦ Ask Metorite, or search and open anything…   in Projects   ⌘K ]
 *
 * The owner's words, 2026-10-14: "remove the bar at the bottom and have it at
 * the title bar … because we have space in the top bar in any case. The
 * functionality should be as expected by the user." So the field kept every
 * act of the chat bar, and only moved.
 *
 * - **Typing drops the results below the field:** Do, Go to, Find and In this
 *   page (`useBarResults`, the engine the modal reads too).
 * - **Enter asks Metorite.** The top row of the results says so. Enter opens a
 *   result only after the member moves the highlight onto it, with ↓ or the
 *   mouse. ↑ from the top row returns to the field. Esc closes the results,
 *   and a second Esc clears the field.
 * - **Enter on an empty field opens the assistant.**
 * - **With the assistant open, the words go into the chat on screen**
 *   (`AssistantAsk.join`), not into a new one.
 * - **The chip names where the member is,** the search's own app and what the
 *   assistant sees. It is a label, not a control (owner, 2026-10-12).
 *
 * `AssistantWorkArea` holds the page and the assistant beside it or over it.
 * The phone has no shell row: its bottom nav holds an Ask tab, and its Menu
 * drawer opens the modal (`AppShell.tsx`).
 */

import { usePathname } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useSession } from "next-auth/react";

import { useAccess } from "@/components/AccessProvider";
import Icon from "@/components/Icon";
import { visibleSections } from "@/lib/nav";

import { ResultList, resultOptionId, useBarResults } from "./CommandBar";
import { heldPanes } from "./registry";
import { useShellPrefs } from "./shellPrefs";
import {
  ASSISTANT_NAME,
  contextChipText,
  effectiveMode,
  openAssistant,
  paneAt,
  useAssistant,
  useCurrentFacts,
} from "./assistant";

/**
 * The page column and the assistant beside it or over it. `AppShell` passes
 * the assistant in, loaded on demand, so this file never pulls the chat into
 * the shell's bundle.
 *
 * While the full view covers the page, the page stays mounted, keeps its
 * state, and takes no input (`inert`).
 */
export function AssistantWorkArea({ children, assistant }: { children: ReactNode; assistant: ReactNode }) {
  const st = useAssistant();
  const covered = st.open && effectiveMode(st.mode, 0, false) === "full";
  return (
    <div className="relative flex min-h-0 min-w-0 flex-1">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col" inert={covered} data-assistant-covered={covered ? "" : undefined}>
        {children}
      </div>
      {assistant}
    </div>
  );
}

/**
 * The event that puts the cursor in the field (`ShellBar.tsx` sends it).
 * `detail.query` puts words in the field. `detail.results` opens the results
 * at once, as ⌘K and "search everywhere for …" do.
 */
export const FOCUS_ASK_FIELD = "cc-focus-ask-field";

export interface FocusAskFieldDetail {
  query?: string;
  results?: boolean;
}

/** "⌘K" on a Mac, "Ctrl K" elsewhere. Read after mount, so the server agrees. */
export function useBarKeyLabel(): string {
  const [label, setLabel] = useState("Ctrl K");
  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- read once after mount
      if (/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)) setLabel("⌘K");
    } catch {
      /* keep Ctrl K */
    }
  }, []);
  return label;
}

/** The placeholder: one field asks, searches and opens. */
export const BAR_PLACEHOLDER = `Ask ${ASSISTANT_NAME}, or search and open anything…`;

/**
 * The highlight after an arrow key. -1 is the field itself, where Enter asks.
 * The results drop BELOW the field, so ↓ steps down into them from the top
 * row, and ↑ from the top row steps back up to the field.
 */
export function moveHighlight(current: number, key: "ArrowUp" | "ArrowDown", count: number): number {
  if (count === 0) return -1;
  if (key === "ArrowDown") return current < 0 ? 0 : Math.min(count - 1, current + 1);
  return current <= 0 ? -1 : current - 1;
}

/** Where the member is, for the chip and the search's own app. */
function useHere() {
  const pathname = usePathname();
  const { access, loading } = useAccess();
  const sections = useMemo(
    () => visibleSections(loading ? null : access.features, access.is_admin),
    [loading, access.features, access.is_admin],
  );
  return { here: paneAt(pathname, sections), panes: heldPanes(sections) };
}

/** The one field, in the middle of the shell bar. */
export function AskField() {
  const { here, panes } = useHere();
  const facts = useCurrentFacts();
  const chip = contextChipText(here, facts);
  const keyLabel = useBarKeyLabel();
  const { data: session } = useSession();
  const shell = useShellPrefs();
  const [text, setText] = useState("");
  const [showing, setShowing] = useState(false);
  // The highlighted result by its KEY, as the modal holds it: Find rows land
  // late and move the rows around them. Null is the field, where Enter asks.
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const fieldRef = useRef<HTMLInputElement>(null);

  const done = useCallback(() => {
    setShowing(false);
    setActiveKey(null);
    setText("");
    fieldRef.current?.blur();
  }, []);

  const { rows, finding } = useBarResults({
    active: showing,
    query: text,
    token: here,
    here,
    panes,
    email: session?.user?.email ?? null,
    jobOrder: shell.enabled ? shell.layout.jobs : undefined,
    onDone: done,
    askRow: false,
  });
  const held = activeKey ? rows.findIndex((r) => r.key === activeKey) : -1;

  useEffect(() => {
    const onFocus = (e: Event) => {
      const detail = (e as CustomEvent<FocusAskFieldDetail | undefined>).detail ?? {};
      if (typeof detail.query === "string") setText(detail.query);
      setActiveKey(null);
      if (detail.results) setShowing(true);
      fieldRef.current?.focus();
    };
    window.addEventListener(FOCUS_ASK_FIELD, onFocus);
    return () => window.removeEventListener(FOCUS_ASK_FIELD, onFocus);
  }, []);

  const ask = () => {
    const words = text.trim();
    setShowing(false);
    setActiveKey(null);
    fieldRef.current?.blur();
    if (!words) {
      openAssistant();
      return;
    }
    openAssistant({ text: words });
    setText("");
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      if (!showing && e.key === "ArrowUp") return;
      e.preventDefault();
      if (!showing) setShowing(true);
      const next = moveHighlight(held, e.key, rows.length);
      setActiveKey(next >= 0 ? rows[next].key : null);
    } else if (e.key === "Escape") {
      e.preventDefault();
      // The first Esc closes the results. The next one clears the field and
      // gives the page its keys back.
      if (showing) {
        setShowing(false);
        setActiveKey(null);
      } else {
        setText("");
        fieldRef.current?.blur();
      }
    } else if (e.key === "Enter" && held >= 0) {
      e.preventDefault();
      rows[held].run();
    }
  };

  const words = text.trim();
  const panel = showing && (words.length > 0 || rows.length > 0);

  return (
    <form
      role="search"
      aria-label={`Ask ${ASSISTANT_NAME}, or search`}
      data-ask-field=""
      onSubmit={(e) => {
        e.preventDefault();
        ask();
      }}
      className="relative w-full max-w-xl shrink"
    >
      <div className="flex h-8 items-center gap-2 rounded-lg border border-border bg-background px-3 tech-transition focus-within:border-primary/50 hover:border-primary/40">
        <Icon name="Sparkles" size={15} className="shrink-0 text-primary" />
        {/* A bare field inside the bar's own box: the box is the control's
            border and focus ring, so the field draws neither (as the old
            fake field did). */}
        <input
          ref={fieldRef}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setActiveKey(null);
            setShowing(e.target.value.trim().length > 0);
          }}
          onKeyDown={onKeyDown}
          onBlur={() => {
            setShowing(false);
            setActiveKey(null);
          }}
          placeholder={BAR_PLACEHOLDER}
          role="combobox"
          aria-expanded={panel}
          aria-controls="askfield-list"
          aria-activedescendant={held >= 0 ? resultOptionId("askfield", rows[held].key) : undefined}
          aria-label={`Ask ${ASSISTANT_NAME}, or search and open anything (${keyLabel})`}
          aria-keyshortcuts="Control+K Meta+K"
          autoComplete="off"
          className="h-full min-w-0 flex-1 bg-transparent text-[13px] text-foreground outline-none placeholder:text-muted-foreground"
        />
        {chip ? (
          <span
            data-ask-field-context=""
            title="The assistant sees where you are"
            className="hidden max-w-48 shrink-0 truncate rounded bg-secondary px-1.5 py-0.5 text-[11px] text-muted-foreground lg:inline"
          >
            {chip}
          </span>
        ) : null}
        <kbd className="shrink-0 rounded border border-border px-1.5 py-0.5 font-sans text-[10px] text-muted-foreground">
          {keyLabel}
        </kbd>
      </div>

      {panel ? (
        <div
          data-ask-field-results=""
          className="absolute left-0 right-0 top-full z-50 mt-1 flex max-h-[70vh] flex-col overflow-hidden rounded-lg border border-border bg-popover shadow-lg"
        >
          {/* The field's own act, nearest the field: Enter asks. A click here
              asks too. It is not an option of the list, so the list stays one
              kind of thing for a screen reader. */}
          {words ? (
            <button
              type="submit"
              onMouseDown={(e) => e.preventDefault()}
              data-ask-field-ask=""
              className={`flex shrink-0 items-center gap-3 border-b border-border px-4 py-2.5 text-left text-[13px] tech-transition ${
                held < 0 ? "bg-primary/10 text-foreground" : "text-muted-foreground hover:bg-secondary"
              }`}
            >
              <Icon name="Sparkles" size={16} className="shrink-0 text-primary" />
              <span className="min-w-0 flex-1 truncate">
                Ask {ASSISTANT_NAME}: “{words}”
              </span>
              <kbd className="shrink-0 rounded border border-border px-1.5 py-0.5 font-sans text-[10px] text-muted-foreground">
                Enter
              </kbd>
            </button>
          ) : null}
          <ResultList
            rows={rows}
            active={held}
            onHover={(key) => {
              if (key !== activeKey) setActiveKey(key);
            }}
            prefix="askfield"
            query={text}
            finding={finding}
            className="min-h-0 flex-1"
          />
          <div className="flex shrink-0 items-center gap-3 border-t border-border px-4 py-1.5 text-[11px] text-muted-foreground">
            <span>↑ ↓ to choose</span>
            <span>Enter to {held >= 0 ? "open" : "ask"}</span>
            <span>Esc to close</span>
          </div>
        </div>
      ) : null}
    </form>
  );
}
