"use client";

/**
 * The member's shell layout, on the client (NS-7, `navigation_shell.md` §8.2).
 *
 * One read, `GET /api/auth/me/shell`, through the one read cache
 * (`dataCache`). The sidebar, All apps, My Day, the command bar and the first
 * sign-in question all read this key, so a pin toggled in one shows in all of
 * them at once.
 *
 * Three rules:
 *
 * - **A read that fails never blanks the sidebar.** The layout falls back to
 *   the role's preset (`shellLayout`), and the first sign-in question does
 *   not open, because "never asked" is a fact only the server knows.
 * - **A write is optimistic.** It shows at once, and it goes back to the last
 *   stored value when the server refuses it.
 * - **The cache is memory only** (`AGENTS.md` rule 9). Nothing here touches
 *   `localStorage`.
 */
import { useAccess } from "@/components/AccessProvider";
import { shouldPollWorkspace } from "@/lib/access";
import { invalidate, peek, put } from "@/lib/dataCache";
import { useCachedResource } from "@/lib/useCachedResource";

import { EMPTY_SHELL, shellLayout, type ShellLayout, type StoredShell } from "./presets";
import { shellNavOn } from "./shellNav";

export const SHELL_PREFS_PATH = "/api/auth/me/shell";

/** Ask the first sign-in question again ("Change my layout", rule 3). */
export const CHANGE_LAYOUT = "shell:change-layout";

export async function readShellPrefs(): Promise<StoredShell> {
  const res = await fetch(SHELL_PREFS_PATH, { cache: "no-store" });
  if (!res.ok) throw new Error(`shell layout ${res.status}`);
  return { ...EMPTY_SHELL, ...((await res.json()) as Partial<StoredShell>) };
}

/**
 * Store `next`, or reset with `null`. It shows at once, and on a refusal the
 * cache goes back to what it held. Throws so the caller can say so.
 */
export async function saveShellPrefs(next: StoredShell | null): Promise<StoredShell> {
  const before = peek<StoredShell>(SHELL_PREFS_PATH)?.data;
  put(SHELL_PREFS_PATH, next ?? EMPTY_SHELL);
  try {
    const res = await fetch(SHELL_PREFS_PATH, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(next),
    });
    if (!res.ok) throw new Error(`shell layout ${res.status}`);
    const saved = { ...EMPTY_SHELL, ...((await res.json()) as Partial<StoredShell>) };
    put(SHELL_PREFS_PATH, saved);
    return saved;
  } catch (err) {
    // Nothing was known before: drop the guess, and read the truth again.
    if (before) put(SHELL_PREFS_PATH, before);
    else invalidate(SHELL_PREFS_PATH);
    throw err;
  }
}

export interface ShellPrefsState {
  /** The stored value, or `undefined` before the first answer. */
  stored: StoredShell | undefined;
  /** What to draw. The role's preset until the server answers. */
  layout: ShellLayout;
  /** True while the first read is out and nothing is cached. */
  loading: boolean;
  /** True when the last read failed. */
  failed: boolean;
  /** False with the shell nav off, or with no workspace yet. */
  enabled: boolean;
}

/**
 * The layout for this member. With the shell nav off it reads nothing and
 * reports `enabled: false`, so every surface draws as before NS-7.
 */
export function useShellPrefs(): ShellPrefsState {
  const { access, loading: accessLoading } = useAccess();
  const enabled = shellNavOn() && shouldPollWorkspace(access, accessLoading);
  const res = useCachedResource<StoredShell>(enabled ? SHELL_PREFS_PATH : null, readShellPrefs, {
    // A layout changes when the member changes it, and every write puts the
    // new value into the cache. So a short revisit never asks again.
    ttl: 60_000,
    revalidateOnFocus: false,
  });
  return {
    stored: res.data,
    layout: shellLayout(res.data, access.roles ?? []),
    loading: enabled && res.data === undefined && res.loading,
    failed: enabled && res.data === undefined && !res.loading && res.error !== null,
    enabled,
  };
}
