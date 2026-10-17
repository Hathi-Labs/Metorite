"use client";

/**
 * Dismissed tool-card registry — lets the user close an AG-UI tool card and have
 * it stay closed across reloads, without a backend round-trip.
 *
 * Tool-card events are the source-of-truth server-side (the tool_events JSONB
 * column), so we don't mutate them; instead we keep a per-browser set of
 * dismissed tool-call ids in localStorage and filter the cards at render time.
 * The ids are the stable tool-call ids persisted in tool_events, so a dismissal
 * survives a refresh and re-applies when the conversation reloads.
 *
 * The same set holds the info-only notices of the chat (owner report,
 * 2026-10-17, `isInfoOnlyNotice` in `lib/runErrors.ts`). A notice key is
 * `noticeDismissKey(sessionId, messageId)`, so it never meets a tool-call id.
 */
import { useSyncExternalStore } from "react";

const LS_KEY = "cc.chat.dismissedToolCards";

function load(): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(LS_KEY);
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}

let _ids: Set<string> = load();
const _subs = new Set<() => void>();

function persist(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(LS_KEY, JSON.stringify([..._ids]));
  } catch {
    /* storage disabled (private mode) — dismissals just won't persist */
  }
}

function emit(): void {
  _subs.forEach((f) => f());
}

/** Dismiss a tool card by its tool-call id (idempotent; persists + notifies). */
export function dismissToolCard(id: string): void {
  if (!id || _ids.has(id)) return;
  _ids = new Set(_ids);
  _ids.add(id);
  persist();
  emit();
}

/** The key of one chat notice in this set: its session, then its message id. */
export function noticeDismissKey(sessionId: string, messageId: string): string {
  return `notice:${sessionId}:${messageId}`;
}

/** Un-dismiss (used by an "undo"/"show dismissed" affordance if added later). */
export function restoreToolCard(id: string): void {
  if (!_ids.has(id)) return;
  _ids = new Set(_ids);
  _ids.delete(id);
  persist();
  emit();
}

export function isToolCardDismissed(id: string): boolean {
  return _ids.has(id);
}

function subscribe(cb: () => void): () => void {
  _subs.add(cb);
  return () => {
    _subs.delete(cb);
  };
}

/** React hook: the current dismissed-id set (re-renders the consumer on change). */
export function useDismissedToolCards(): Set<string> {
  return useSyncExternalStore(
    subscribe,
    () => _ids,
    () => _ids,
  );
}
