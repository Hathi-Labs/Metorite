/**
 * The last layout the server confirmed, kept in this browser per ACCOUNT
 * (NS-7 round 2). Pure, with no React, so `lib/sessions.ts` can clear it on
 * sign-out without importing the hook.
 *
 * Why it exists. The read cache (`dataCache`) lives in memory only, so every
 * full page load started with no layout. The sidebar then changed shape when
 * the read landed, and a click in that moment opened the wrong app. This
 * copy paints the last known layout on the first frame, and the read
 * confirms it or replaces it. It holds the stars (`pins`) and `known`, and
 * never a tenant row.
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
 * - **The org default has a copy too** (PKG-17b), under
 *   `cc-shell-org-layout:<email>|<organizationId>`, with the same rules. A
 *   404 or an empty answer writes a "no default" marker, and never clears
 *   the key. `orgLayout.ts` reads and writes it.
 *
 * Fence: `shellCache.test.ts` and `orgLayout.test.ts`.
 */
import { EMPTY_SHELL, type StoredShell } from "./presets";

export const SHELL_CACHE_PREFIX = "cc-shell-prefs:";

/** The key of one account's layout, or null with no account to name. */
export function shellCacheKey(scope: string | null | undefined): string | null {
  if (!scope || !scope.includes("|")) return null;
  return `${SHELL_CACHE_PREFIX}${scope}`;
}

/** The keys of a layout. Any other key of an answer is not the layout's. */
const LAYOUT_KEYS = Object.keys(EMPTY_SHELL) as (keyof StoredShell)[];

/**
 * A body is a layout only when it says whether the member was asked. Any
 * other body is not an answer, so it must never read as "never asked".
 *
 * ⚠️ It keeps the layout's own keys only. The answer also carries `guide`
 * (GD-5a), a fact of the server, and a layout save sends the layout back.
 * The gateway refuses an unknown key (`ShellPrefs`, `extra="forbid"`), so a
 * kept `guide` would fail every star save with a 422.
 */
export function asLayout(body: unknown): StoredShell | null {
  if (!body || typeof body !== "object" || Array.isArray(body) || !("answered" in body)) return null;
  const from = body as Record<string, unknown>;
  const out: Record<string, unknown> = { ...EMPTY_SHELL };
  for (const k of LAYOUT_KEYS) if (k in from) out[k] = from[k];
  return out as unknown as StoredShell;
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

// ── The nudge's browser copy (WS-62 CF-2, `chat_first_surface.md` §4.7) ────
// `shellPrefs.ts` reads and writes it. It is here, pure, so a sign-out can
// clear it with the layout's copy.

/**
 * The key prefix of the browser copy, `cc-shell-chat-nudge:<email>|<org>`.
 * ⚠️ It keeps the shell prefix: a key that starts with "cc-" and then "chat"
 * reads as a chat cache key with no namespace (`railSessions.test.ts`).
 */
export const NUDGE_PREFIX = "cc-shell-chat-nudge:";

/** The key of one account's copy, or null with no account to name. */
export function nudgeKey(scope: string | null | undefined): string | null {
  if (!scope || !scope.includes("|")) return null;
  return `${NUDGE_PREFIX}${scope}`;
}

/** Whether this browser already knows the member wrote to the assistant. */
export function readNudgeRetired(scope: string | null | undefined): boolean {
  const key = nudgeKey(scope);
  const store = storage();
  if (!key || !store) return false;
  try {
    return store.getItem(key) === "1";
  } catch {
    return false;
  }
}

/** Keep the fact in this browser. Never throws. */
export function writeNudgeRetired(scope: string | null | undefined): void {
  const key = nudgeKey(scope);
  const store = storage();
  if (!key || !store) return;
  try {
    store.setItem(key, "1");
  } catch {
    /* storage off: the server's copy still holds */
  }
}

/** Remove the copies of these emails, in every organization. Never throws. */
export function clearNudgeMarks(emails: readonly string[]): void {
  clearAccountKeys(NUDGE_PREFIX, emails);
}

/** The key prefix of every organization of one email. */
export function shellAccountPrefix(email: string): string {
  return `${SHELL_CACHE_PREFIX}${email}|`;
}

/**
 * Remove the layouts of these emails, in every organization. Never throws.
 * It also removes the copies of the org default (PKG-17b), so the two copies
 * share one sign-out rule.
 */
export function clearCachedLayouts(emails: readonly string[]): void {
  clearAccountKeys(SHELL_CACHE_PREFIX, emails);
  clearAccountKeys(ORG_LAYOUT_PREFIX, emails);
}

// ── The org default's browser copy (PKG-17b, `package_host.md` §9.1) ───────
// The same rules as the layout's copy above. One key per account, and only
// an answer of the server is kept. `clearCachedLayouts` clears it on a
// sign-out. The copy is raw JSON here. `orgLayout.ts` gives it its shape and
// parses it, so a bad copy reads as no copy.

/**
 * The key prefix of the copy, `cc-shell-org-layout:<email>|<org>`.
 * ⚠️ It keeps the shell prefix, as `NUDGE_PREFIX` does.
 */
export const ORG_LAYOUT_PREFIX = "cc-shell-org-layout:";

/** The key of one account's copy, or null with no account to name. */
export function orgLayoutCacheKey(scope: string | null | undefined): string | null {
  if (!scope || !scope.includes("|")) return null;
  return `${ORG_LAYOUT_PREFIX}${scope}`;
}

/** The account's stored copy, not yet parsed, or undefined. Never throws. */
export function readCachedOrgLayoutRaw(scope: string | null | undefined): unknown {
  const key = orgLayoutCacheKey(scope);
  const store = storage();
  if (!key || !store) return undefined;
  try {
    const raw = store.getItem(key);
    return raw ? (JSON.parse(raw) as unknown) : undefined;
  } catch {
    return undefined;
  }
}

/** Keep an org default that the server answered. Never throws. */
export function writeCachedOrgLayout(scope: string | null | undefined, layout: unknown): void {
  const key = orgLayoutCacheKey(scope);
  const store = storage();
  if (!key || !store) return;
  try {
    store.setItem(key, JSON.stringify(layout));
  } catch {
    /* storage full or off: the next visit reads the server, as before */
  }
}

/**
 * Remove every `<prefix><email>|<org>` key of these emails. Never throws.
 * The shell layout and the sidebar folds (`sidebarFolds.ts`) share it.
 */
export function clearAccountKeys(prefix: string, emails: readonly string[]): void {
  const store = storage();
  if (!store) return;
  try {
    const prefixes = [...new Set(emails.flatMap((e) => [e, e.toLowerCase()]))]
      .filter(Boolean)
      .map((e) => `${prefix}${e}|`);
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
