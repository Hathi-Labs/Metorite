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
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { useAccess } from "@/components/AccessProvider";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useActivityRows, useRunActivity } from "@/hooks/useActiveSessions";
import { shouldPollWorkspace } from "@/lib/access";
import { visibleSections } from "@/lib/nav";

import { ActivityButton, openActivity, useActivityOpen } from "./ActivityControl";
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

/** The event that puts the cursor in the bar's field (`ShellBar.tsx` sends it). */
export const FOCUS_CHAT_BAR = "cc-focus-chat-bar";

/** "⌘J" on a Mac, "Ctrl J" elsewhere. Read after mount, so the server agrees. */
export function useAssistantKeyLabel(): string {
  const [label, setLabel] = useState("Ctrl J");
  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- read once after mount
      if (/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)) setLabel("⌘J");
    } catch {
      /* keep Ctrl J */
    }
  }, []);
  return label;
}

/** The placeholder: the assistant's name, and the app it starts from. */
export function barPlaceholder(appLabel: string | null): string {
  return appLabel ? `Ask ${ASSISTANT_NAME} about ${appLabel}, or anything else…` : `Ask ${ASSISTANT_NAME} anything…`;
}

/** Where the member is, for the placeholder and the chip. */
function useHere() {
  const pathname = usePathname();
  const { access, loading } = useAccess();
  const sections = useMemo(
    () => visibleSections(loading ? null : access.features, access.is_admin),
    [loading, access.features, access.is_admin],
  );
  return paneAt(pathname, sections);
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

/** The desktop bar: a row at the foot of the page column. */
export function ChatBar() {
  const { open } = useAssistant();
  const here = useHere();
  const facts = useCurrentFacts();
  const chip = contextChipText(here, facts);
  const keyLabel = useAssistantKeyLabel();
  const [text, setText] = useState("");
  const fieldRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onFocus = () => fieldRef.current?.focus();
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

  const send = () => {
    const words = text.trim();
    if (!words) {
      openAssistant();
      return;
    }
    openAssistant({ text: words });
    setText("");
  };

  return (
    <div data-chat-bar-row="desktop" className="shrink-0 border-t border-border bg-card px-4 py-2">
      <form
        role="search"
        aria-label={`Ask ${ASSISTANT_NAME}`}
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
        className="mx-auto flex w-full max-w-3xl items-center gap-2"
      >
        <Input
          ref={fieldRef}
          icon="Sparkles"
          inputSize="lg"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={barPlaceholder(here?.label ?? null)}
          aria-label={`Ask ${ASSISTANT_NAME} (${keyLabel})`}
          aria-keyshortcuts="Control+J Meta+J"
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
