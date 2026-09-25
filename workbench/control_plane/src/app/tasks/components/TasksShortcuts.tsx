"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ShortcutsSheet } from "@/app/projects/components/ShortcutsSheet";
import { SEQUENCE_TIMEOUT_MS } from "@/app/projects/lib/commands";

import {
  keyPassesOpenSheet,
  offersKey,
  stepTasksKey,
  tasksShortcutSections,
} from "../lib/shortcuts";

/** The event a button raises to open the sheet (the Inbox's keyboard icon). */
const OPEN_EVENT = "tasks:shortcuts";

/** Open the `?` sheet from a click. One sheet, mounted by the page. */
export function openShortcutsSheet(): void {
  window.dispatchEvent(new Event(OPEN_EVENT));
}

/**
 * My Tasks' `?` sheet and `g <letter>` jumps (continuity P3, item 7).
 *
 * Mounted once per page. The listener runs in the CAPTURE phase on `window`,
 * so it sees a key before the Inbox's triage switch does. That matters: after
 * `g`, the `t` of `g t` must jump to My Tasks, and never reach the Inbox's `t`
 * (delete). A key the sequences do not claim passes on untouched.
 *
 * While the sheet is open, the same listener holds back every key except
 * Escape and Tab. The Inbox's triage keys listen on `window` and check no
 * dialog, so without this a `t` pressed over the sheet deleted a task.
 *
 * `blocked` is true while another surface owns the keyboard: Quick Capture,
 * Clarify, the ⌘K palette or the focused task. The sheet is a `Modal` at the
 * dialog layer, and the focused task paints above it.
 */
export function TasksShortcuts({ blocked }: { blocked: boolean }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  // The latest props, read inside the one listener bound on mount.
  const live = useRef({ blocked, open, router });
  useEffect(() => {
    live.current = { blocked, open, router };
  });

  useEffect(() => {
    const onOpen = () => setOpen(true);
    window.addEventListener(OPEN_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_EVENT, onOpen);
  }, []);

  useEffect(() => {
    let pending: string[] = [];
    let timer: ReturnType<typeof setTimeout> | null = null;
    const forget = () => {
      pending = [];
      timer = null;
    };
    const onKey = (event: KeyboardEvent) => {
      if (live.current.open) {
        if (!keyPassesOpenSheet(event.key)) event.stopPropagation();
        return;
      }
      if (live.current.blocked) return;
      // Named fields, never `{...event}`: a DOM event's `key` and modifier
      // flags are prototype getters, and a spread copies none of them.
      const offered = offersKey({
        key: event.key,
        metaKey: event.metaKey,
        ctrlKey: event.ctrlKey,
        altKey: event.altKey,
        target: event.target as HTMLElement | null,
      });
      if (!offered) return;
      const step = stepTasksKey(pending, event.key);
      if (timer) clearTimeout(timer);
      timer = null;
      pending = step.pending;
      if (pending.length > 0) timer = setTimeout(forget, SEQUENCE_TIMEOUT_MS);
      if (!step.claimed) return;
      // Claimed: this key belongs to a sequence, and no other handler on the
      // page may act on it too.
      event.preventDefault();
      event.stopPropagation();
      if (step.outcome.kind === "help") setOpen(true);
      else if (step.outcome.kind === "go") live.current.router.push(step.outcome.href);
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      if (timer) clearTimeout(timer);
    };
  }, []);

  if (!open) return null;
  return <ShortcutsSheet sections={tasksShortcutSections()} onClose={() => setOpen(false)} />;
}
