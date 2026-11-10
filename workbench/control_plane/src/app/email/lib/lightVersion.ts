"use client";

/**
 * "View light version" — the member's choice to see one email as sent, on its
 * light sheet, while the app is in dark mode (owner, 2026-10-10).
 *
 * It is a convenience of this viewer on this device, so it lives in
 * `localStorage`, one list of message ids under one key. It holds ids only,
 * never a subject or a body, and it keeps the newest {@link MAX_IDS}. Each
 * read and each write is in a try block: a private window or a full store
 * must not break the reading pane.
 *
 * Fence: `bodyLook.test.ts` ("the light version").
 */

import { useSyncExternalStore } from "react";

export const LIGHT_VERSION_KEY = "metorite.email.lightVersion";

/** How many ids the list keeps. The oldest goes first. */
export const MAX_IDS = 200;

/** The list after a toggle of `id`: on adds it at the end, off removes it. */
export function toggleId(list: readonly string[], id: string): string[] {
  if (list.includes(id)) return list.filter((x) => x !== id);
  return [...list, id].slice(-MAX_IDS);
}

/** The ids in a stored value. Anything that is not a list of strings is none. */
export function parseIds(raw: string | null): string[] {
  if (!raw) return [];
  try {
    const value: unknown = JSON.parse(raw);
    return Array.isArray(value) ? value.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

const EMPTY: ReadonlySet<string> = new Set();
let current: ReadonlySet<string> | null = null;
const listeners = new Set<() => void>();

function load(): ReadonlySet<string> {
  if (current) return current;
  try {
    current = new Set(parseIds(window.localStorage.getItem(LIGHT_VERSION_KEY)));
  } catch {
    current = new Set();
  }
  return current;
}

/** Turn the light version of one message on or off, and tell each reader. */
export function toggleLightVersion(id: string): void {
  const next = toggleId([...load()], id);
  current = new Set(next);
  try {
    window.localStorage.setItem(LIGHT_VERSION_KEY, JSON.stringify(next));
  } catch {
    /* the choice holds for this page only */
  }
  listeners.forEach((fn) => fn());
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** The ids whose light version is on. Every reader sees the same set. */
export function useLightVersions(): ReadonlySet<string> {
  return useSyncExternalStore(subscribe, load, () => EMPTY);
}
