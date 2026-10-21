/**
 * The rules of the first sign-in dialog that need no React
 * (`components/WelcomeDialog.tsx`, `navigation_shell.md` §8.4). Pure, so
 * each rule is an assertion. Fence: `welcomeSteps.test.ts`.
 *
 * One small store joins them (WS-62 CF-2): whether the dialog is open NOW,
 * so the chat-first landing and the nudge can wait for it to close. The
 * dialog writes it, and nothing else does.
 */

import { useSyncExternalStore } from "react";

/**
 * Whether the question dialog shows.
 *
 * ⚠️ Never while access resolves. Step 2 lists the apps the member holds,
 * and before access resolves that list is empty, so "Done" would save
 * `pins: []` and hide every app.
 */
export function layoutQuestionOpen(a: {
  navOn: boolean;
  asking: boolean;
  reopened: boolean;
  step: "ask" | "apps" | "invite";
  accessLoading: boolean;
}): boolean {
  return a.navOn && !a.accessLoading && (a.asking || a.reopened) && (a.step === "ask" || a.step === "apps");
}

/**
 * Whether step 2, "Choose the apps for your sidebar", is skipped (WS-62
 * CF-2, `chat_first_surface.md` §4.7). A phone has no sidebar, so with the
 * chat-first flag on the answer to step 1 writes the preset's default stars
 * and the dialog closes. Step 1 stays: it picks the brief's order too. A
 * desktop, or the flag off, keeps step 2 as before.
 */
export function skipsAppsStep(a: { chatFirst: boolean; phone: boolean }): boolean {
  return a.chatFirst && a.phone;
}

// ── Is the dialog open now? ──────────────────────────────────────────────────

let _welcomeOpen = false;
const _welcomeListeners = new Set<() => void>();

/** The dialog reports its open state here, on every change. */
export function setWelcomeOpen(open: boolean): void {
  if (_welcomeOpen === open) return;
  _welcomeOpen = open;
  _welcomeListeners.forEach((l) => l());
}

/** Read once, outside React. */
export function welcomeOpen(): boolean {
  return _welcomeOpen;
}

function subscribeWelcome(listener: () => void): () => void {
  _welcomeListeners.add(listener);
  return () => {
    _welcomeListeners.delete(listener);
  };
}
const getWelcome = () => _welcomeOpen;
const getServerWelcome = () => false;

/** Whether the first sign-in dialog, or the founder's welcome, is open now. */
export function useWelcomeOpen(): boolean {
  return useSyncExternalStore(subscribeWelcome, getWelcome, getServerWelcome);
}

/** Tests only. */
export function __resetWelcomeForTests(): void {
  _welcomeOpen = false;
  _welcomeListeners.clear();
}
