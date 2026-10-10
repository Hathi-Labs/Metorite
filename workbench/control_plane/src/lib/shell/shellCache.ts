/**
 * The last layout the server confirmed, kept in this browser per ACCOUNT
 * (NS-7 round 2). Pure, with no React, so `lib/sessions.ts` can clear it on
 * sign-out without importing the hook.
 *
 * Why it exists. The read cache (`dataCache`) lives in memory only, so every
 * full page load started with no layout. The pins then jumped from the team
 * groups into "My apps" when the read landed, and a click in that moment
 * opened the wrong app. This copy paints the last known layout on the first
 * frame, and the read confirms it or replaces it.
 *
 * The rules:
 *
 * - **One key per account,** `cc-shell-prefs:<email>|<organizationId>`, the
 *   scope of `chatScope` in `lib/sessions.ts`. Two members on one browser
 *   never read each other's layout.
 * - **Only a layout the server confirmed is kept.** A write that failed is
 *   never stored here, so a later visit cannot show a layout nobody saved.
 * - **A sign-out clears it** with the chat caches, through the same two
 *   functions (`clearSignedOutAccount`, `clearAccountNamespaces`). An email
 *   must not outlive its sign-out in a key name.
 * - ⚠️ **It never opens the first sign-in question.** A cached "never asked"
 *   may be out of date, so only the server's own answer opens it
 *   (`useShellPrefs().fresh`).
 *
 * Fence: `shellCache.test.ts`.
 */
import { EMPTY_SHELL, type StoredShell } from "./presets";

export const SHELL_CACHE_PREFIX = "cc-shell-prefs:";

/** The key of one account's layout, or null with no account to name. */
export function shellCacheKey(scope: string | null | undefined): string | null {
  if (!scope || !scope.includes("|")) return null;
  return `${SHELL_CACHE_PREFIX}${scope}`;
}

/**
 * A body is a layout only when it says whether the member was asked. Any
 * other body is not an answer, so it must never read as "never asked".
 */
export function asLayout(body: unknown): StoredShell | null {
  if (!body || typeof body !== "object" || Array.isArray(body) || !("answered" in body)) return null;
  return { ...EMPTY_SHELL, ...(body as Partial<StoredShell>) };
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** The account's last confirmed layout, or undefined. Never throws. */
export function readCachedLayout(scope: string | null | undefined): StoredShell | undefined {
  const key = shellCacheKey(scope);
  const store = storage();
  if (!key || !store) return undefined;
  try {
    const raw = store.getItem(key);
    return raw ? (asLayout(JSON.parse(raw)) ?? undefined) : undefined;
  } catch {
    return undefined;
  }
}

/** Keep a layout the server confirmed. Never throws. */
export function writeCachedLayout(scope: string | null | undefined, layout: StoredShell): void {
  const key = shellCacheKey(scope);
  const store = storage();
  if (!key || !store) return;
  try {
    store.setItem(key, JSON.stringify(layout));
  } catch {
    /* storage full or off: the next visit reads the server, as before */
  }
}

/** The key prefix of every organization of one email. */
export function shellAccountPrefix(email: string): string {
  return `${SHELL_CACHE_PREFIX}${email}|`;
}

/** Remove the layouts of these emails, in every organization. Never throws. */
export function clearCachedLayouts(emails: readonly string[]): void {
  const store = storage();
  if (!store) return;
  try {
    const prefixes = [...new Set(emails.flatMap((e) => [e, e.toLowerCase()]))]
      .filter(Boolean)
      .map(shellAccountPrefix);
    const doomed: string[] = [];
    for (let i = 0; i < store.length; i++) {
      const k = store.key(i);
      if (k && prefixes.some((p) => k.startsWith(p))) doomed.push(k);
    }
    for (const k of doomed) store.removeItem(k);
  } catch {
    /* storage unavailable: nothing is stored */
  }
}
