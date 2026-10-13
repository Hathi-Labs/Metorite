"use client";

/**
 * The chat bar: the one way into the assistant, at the foot of every page
 * (D99, `navigation_shell.md` §7.1, NS-6 slice 6b, flag
 * `NEXT_PUBLIC_SHELL_CHAT`).
 *
 *   [ ✦ Ask Metorite…                     ]  [ Projects · Website relaunch ]  [ Ctrl J ]  [ activity ]  [ ⤢ ]
 *
 * **A row of the frame, not a layer.** `AppShell` puts it under `<main>` in
 * the page column, so an app's own bottom controls sit in `<main>`, above
 * it, and the bar never covers them. The phone has no bar: its bottom nav
 * holds an Ask tab, which opens the assistant as a full-screen sheet
 * (`AppShell.tsx`, `data-ask-tab`).
 *
 * **Enter sends.** The words go to the assistant, which opens and sends them
 * (`openAssistant({ text })`). The expand control opens it with nothing sent.
 * ⌘J or Ctrl J puts the cursor here (`ShellBar.tsx` holds the key).
 *
 * **The chip is a label, not a control** (owner, 2026-10-12: no extra UI for
 * the member). It names what the assistant sees: the app and the page's own
 * label (`useAssistantFacts`).
 *
 * **The activity control lives here** (D99, amends WS-51 S3). One run opens
 * its chat at once. More than one opens the shell's one panel.
 *
 * The bar hides while the assistant is open, which has its own composer.
 */

import { usePathname } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useSession } from "next-auth/react";

import { useAccess } from "@/components/AccessProvider";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useActivityRows, useRunActivity } from "@/hooks/useActiveSessions";
import { shouldPollWorkspace } from "@/lib/access";
import { visibleSections } from "@/lib/nav";

import { ActivityButton, openActivity, useActivityOpen } from "./ActivityControl";
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
  useViewportWidth,
} from "./assistant";

/**
 * The page column with the bar under it, and the assistant beside it or over
 * it. `AppShell` passes the assistant in, loaded on demand, so this file
 * never pulls the chat into the shell's bundle.
 *
 * While the full view covers the page, the page stays mounted, keeps its
 * state, and takes no input (`inert`).
 */
export function AssistantWorkArea({ children, assistant }: { children: ReactNode; assistant: ReactNode }) {
  const st = useAssistant();
  const vw = useViewportWidth();
  const covered = st.open && effectiveMode(st.mode, vw, false) === "full";
  return (
    <div className="relative flex min-h-0 min-w-0 flex-1">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col" inert={covered} data-assistant-covered={covered ? "" : undefined}>
        {children}
        <ChatBar />
      </div>
      {assistant}
    </div>
  );
}

/**
 * The event that puts the cursor in the bar's field (`ShellBar.tsx` sends it).
 * `detail.query` puts words in the field. `detail.results` opens the results
 * at once, as ⌘K and "search everywhere for …" do (D100).
 */
export const FOCUS_CHAT_BAR = "cc-focus-chat-bar";

export interface FocusChatBarDetail {
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

/** The placeholder: one field asks, searches and opens (D100). */
export const BAR_PLACEHOLDER = `Ask ${ASSISTANT_NAME}, or search and open anything…`;

/**
 * The highlight after an arrow key. -1 is the field itself, where Enter asks.
 * The results sit ABOVE the field, so ↑ steps up into them from the bottom
 * row, and ↓ steps back down to the field.
 */
export function moveHighlight(current: number, key: "ArrowUp" | "ArrowDown", count: number): number {
  if (count === 0) return -1;
  if (key === "ArrowUp") return current < 0 ? count - 1 : Math.max(0, current - 1);
  if (current < 0) return -1;
  return current + 1 >= count ? -1 : current + 1;
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

/**
 * The activity control, as the bar draws it. One run opens its chat in the
 * assistant. More than one opens the one panel, whose rows open in the
 * assistant too (`ActivityHost`).
 */
function BarActivity({ className = "" }: { className?: string }) {
  const { access, loading } = useAccess();
  const workspace = shouldPollWorkspace(access, loading);
  const { total, needsTotal, unreadTotal } = useRunActivity(null, workspace);
  const rows = useActivityRows(workspace);
  const open = useActivityOpen();
  if (!workspace) return null;
  return (
    <ActivityButton
      running={total}
      needsInput={needsTotal}
      unread={unreadTotal}
      open={open}
      onOpen={() => {
        if (rows.length === 1) openAssistant({ sessionId: rows[0].threadId });
        else openActivity();
      }}
      className={className}
    />
  );
}

/**
 * THE one bar (D100): ask, search and open, from one field at the foot of
 * the page. It replaced the command bar's field at the top of the shell.
 *
 * - Typing shows the results above the field: Do, Go to, Find, In this page
 *   (`useBarResults`, the engine the modal reads too).
 * - **Enter asks Metorite**, unless the member moved the highlight onto a
 *   result. The bottom row of the results says so: "↵ Ask Metorite".
 * - ↑ steps into the results, ↓ steps back to the field, Esc closes them.
 * - A click in an empty field opens nothing: it is a chat box first.
 */
export function ChatBar() {
  const { open } = useAssistant();
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
      const detail = (e as CustomEvent<FocusChatBarDetail | undefined>).detail ?? {};
      if (typeof detail.query === "string") setText(detail.query);
      setActiveKey(null);
      if (detail.results) setShowing(true);
      fieldRef.current?.focus();
    };
    window.addEventListener(FOCUS_CHAT_BAR, onFocus);
    return () => window.removeEventListener(FOCUS_CHAT_BAR, onFocus);
  }, []);

  // While the bar shows, the toasts rise above it (`globals.css`).
  useEffect(() => {
    if (open) return;
    const root = document.documentElement;
    root.setAttribute("data-chat-bar", "desktop");
    return () => root.removeAttribute("data-chat-bar");
  }, [open]);

  if (open) return null;

  const ask = () => {
    const words = text.trim();
    setShowing(false);
    setActiveKey(null);
    if (!words) {
      openAssistant();
      return;
    }
    openAssistant({ text: words });
    setText("");
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      if (!showing && e.key === "ArrowDown") return;
      e.preventDefault();
      if (!showing) setShowing(true);
      const next = moveHighlight(held, e.key, rows.length);
      setActiveKey(next >= 0 ? rows[next].key : null);
    } else if (e.key === "Escape") {
      if (showing) {
        e.preventDefault();
        setShowing(false);
        setActiveKey(null);
      } else {
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
    <div data-chat-bar-row="desktop" className="shrink-0 border-t border-border bg-card px-4 py-2">
      <form
        role="search"
        aria-label={`Ask ${ASSISTANT_NAME}, or search`}
        onSubmit={(e) => {
          e.preventDefault();
          ask();
        }}
        className="relative mx-auto flex w-full max-w-3xl items-center gap-2"
      >
        {panel ? (
          <div
            data-chat-bar-results=""
            className="absolute inset-x-0 bottom-full z-30 mb-2 flex max-h-[60vh] flex-col overflow-hidden rounded-lg border border-border bg-popover shadow-lg"
          >
            <ResultList
              rows={rows}
              active={held}
              onHover={(key) => {
                if (key !== activeKey) setActiveKey(key);
              }}
              prefix="chatbar"
              query={text}
              finding={finding}
              className="min-h-0 flex-1"
            />
            {/* The field's own act, nearest the field: Enter asks. A click
                here asks too. It is not an option of the list, so the list
                stays one kind of thing for a screen reader. */}
            {words ? (
              <button
                type="submit"
                onMouseDown={(e) => e.preventDefault()}
                data-chat-bar-ask=""
                className={`flex shrink-0 items-center gap-3 border-t border-border px-4 py-2.5 text-left text-[13px] tech-transition ${
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
            <div className="flex shrink-0 items-center gap-3 border-t border-border px-4 py-1.5 text-[11px] text-muted-foreground">
              <span>↑ ↓ to choose</span>
              <span>Enter to {held >= 0 ? "open" : "ask"}</span>
              <span>Esc to close</span>
            </div>
          </div>
        ) : null}
        <Input
          ref={fieldRef}
          icon="Sparkles"
          inputSize="lg"
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
          aria-controls="chatbar-list"
          aria-activedescendant={held >= 0 ? resultOptionId("chatbar", rows[held].key) : undefined}
          aria-label={`Ask ${ASSISTANT_NAME}, or search and open anything (${keyLabel})`}
          aria-keyshortcuts="Control+K Meta+K Control+J Meta+J"
          autoComplete="off"
          className="min-w-0 flex-1"
        />
        {chip ? (
          <span
            data-chat-bar-context=""
            title="The assistant sees where you are"
            className="hidden max-w-60 shrink-0 truncate rounded-md bg-secondary px-2 py-1 text-[11px] text-muted-foreground lg:inline"
          >
            {chip}
          </span>
        ) : null}
        <kbd className="hidden shrink-0 rounded border border-border px-1.5 py-0.5 font-sans text-[10px] text-muted-foreground md:inline">
          {keyLabel}
        </kbd>
        <BarActivity className="shrink-0" />
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          icon="PanelRightOpen"
          aria-label={`Open ${ASSISTANT_NAME}`}
          title={`Open ${ASSISTANT_NAME}`}
          onClick={() => openAssistant()}
          className="shrink-0"
        />
      </form>
    </div>
  );
}
